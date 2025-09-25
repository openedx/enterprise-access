"""
Primary Python API for interacting with Subsidy Request
records and business logic.
"""
import logging
from typing import Iterable

from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates
from enterprise_access.apps.subsidy_request.models import LearnerCreditRequest
from enterprise_access.apps.subsidy_request.tasks import send_reminder_email_for_pending_learner_credit_request

logger = logging.getLogger(__name__)


def remind_learner_credit_requests(requests: Iterable[LearnerCreditRequest]) -> dict:
    """
    Bulk remind for Learner Credit Requests.

    This filters for requests that are in a remindable state and triggers a Celery
    task to send a reminder email for each one.

    Args:
        requests: An iterable of LearnerCreditRequest objects.

    Returns:
        A dict containing lists of 'remindable_requests' and 'non_remindable_requests' requests.
    """
    # A request is only remindable if it is APPROVED and has an associated assignment.
    remindable_requests = {
        req for req in requests
        if req.state == SubsidyRequestStates.APPROVED and req.assignment_id is not None
    }

    non_remindable_requests = set(requests) - remindable_requests

    logger.info(f'Skipping {len(non_remindable_requests)} non-remindable learner credit requests.')
    logger.info(f'Queueing reminders for {len(remindable_requests)} learner credit requests.')

    for req in remindable_requests:
        send_reminder_email_for_pending_learner_credit_request.delay(req.assignment.uuid)

    return {
        'remindable_requests': list(remindable_requests),
        'non_remindable_requests': list(non_remindable_requests),
    }
