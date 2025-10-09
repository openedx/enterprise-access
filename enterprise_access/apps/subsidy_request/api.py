"""
Primary Python API for interacting with Subsidy Request
records and business logic.
"""

import logging
from typing import Iterable

from django.db import transaction

from enterprise_access.apps.subsidy_access_policy.api import approve_learner_credit_request_via_policy
from enterprise_access.apps.subsidy_access_policy.exceptions import SubisidyAccessPolicyRequestApprovalError
from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates
from enterprise_access.apps.subsidy_request.models import LearnerCreditRequest
from enterprise_access.apps.subsidy_request.tasks import send_learner_credit_bnr_request_approve_task

logger = logging.getLogger(__name__)


def approve_learner_credit_requests(
    learner_credit_requests: Iterable[LearnerCreditRequest],
    policy_uuid: str,
    reviewer: object,
) -> dict:
    """
    Bulk approve Learner Credit Requests against a specific policy.

    This iterates through requests, attempts to approve each one by allocating an
    assignment, and triggers background tasks for notifications.

    Args:
        learner_credit_requests: An iterable of LearnerCreditRequest objects to be approved.
        policy_uuid: The UUID of the policy to approve against.
        reviewer: The user object of the admin performing the approval.

    Returns:
        A dict containing lists of 'approved' and 'failed_approval' requests.
    """
    approved_requests = []
    failed_requests = []

    # Only attempt to approve requests that are in a valid initial state.
    approvable_requests = {
        req
        for req in learner_credit_requests
        if req.state in [SubsidyRequestStates.REQUESTED, SubsidyRequestStates.ERROR]
    }

    for request in approvable_requests:
        try:
            with transaction.atomic():
                assignment = approve_learner_credit_request_via_policy(
                    policy_uuid,
                    request.course_id,
                    request.course_price,
                    request.user.email,
                    request.user.lms_user_id,
                )
                request.assignment = assignment
                request.approve(reviewer)  # This sets state, reviewer, and reviewed_at

                # The approval succeeded, so create the success action.
                request.add_successful_approved_action()

                # Enqueue the notification task only on full success.
                transaction.on_commit(
                    lambda assignment_uuid=assignment.uuid: send_learner_credit_bnr_request_approve_task.delay(
                        assignment_uuid
                    )
                )

            approved_requests.append(request)

        except SubisidyAccessPolicyRequestApprovalError as exc:
            # The approval failed, so create the errored action.
            request.add_errored_approved_action(exc)
            failed_requests.append(request)
            logger.exception(f"Failed to approve LCR {request.uuid}: {exc.message}")

    return {
        "approved": approved_requests,
        "failed_approval": failed_requests,
    }
