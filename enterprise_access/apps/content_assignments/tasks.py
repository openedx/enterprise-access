"""
Tasks for content_assignments app.
"""

import logging

from celery import shared_task
from django.apps import apps
from django.conf import settings

from enterprise_access.apps.api_client.lms_client import LmsApiClient
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
