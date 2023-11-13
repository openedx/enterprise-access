"""
Tasks for content_assignments app.
"""

import logging
from datetime import datetime

from celery import shared_task
from django.apps import apps
from django.conf import settings

from enterprise_access.apps.api_client.braze_client import BrazeApiClient
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.content_assignments.constants import AssignmentActionErrors, AssignmentActions
from enterprise_access.apps.content_assignments.models import LearnerContentAssignmentAction
from enterprise_access.tasks import LoggedTaskWithRetry

from .constants import LearnerContentAssignmentStateChoices

logger = logging.getLogger(__name__)


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
            if self.request.retries == settings.TASK_MAX_RETRIES:
                # The failure resulted from too many retries.  This fact would be a useful thing to record in a "reason"
                # field on the assignment if one existed.
                logger.error(
                    'The task failure resulted from exceeding the locally defined max number of retries '
                    '(settings.TASK_MAX_RETRIES).'
                )
        except assignment.DoesNotExist:
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
        return
    learner_content_assignment_action = LearnerContentAssignmentAction(
        assignment=assignment, action_type=AssignmentActions.CANCELLED_NOTIFICATION
    )

    braze_trigger_properties = {}
    braze_client_instance = BrazeApiClient()
    lms_client = LmsApiClient()
    enterprise_customer_uuid = assignment.assignment_configuration.enterprise_customer_uuid
    enterprise_customer_data = lms_client.get_enterprise_customer_data(enterprise_customer_uuid)
    lms_user_id = assignment.lms_user_id
    admin_emails = [user['email'] for user in enterprise_customer_data['admin_users']]
    braze_trigger_properties['contact_admin_link'] = braze_client_instance.generate_mailto_link(admin_emails)

    try:
        recipient = braze_client_instance.create_recipient(
            user_email=assignment.learner_email,
            lms_user_id=assignment.lms_user_id
        )
        braze_trigger_properties["organization"] = enterprise_customer_data['name']
        braze_trigger_properties["course_name"] = assignment.content_title
        braze_client_instance.send_campaign_message(
            settings.BRAZE_ASSIGNMENT_CANCELLED_NOTIFICATION_CAMPAIGN,
            recipients=[recipient],
            trigger_properties=braze_trigger_properties,
        )
        learner_content_assignment_action.completed_at = datetime.now()
        learner_content_assignment_action.save()
        logger.info(f'Sending braze campaign message for cancelled assignment {assignment}')
        return
    except Exception as exc:
        logger.error(f"Unable to send email for {lms_user_id} due to exception: {exc}")
        learner_content_assignment_action.error_reason = AssignmentActionErrors.EMAIL_ERROR
        learner_content_assignment_action.traceback = exc
        learner_content_assignment_action.save()
        raise


@shared_task(base=LoggedTaskWithRetry)
def send_reminder_email_for_pending_assignment(assignment_uuid):
    """
    Send email via braze for reminding users of their pending assignment
    Args:
        assignment_uuid: (string) the subsidy request uuid
    """
    # importing this here to get around a cyclical import error
    import enterprise_access.apps.content_assignments.api as content_api  # pylint: disable=import-outside-toplevel
    learner_content_assignment_model = apps.get_model('content_assignments.LearnerContentAssignment')
    subsidy_policy_model = apps.get_model('subsidy_access_policy.SubsidyAccessPolicy')
    try:
        assignment = learner_content_assignment_model.objects.get(uuid=assignment_uuid)
    except learner_content_assignment_model.DoesNotExist:
        logger.warning(f'request with uuid: {assignment_uuid} does not exist.')
        return

    try:
        policy = subsidy_policy_model.objects.get(
            assignment_configuration=assignment.assignment_configuration
        )
    except subsidy_policy_model.DoesNotExist:
        logger.warning(f'policy with assignment config: {assignment.assignment_configuration} does not exist.')
        return

    learner_content_assignment_action = LearnerContentAssignmentAction(
        assignment=assignment, action_type=AssignmentActions.REMINDED,
    )
    braze_trigger_properties = {}
    braze_client_instance = BrazeApiClient()
    lms_client = LmsApiClient()
    enterprise_customer_uuid = assignment.assignment_configuration.enterprise_customer_uuid
    enterprise_customer_data = lms_client.get_enterprise_customer_data(enterprise_customer_uuid)
    admin_emails = [user['email'] for user in enterprise_customer_data['admin_users']]
    course_metadata = content_api.get_content_metadata_for_assignments(
        policy.catalog_uuid, assignment.assignment_configuration
    )
    learner_portal_url = '{}/{}'.format(
        settings.ENTERPRISE_LEARNER_PORTAL_URL,
        enterprise_customer_data['slug'],
    )
    lms_user_id = assignment.lms_user_id
    braze_trigger_properties['contact_admin_link'] = braze_client_instance.generate_mailto_link(admin_emails)

    try:
        recipient = braze_client_instance.create_recipient(
            user_email=assignment.learner_email,
            lms_user_id=assignment.lms_user_id,
        )
        braze_trigger_properties["organization"] = enterprise_customer_data['name']
        braze_trigger_properties["course_title"] = assignment.content_title
        braze_trigger_properties["enrollment_deadline"] = course_metadata['normalized_metadata']['enroll_by_date']
        braze_trigger_properties["start_date"] = course_metadata['normalized_metadata']['start_date']
        braze_trigger_properties["course_partner"] = course_metadata['owners'][0]['name']
        braze_trigger_properties["course_card_image"] = course_metadata['card_image_url']
        braze_trigger_properties["learner_portal_link"] = learner_portal_url

        logger.info(f'Sending braze campaign message for reminded assignment {assignment}')
        braze_client_instance.send_campaign_message(
            settings.BRAZE_ASSIGNMENT_REMINDER_NOTIFICATION_CAMPAIGN,
            recipients=[recipient],
            trigger_properties=braze_trigger_properties,
        )
        learner_content_assignment_action.completed_at = datetime.now()
        learner_content_assignment_action.save()
        return
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error(f"Unable to send email for {lms_user_id} due to exception: {exc}")
        learner_content_assignment_action.error_reason = AssignmentActionErrors.EMAIL_ERROR
        learner_content_assignment_action.traceback = exc
        learner_content_assignment_action.save()
