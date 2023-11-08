"""
Tasks for content_assignments app.
"""

import logging

from celery import shared_task
from django.apps import apps
from django.conf import settings

from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.content_assignments.models import LearnerContentAssignment, LearnerContentAssignmentAction
from enterprise_access.apps.subsidy_request import _get_course_partners
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

    # Intentionally not logging the learner email (PII).
    logger.info(f'Creating a pending enterprise user for enterprise {enterprise_customer_uuid}.')

    lms_client = LmsApiClient()
    # Could raise HTTPError and trigger task retry.  Intentionally ignoring response since success should just not throw
    # an exception.  Two possible success statuses are 201 (created) and 200 (found), but there's no reason to
    # distinguish them for the purpose of this task.
    lms_client.create_pending_enterprise_users(enterprise_customer_uuid, [assignment.learner_email])

    # TODO: ENT-7596: Save activity history on this assignment to represent that the learner is successfully linked to
    # the enterprise.


@shared_task(base=LoggedTaskWithRetry)
def send_reminder_email_for_pending_assignment(assignment_uuid):
    """
    Send email via braze for reminding users of their pending assignment
    Args:
        assignment_uuid: (string) the subsidy request uuid
    """
    assignment = LearnerContentAssignment.objects.get(assignment_uuid)
    policy = SubsidyAccessPolicy.objects.get(
        assignment_configuration=assignment.assignment_configuration
    )
    # policy.catalog_uuid
    get_content_metadata_for_assignments(policy.catalog_uuid, )
    # caches (be mindful)

    # subsidy = SubsidyRequest.objects.get(
    #     uuid=policy.subsidy_uuid
    # )

    learner_content_assignment_action = LearnerContentAssignmentAction(
        assignment=assignment, action_type=AssignmentActions.REMINDED,
    )

    if braze_trigger_properties is None:
        braze_trigger_properties = {}

    braze_client_instance = BrazeApiClient()
    lms_client = LmsApiClient()
    enterprise_customer_uuid = assignment.assignment_configuration.enterprise_customer_uuid
    enterprise_customer_data = lms_client.get_enterprise_customer_data(enterprise_customer_uuid)
    # is content_key the same as course_id from subsidy info?
    # if not can i fetch subsidy request which has the course_id?
    course_data = discovery_client.get_course_data(assignment.content_key)
    # how can a subsidy request have information about the course for this assignment though?
    # course_data = discovery_client.get_course_data(subsidy.course_id)
    lms_user_id = assignment.lms_user_id

    try:
        recipient = braze_client_instance.create_recipient(
            user_email=assignment.learner_email,
            lms_user_id=assignment.lms_user_id,
        )
        braze_trigger_properties["first_name"] = lms_user_id
        braze_trigger_properties["organization"] = enterprise_customer_data['name']
        braze_trigger_properties["course_title"] = assignment.content_title
        braze_trigger_properties["enrollment_deadline"] = course_data["enrollment_end"]
        braze_trigger_properties["start_date"] = course_data["start"]
        braze_trigger_properties["course_partner"] = _get_course_partners(course_data)
        braze_trigger_properties["course_card_image"] = course_data['card_image_url']
        
        # Call to action link to Learner Portal, with logistration logic.
        # Admin email hyperlink (should be available in the LMS model for the enterprise – if not available, conditional logic might be required to make this not be a link).

        braze_client_instance.send_campaign_message(
            settings.BRAZE_ASSIGNMENT_REMINDER_NOTIFICATION_CAMPAIGN,
            recipients=[recipient],
            trigger_properties=braze_trigger_properties,
        )
        learner_content_assignment_action.completed_at = datetime.now()
        learner_content_assignment_action.save()
    except Exception as exc:
        logger.error(f"Unable to send email for {lms_user_id} due to exception: {exc}")
        learner_content_assignment_action.error_reason = exc