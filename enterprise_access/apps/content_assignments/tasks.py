"""
Tasks for content_assignments app.
"""

import logging

from celery import shared_task
from django.apps import apps
from django.conf import settings
from django.utils import timezone

from enterprise_access.apps.api_client.braze_client import BrazeApiClient
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.content_assignments.constants import AssignmentActionErrors, AssignmentActions
from enterprise_access.apps.content_assignments.content_metadata_api import (
    get_card_image_url,
    get_content_metadata_for_assignments,
    get_course_partners,
    get_human_readable_date
)
from enterprise_access.apps.content_assignments.models import LearnerContentAssignmentAction
from enterprise_access.tasks import LoggedTaskWithRetry

from .constants import LearnerContentAssignmentStateChoices
from .exceptions import MissingContentAssignment
from .utils import format_traceback

logger = logging.getLogger(__name__)


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
        'learner_portal_link'
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
        recipient = self.braze_client.create_recipient(
            user_email=self.assignment.learner_email,
            lms_user_id=self.assignment.lms_user_id,
        )
        response = self.braze_client.send_campaign_message(
            campaign_identifier,
            recipients=[recipient],
            trigger_properties=braze_trigger_properties,
        )
        return response

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
            self._course_metadata = get_content_metadata_for_assignments(
                self.policy.catalog_uuid, self.assignment.assignment_configuration
            )
        return self._course_metadata

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

    def get_enrollment_deadline(self):
        return get_human_readable_date(
            self.course_metadata.get('normalized_metadata', {}).get('enroll_by_date')
        )

    def get_start_date(self):
        return get_human_readable_date(
            self.course_metadata.get('normalized_metadata', {}).get('start_date')
        )

    def get_course_partner(self):
        return get_course_partners(self.course_metadata)

    def get_course_card_image(self):
        return get_card_image_url(self.course_metadata)

    def get_learner_portal_link(self):
        slug = self.customer_data["slug"]
        return f'{settings.ENTERPRISE_LEARNER_PORTAL_URL}/{slug}'


class CreatePendingEnterpriseLearnerForAssignmentTaskBase(LoggedTaskWithRetry):  # pylint: disable=abstract-method
    """
    Base class for the create_pending_enterprise_learner_for_assignment task.

    Provides a place to define retry failure handling logic.
    """

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """
        If the task fails for any reason (whether or not retries were involved), set the assignment state to errored.

        Function signature documented at: https://docs.celeryq.dev/en/stable/userguide/tasks.html#on_failure
        """
        logger.error(f'"{task_id}" failed: "{exc}"')
        learner_content_assignment_uuid = args[0]
        learner_content_assignment_model = apps.get_model('content_assignments.LearnerContentAssignment')

        try:
            assignment = learner_content_assignment_model.objects.get(uuid=learner_content_assignment_uuid)
            assignment.state = LearnerContentAssignmentStateChoices.ERRORED
            assignment.save()
            assignment.add_errored_linked_action(exc)
            if self.request.retries == settings.TASK_MAX_RETRIES:
                # The failure resulted from too many retries.  This fact would be a useful thing to record in a "reason"
                # field on the assignment if one existed.
                logger.error(
                    'The task failure resulted from exceeding the locally defined max number of retries '
                    '(settings.TASK_MAX_RETRIES).'
                )
        except learner_content_assignment_model.DoesNotExist:
            logger.error(f'LearnerContentAssignment not found with UUID: {learner_content_assignment_uuid}')


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
    learner_content_assignment_model = apps.get_model('content_assignments.LearnerContentAssignment')
    assignment = learner_content_assignment_model.objects.get(uuid=learner_content_assignment_uuid)
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


@shared_task(base=LoggedTaskWithRetry)
def send_cancel_email_for_pending_assignment(cancelled_assignment_uuid):
    """
    Send email via braze for cancelling pending assignment

    Args:
        cancelled_assignment: (string) the cancelled assignment uuid
    """
    learner_content_assignment_model = apps.get_model('content_assignments.LearnerContentAssignment')

    try:
        assignment = learner_content_assignment_model.objects.get(uuid=cancelled_assignment_uuid)
    except learner_content_assignment_model.DoesNotExist:
        logger.warning(f'request with uuid: {cancelled_assignment_uuid} does not exist.')
        raise

    learner_content_assignment_action = LearnerContentAssignmentAction(
        assignment=assignment, action_type=AssignmentActions.CANCELLED_NOTIFICATION
    )

    try:
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
        logger.info(f'Sent braze campaign cancelled uuid={campaign_uuid} message for assignment {assignment}')
        learner_content_assignment_action.completed_at = timezone.now()
        learner_content_assignment_action.save()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error(f"Unable to send assignment cancellation for {assignment.uuid} due to exception: {exc}")
        learner_content_assignment_action.error_reason = AssignmentActionErrors.EMAIL_ERROR
        learner_content_assignment_action.traceback = format_traceback(exc)
        learner_content_assignment_action.save()
        assignment.state = LearnerContentAssignmentStateChoices.ERRORED
        assignment.full_clean()
        assignment.save()


@shared_task(base=LoggedTaskWithRetry)
def send_reminder_email_for_pending_assignment(assignment_uuid):
    """
    Send email via braze for reminding users of their pending assignment
    Args:
        assignment_uuid: (string) the subsidy request uuid
    """
    learner_content_assignment_model = apps.get_model('content_assignments.LearnerContentAssignment')

    try:
        assignment = learner_content_assignment_model.objects.get(uuid=assignment_uuid)
    except learner_content_assignment_model.DoesNotExist:
        logger.warning(f'request with uuid: {assignment_uuid} does not exist.')
        raise

    learner_content_assignment_action = LearnerContentAssignmentAction(
        assignment=assignment, action_type=AssignmentActions.REMINDED,
    )

    try:
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
        )
        campaign_uuid = settings.BRAZE_ASSIGNMENT_REMINDER_NOTIFICATION_CAMPAIGN
        if assignment.lms_user_id is not None:
            campaign_uuid = settings.BRAZE_ASSIGNMENT_REMINDER_POST_LOGISTRATION_NOTIFICATION_CAMPAIGN

        campaign_sender.send_campaign_message(
            braze_trigger_properties,
            campaign_uuid,
        )
        logger.info(f'Sent braze campaign reminder uuid={campaign_uuid} message for assignment {assignment}')
        learner_content_assignment_action.completed_at = timezone.now()
        learner_content_assignment_action.save()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error(f"Unable to send assignment reminder for {assignment.uuid} due to exception: {exc}")
        learner_content_assignment_action.error_reason = AssignmentActionErrors.EMAIL_ERROR
        learner_content_assignment_action.traceback = format_traceback(exc)
        learner_content_assignment_action.save()
        assignment.state = LearnerContentAssignmentStateChoices.ERRORED
        assignment.full_clean()
        assignment.save()


@shared_task(base=LoggedTaskWithRetry)
def send_email_for_new_assignment(new_assignment_uuid):
    """
    Send email via braze for new assignment

    Args:
        new_assignment_uuid: (string) the new assignment uuid
    """
    learner_content_assignment_model = apps.get_model('content_assignments.LearnerContentAssignment')

    try:
        assignment = learner_content_assignment_model.objects.get(uuid=new_assignment_uuid)
    except learner_content_assignment_model.DoesNotExist as exc:
        logger.warning(f'request with uuid: {new_assignment_uuid} does not exist.')
        raise MissingContentAssignment(
            f'No assignment was found for assignment_uuid={new_assignment_uuid}.'
        ) from exc

    learner_content_assignment_action = LearnerContentAssignmentAction(
        assignment=assignment, action_type=AssignmentActions.NOTIFIED
    )

    try:
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
        )
        campaign_uuid = settings.BRAZE_ASSIGNMENT_NOTIFICATION_CAMPAIGN
        campaign_sender.send_campaign_message(
            braze_trigger_properties,
            campaign_uuid,
        )
        logger.info(f'Sent braze campaign notification uuid={campaign_uuid} message for assignment {assignment}')
        learner_content_assignment_action.completed_at = timezone.now()
        learner_content_assignment_action.save()
    except Exception as exc:
        logger.error(f"Unable to send assignment notification for {assignment.uuid} due to exception: {exc}")
        learner_content_assignment_action.error_reason = AssignmentActionErrors.EMAIL_ERROR
        learner_content_assignment_action.traceback = format_traceback(exc)
        learner_content_assignment_action.save()
        raise


@shared_task(base=LoggedTaskWithRetry)
def send_assignment_automatically_expired_email(expired_assignment_uuid):
    """
    Send email via braze for automatically expired assignment
    Args:
        expired_assignment_uuid: (string) expired assignment uuid
    """
    learner_content_assignment_model = apps.get_model('content_assignments.LearnerContentAssignment')

    try:
        assignment = learner_content_assignment_model.objects.get(uuid=expired_assignment_uuid)
    except learner_content_assignment_model.DoesNotExist:
        logger.warning(f'Request with uuid: {expired_assignment_uuid} does not exist.')
        raise

    learner_content_assignment_action = LearnerContentAssignmentAction(
        assignment=assignment, action_type=AssignmentActions.AUTOMATIC_CANCELLATION_NOTIFICATION
    )

    try:
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
        logger.info(f'Sent braze campaign expiration uuid={campaign_uuid} message for assignment {assignment}')
        learner_content_assignment_action.completed_at = timezone.now()
        learner_content_assignment_action.save()
    except Exception as exc:
        logger.error(f"Unable to send assignment expiration for {assignment.uuid} due to exception: {exc}")
        learner_content_assignment_action.error_reason = AssignmentActionErrors.EMAIL_ERROR
        learner_content_assignment_action.traceback = format_traceback(exc)
        learner_content_assignment_action.save()
        raise
