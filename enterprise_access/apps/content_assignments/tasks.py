"""
Tasks for content_assignments app.
"""

import logging
from datetime import datetime

from braze.exceptions import BrazeBadRequestError
from celery import shared_task
from django.apps import apps
from django.conf import settings
from pytz import UTC

from enterprise_access.apps.api_client.braze_client import ENTERPRISE_BRAZE_ALIAS_LABEL, BrazeApiClient
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.content_assignments.content_metadata_api import (
    format_datetime_obj,
    get_card_image_url,
    get_content_metadata_for_assignments,
    get_course_partners,
    get_human_readable_date,
    parse_datetime_string
)
from enterprise_access.tasks import LoggedTaskWithRetry

from .constants import LearnerContentAssignmentStateChoices

logger = logging.getLogger(__name__)


def _get_assignment_or_raise(assignment_uuid):
    """
    Returns a ``LearnerContentAssignment`` instance with the given uuid, or raises
    if no such record exists.
    """
    learner_content_assignment_model = apps.get_model('content_assignments.LearnerContentAssignment')

    try:
        return learner_content_assignment_model.objects.get(uuid=assignment_uuid)
    except learner_content_assignment_model.DoesNotExist:
        logger.warning(f'Request with uuid: {assignment_uuid} does not exist.')
        raise


class BrazeCampaignSender:
    """
    Class to help standardize the allowed keys and methods of conversion to values
    for Braze API-triggered campaign properties.  Use as follows:

    sender = BrazeCampaignSender(learner_content_assignment_record)
    props = sender.get_properties(course_title, course_partner, ...) # any subset of ALLOWED_TRIGGER_PROPERTIES
    sender.send_campaign_message(props, campaign_identifier)
    """
    ALLOWED_TRIGGER_PROPERTIES = {
        'contact_admin_link',
        'organization',
        'course_title',
        'enrollment_deadline',
        'start_date',
        'course_partner',
        'course_card_image',
        'learner_portal_link',
        'action_required_by',
    }

    def __init__(self, assignment):
        self.assignment = assignment
        self.enterprise_customer_uuid = assignment.assignment_configuration.enterprise_customer_uuid

        subsidy_policy_model = apps.get_model('subsidy_access_policy.SubsidyAccessPolicy')
        try:
            self.policy = subsidy_policy_model.objects.get(
                assignment_configuration=assignment.assignment_configuration
            )
        except subsidy_policy_model.DoesNotExist:
            logger.warning(f'policy with assignment config: {assignment.assignment_configuration} does not exist.')
            raise

        self.braze_client = BrazeApiClient()
        self.lms_client = LmsApiClient()
        self._customer_data = None
        self._course_metadata = None

    def send_campaign_message(self, braze_trigger_properties, campaign_identifier):
        """
        Creates a recipient and sends a braze campaign message.
        """
        if not campaign_identifier:
            raise Exception('campaign_identifiers must be non-null/empty!')

        if self.assignment.lms_user_id is None:
            recipient = self.braze_client.create_recipient_no_external_id(
                self.assignment.learner_email,
            )
            # We need an alias record to exist in Braze before
            # sending to any previously-unidentified users.
            self.braze_client.create_braze_alias(
                [self.assignment.learner_email],
                ENTERPRISE_BRAZE_ALIAS_LABEL,
            )
        else:
            recipient = self.braze_client.create_recipient(
                user_email=self.assignment.learner_email,
                lms_user_id=self.assignment.lms_user_id,
            )

        try:
            response = self.braze_client.send_campaign_message(
                campaign_identifier,
                recipients=[recipient],
                trigger_properties=braze_trigger_properties,
            )
            log_message = (
                'Successfully sent Braze campaign message for assignment %s, recipient %s, '
                'campaign %s, with trigger properties %s',
            )
            logger.info(
                log_message,
                self.assignment.uuid,
                recipient,
                campaign_identifier,
                braze_trigger_properties,
            )
            return response
        except BrazeBadRequestError as exc:
            # hack into the underlying HTTPError to understand why the request was bad
            exc_response_content = ''
            if exc.__cause__ and hasattr(exc.__cause__, 'response'):
                exc_response_content = exc.__cause__.response.content.decode()
            logger.exception(
                f'Braze request error {exc_response_content} while sending campaign {campaign_identifier}'
            )
            raise

    @property
    def customer_data(self):
        """
        Returns memoized customer metadata dictionary.
        """
        if not self._customer_data:
            self._customer_data = self.lms_client.get_enterprise_customer_data(self.enterprise_customer_uuid)
        return self._customer_data

    @property
    def course_metadata(self):
        """
        Returns memoized course metadata dictionary.
        """
        if not self._course_metadata:
            metadata_by_key = get_content_metadata_for_assignments(
                self.policy.catalog_uuid, [self.assignment]
            )
            self._course_metadata = metadata_by_key.get(self.assignment.content_key)
            if not self._course_metadata:
                msg = (
                    f'Could not fetch metadata for assignment {self.assignment.uuid}, '
                    f'content_key {self.assignment.content_key}'
                )
                raise Exception(msg)
        return self._course_metadata

    @property
    def subsidy_record(self):
        """
        Returns a cached subsidy record for the policy related to this assignment.
        """
        # send an extra cache arg so that cache keys are scoped
        # to the context of braze campaign-sending.
        return self.policy.subsidy_record_from_tiered_cache('braze_campaign_sender')

    def get_properties(self, *property_names):
        """
        Looks for instance methods on ``self`` that match "get_{property_name}"
        for each provided property name, then evaluates those methods and
        stores the result as the value in a dict keyed by property names.
        This dict is then returned.
        """
        properties = {}
        for property_name in property_names:
            if property_name not in self.ALLOWED_TRIGGER_PROPERTIES:
                logger.warning(f'{property_name} is not an allowed braze trigger property')
                continue
            get_property_value_func = getattr(self, f'get_{property_name}')
            properties[property_name] = get_property_value_func()
        return properties

    def get_contact_admin_link(self):
        admin_emails = [user['email'] for user in self.customer_data['admin_users']]
        return self.braze_client.generate_mailto_link(admin_emails)

    def get_organization(self):
        return self.customer_data.get('name')

    def get_course_title(self):
        return self.assignment.content_title

    def _enrollment_deadline_raw(self):
        return self.course_metadata.get('normalized_metadata', {}).get('enroll_by_date')

    def get_enrollment_deadline(self):
        return get_human_readable_date(self._enrollment_deadline_raw())

    def get_start_date(self):
        return get_human_readable_date(
            self.course_metadata.get('normalized_metadata', {}).get('start_date')
        )

    def get_action_required_by(self):
        """
        Returns the minimum of this assignment's auto-cancellation date,
        the content's enrollment deadline, and the related policy's expiration datetime.
        """
        if subsidy_record := self.subsidy_record:
            subsidy_expiration = parse_datetime_string(subsidy_record.get('expiration_datetime')) or datetime.max
            subsidy_expiration = subsidy_expiration.replace(tzinfo=UTC)
        else:
            subsidy_expiration = datetime.max.replace(tzinfo=UTC)

        enrollment_deadline = parse_datetime_string(self._enrollment_deadline_raw()) or datetime.max
        enrollment_deadline = enrollment_deadline.replace(tzinfo=UTC)

        auto_cancellation_date = self.assignment.get_auto_expiration_date() or datetime.max
        auto_cancellation_date = auto_cancellation_date.replace(tzinfo=UTC)

        message = (
            'action_required_by assignment=%s: subsidy_expiration=%s, enrollment_deadline=%s, '
            'auto_cancellation_date=%s'
        )
        logger.info(
            message,
            self.assignment.uuid,
            subsidy_expiration,
            enrollment_deadline,
            auto_cancellation_date,
        )
        action_required_by = min(subsidy_expiration, enrollment_deadline, auto_cancellation_date)
        return format_datetime_obj(action_required_by)

    def get_course_partner(self):
        return get_course_partners(self.course_metadata)

    def get_course_card_image(self):
        """
        Fetches the ``course_card_image`` property for this object's assignment and course.
        """
        image_url = get_card_image_url(self.course_metadata)
        logger.warning(
            'Found course_card_image %s for assignment %s with metadata %s',
            image_url,
            self.assignment.uuid,
            self.course_metadata,
        )
        return image_url

    def get_learner_portal_link(self):
        slug = self.customer_data["slug"]
        return f'{settings.ENTERPRISE_LEARNER_PORTAL_URL}/{slug}'


class BaseAssignmentRetryAndErrorActionTask(LoggedTaskWithRetry):
    """
    Base class that sets an errored state and action on an assignment.
    Provides a place to define retry failure handling logic.  This helps ensure
    that only *one* error action record gets written when a task is retried
    multiple times.
    """
    def add_errored_action(self, assignment, exc):
        """
        Do something here to add a related action with error info.
        """
        raise NotImplementedError

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """
        If the task fails for any reason (whether or not retries were involved), set the assignment state to errored.

        Function signature documented at: https://docs.celeryq.dev/en/stable/userguide/tasks.html#on_failure
        """
        logger.error(
            f'Assignment task {self.name} failed. task id: {task_id}, '
            f'exception: {exc}, task args/assignment-uuid: {args}'
        )

        assignment = _get_assignment_or_raise(args[0])

        assignment.state = LearnerContentAssignmentStateChoices.ERRORED
        assignment.save()
        self.add_errored_action(assignment, exc)
        if self.request.retries == settings.TASK_MAX_RETRIES:
            # The failure resulted from too many retries.  This fact would be a useful thing to record in a "reason"
            # field on the assignment if one existed.
            logger.error(
                'The task failure resulted from exceeding the locally defined max number of retries '
                '(settings.TASK_MAX_RETRIES).'
            )


# pylint: disable=abstract-method
class CreatePendingEnterpriseLearnerForAssignmentTaskBase(BaseAssignmentRetryAndErrorActionTask):
    """
    Base class for the create_pending_enterprise_learner_for_assignment task.
    """
    def add_errored_action(self, assignment, exc):
        assignment.add_errored_linked_action(exc)


@shared_task(base=CreatePendingEnterpriseLearnerForAssignmentTaskBase)
def create_pending_enterprise_learner_for_assignment_task(learner_content_assignment_uuid):
    """
    Create a pending enterprise learner for the email+content associated with the given LearnerContentAssignment.

    Args:
        learner_content_assignment_uuid (str):
            UUID of the LearnerContentAssignment object from which to obtain the learner email and enterprise customer.

    Raises:
        HTTPError if LMS API call fails with an HTTPError.
    """
    assignment = _get_assignment_or_raise(learner_content_assignment_uuid)
    enterprise_customer_uuid = assignment.assignment_configuration.enterprise_customer_uuid

    lms_client = LmsApiClient()
    # Could raise HTTPError and trigger task retry.  Intentionally ignoring response since success should just not throw
    # an exception.  Two possible success statuses are 201 (created) and 200 (found), but there's no reason to
    # distinguish them for the purpose of this task.
    lms_client.create_pending_enterprise_users(enterprise_customer_uuid, [assignment.learner_email])

    assignment.add_successful_linked_action()
    logger.info(
        f'Successfully linked learner to enterprise {enterprise_customer_uuid} '
        f'for assignment {assignment.uuid}'
    )


# pylint: disable=abstract-method
class SendCancelEmailTask(BaseAssignmentRetryAndErrorActionTask):
    """
    Base class for the ``send_cancel_email_for_pending_assignment`` task.
    """
    def add_errored_action(self, assignment, exc):
        assignment.add_errored_cancel_action(exc)


@shared_task(base=SendCancelEmailTask)
def send_cancel_email_for_pending_assignment(cancelled_assignment_uuid):
    """
    Send email via braze for cancelling pending assignment

    Args:
        cancelled_assignment: (string) the cancelled assignment uuid
    """
    assignment = _get_assignment_or_raise(cancelled_assignment_uuid)

    campaign_sender = BrazeCampaignSender(assignment)
    braze_trigger_properties = campaign_sender.get_properties(
        'contact_admin_link',
        'organization',
        'course_title',
    )
    campaign_uuid = settings.BRAZE_ASSIGNMENT_CANCELLED_NOTIFICATION_CAMPAIGN
    campaign_sender.send_campaign_message(
        braze_trigger_properties,
        campaign_uuid,
    )
    assignment.add_successful_cancel_action()
    logger.info(f'Sent braze campaign cancelled uuid={campaign_uuid} message for assignment {assignment}')


# pylint: disable=abstract-method
class SendExecutiveEducationNudgeTask(BaseAssignmentRetryAndErrorActionTask):
    """
    Base class for the ``send_exec_ed_enrollment_warmer`` task.
    """
    def add_errored_action(self, assignment, exc):
        assignment.add_errored_reminded_action(exc)


@shared_task(base=SendExecutiveEducationNudgeTask)
def send_exec_ed_enrollment_warmer(assignment_uuid, days_before_course_start_date):
    """
    Send email via braze for nudging users of their pending accepted assignments
    Args:
        assignment_uuid: (string) the subsidy request uuid
    """
    assignment = _get_assignment_or_raise(assignment_uuid)

    campaign_sender = BrazeCampaignSender(assignment)
    braze_trigger_properties = campaign_sender.get_properties(
        'contact_admin_link',
        'organization',
        'course_title',
        'enrollment_deadline',
        'start_date',
        'course_partner',
        'course_card_image',
        'learner_portal_link',
        'action_required_by',
    )

    braze_trigger_properties['days_before_course_start_date'] = days_before_course_start_date

    campaign_uuid = settings.BRAZE_ASSIGNMENT_NUDGE_EXEC_ED_ACCEPTED_ASSIGNMENT_CAMPAIGN

    campaign_sender.send_campaign_message(
        braze_trigger_properties,
        campaign_uuid,
    )
    logger.info(
        f'Sent braze campaign nudge reminder at '
        f'days_before_course_start_date={days_before_course_start_date} '
        f'uuid={campaign_uuid} message for assignment {assignment}'
    )


# pylint: disable=abstract-method
class SendReminderEmailTask(BaseAssignmentRetryAndErrorActionTask):
    """
    Base class for the ``send_reminder_email_for_pending_assignment`` task.
    """
    def add_errored_action(self, assignment, exc):
        assignment.add_errored_reminded_action(exc)


@shared_task(base=SendReminderEmailTask)
def send_reminder_email_for_pending_assignment(assignment_uuid):
    """
    Send email via braze for reminding users of their pending assignment
    Args:
        assignment_uuid: (string) the subsidy request uuid
    """
    assignment = _get_assignment_or_raise(assignment_uuid)

    campaign_sender = BrazeCampaignSender(assignment)
    braze_trigger_properties = campaign_sender.get_properties(
        'contact_admin_link',
        'organization',
        'course_title',
        'enrollment_deadline',
        'start_date',
        'course_partner',
        'course_card_image',
        'learner_portal_link',
        'action_required_by',
    )
    campaign_uuid = settings.BRAZE_ASSIGNMENT_REMINDER_NOTIFICATION_CAMPAIGN
    if assignment.lms_user_id is not None:
        campaign_uuid = settings.BRAZE_ASSIGNMENT_REMINDER_POST_LOGISTRATION_NOTIFICATION_CAMPAIGN

    campaign_sender.send_campaign_message(
        braze_trigger_properties,
        campaign_uuid,
    )
    assignment.add_successful_reminded_action()
    logger.info(f'Sent braze campaign reminder uuid={campaign_uuid} message for assignment {assignment}')


# pylint: disable=abstract-method
class SendNotificationEmailTask(BaseAssignmentRetryAndErrorActionTask):
    """
    Base class for the ``send_email_for_new_assignment`` task.
    """
    def add_errored_action(self, assignment, exc):
        assignment.add_errored_notified_action(exc)


@shared_task(base=SendNotificationEmailTask)
def send_email_for_new_assignment(new_assignment_uuid):
    """
    Send email via braze for new assignment

    Args:
        new_assignment_uuid: (string) the new assignment uuid
    """
    assignment = _get_assignment_or_raise(new_assignment_uuid)

    campaign_sender = BrazeCampaignSender(assignment)
    braze_trigger_properties = campaign_sender.get_properties(
        'contact_admin_link',
        'organization',
        'course_title',
        'enrollment_deadline',
        'start_date',
        'course_partner',
        'course_card_image',
        'learner_portal_link',
        'action_required_by',
    )
    campaign_uuid = settings.BRAZE_ASSIGNMENT_NOTIFICATION_CAMPAIGN
    campaign_sender.send_campaign_message(
        braze_trigger_properties,
        campaign_uuid,
    )
    assignment.add_successful_notified_action()
    logger.info(f'Sent braze campaign notification uuid={campaign_uuid} message for assignment {assignment}')


# pylint: disable=abstract-method
class SendExpirationEmailTask(BaseAssignmentRetryAndErrorActionTask):
    """
    Base class for the ``send_assignment_automatically_expired_email`` task.
    """
    def add_errored_action(self, assignment, exc):
        assignment.add_errored_expiration_action(exc)


@shared_task(base=SendExpirationEmailTask)
def send_assignment_automatically_expired_email(expired_assignment_uuid):
    """
    Send email via braze for automatically expired assignment
    Args:
        expired_assignment_uuid: (string) expired assignment uuid
    """
    assignment = _get_assignment_or_raise(expired_assignment_uuid)

    campaign_sender = BrazeCampaignSender(assignment)
    braze_trigger_properties = campaign_sender.get_properties(
        'contact_admin_link',
        'organization',
        'course_title',
    )
    campaign_uuid = settings.BRAZE_ASSIGNMENT_AUTOMATIC_CANCELLATION_NOTIFICATION_CAMPAIGN
    campaign_sender.send_campaign_message(
        braze_trigger_properties,
        campaign_uuid,
    )
    assignment.add_successful_expiration_action()
    logger.info(f'Sent braze campaign expiration uuid={campaign_uuid} message for assignment {assignment}')
