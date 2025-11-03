"""
Primary Python API for interacting with Subsidy Request
records and business logic.
"""

import logging
from typing import Iterable

from django.db import transaction

from enterprise_access.apps.subsidy_access_policy.api import approve_learner_credit_requests_via_policy
from enterprise_access.apps.subsidy_access_policy.exceptions import SubisidyAccessPolicyRequestApprovalError
from enterprise_access.apps.subsidy_request.constants import (
    LearnerCreditRequestActionErrorReasons,
    SubsidyRequestStates
)
from enterprise_access.apps.subsidy_request.models import LearnerCreditRequest, LearnerCreditRequestActions
from enterprise_access.apps.subsidy_request.tasks import send_learner_credit_bnr_request_approve_task
from enterprise_access.apps.subsidy_request.utils import get_action_choice, get_user_message_choice
from enterprise_access.utils import format_traceback, localized_utcnow

logger = logging.getLogger(__name__)


def approve_learner_credit_requests(
    learner_credit_requests: Iterable[LearnerCreditRequest],
    policy_uuid: str,
    reviewer: object,
) -> dict:
    """
    Bulk approve Learner Credit Requests against a specific policy.
    This handles partial success and failure, using bulk operations for maximum performance.
    """
    requests_to_process = [
        req for req in learner_credit_requests
        if req.state in [SubsidyRequestStates.REQUESTED, SubsidyRequestStates.ERROR]
    ]
    if not requests_to_process:
        return {"approved": [], "failed": list(learner_credit_requests)}

    approved_requests = []
    failed_requests = []
    actions_to_create = []
    error_message = None

    try:
        response = approve_learner_credit_requests_via_policy(
            policy_uuid,
            requests_to_process
        )
        approved_requests_map = response.get("approved_requests", {})
        failed_requests_by_reason = response.get("failed_requests_by_reason", {})

        # prepare all data for bulk operations.
        approved_requests = _prepare_requests_for_update(approved_requests_map, reviewer)
        failed_requests, failed_actions = _prepare_failed_requests_and_actions(failed_requests_by_reason)
        actions_to_create.extend(failed_actions)

    except SubisidyAccessPolicyRequestApprovalError as exc:
        # Handle global failures by preparing failure actions for all requests.
        logger.warning(
            "Bulk approval failed for policy %s with a global error. Reason: %s", policy_uuid, exc.message
        )
        error_message = exc.message
        for request in requests_to_process:
            actions_to_create.append(
                LearnerCreditRequestActions(
                    learner_credit_request=request,
                    recent_action=get_action_choice(SubsidyRequestStates.APPROVED),
                    status=get_user_message_choice(SubsidyRequestStates.REQUESTED),
                    error_reason=LearnerCreditRequestActionErrorReasons.FAILED_APPROVAL,
                    traceback=format_traceback(exc),
                )
            )
        failed_requests.extend(requests_to_process)

    # This transaction ensures the LearnerCreditRequest records are in their final state
    # before any dependent actions are created.
    if approved_requests:
        with transaction.atomic():
            approved_requests = _update_and_refresh_requests(
                approved_requests, ['state', 'assignment', 'reviewer', 'reviewed_at']
            )

    # Now that we have refreshed `approved_requests`, prepare the success actions.
    success_actions_to_create = [
        LearnerCreditRequestActions(
            learner_credit_request=request,
            recent_action=get_action_choice(SubsidyRequestStates.APPROVED),
            status=get_user_message_choice(SubsidyRequestStates.APPROVED),
        ) for request in approved_requests
    ]
    actions_to_create.extend(success_actions_to_create)

    # In a separate transaction, create the audit trail for the entire batch.
    if actions_to_create:
        with transaction.atomic():
            LearnerCreditRequestActions.bulk_create(actions_to_create)

    # Enqueue notifications
    for request in approved_requests:
        transaction.on_commit(
            lambda assignment_uuid=request.assignment.uuid: send_learner_credit_bnr_request_approve_task.delay(
                assignment_uuid
            )
        )

    return {"approved": approved_requests, "failed_approval": failed_requests, "error_message": error_message}


def _update_and_refresh_requests(requests_to_update, fields_to_update):
    """
    Helper to bulk update LearnerCreditRequest records and refresh their state from the DB,
    mirroring the pattern in the content_assignments API.
    """
    if not requests_to_update:
        return []

    LearnerCreditRequest.bulk_update(requests_to_update, fields_to_update)

    # Get a list of refreshed objects that we just updated.
    return list(
        LearnerCreditRequest.objects.prefetch_related('actions').filter(
            uuid__in=[record.uuid for record in requests_to_update],
        )
    )


def _prepare_requests_for_update(approved_requests_map, reviewer):
    """
    Prepares successful LearnerCreditRequest objects for bulk_update.
    Does NOT prepare actions.
    """
    requests_to_update = []
    if approved_requests_map:
        requests_to_update = [item["request"] for item in approved_requests_map.values()]
        for request in requests_to_update:
            request.state = SubsidyRequestStates.APPROVED
            request.reviewer = reviewer
            request.reviewed_at = localized_utcnow()
            request.assignment = approved_requests_map[request.uuid]["assignment"]
    return requests_to_update


def _prepare_failed_requests_and_actions(failed_requests_by_reason):
    """
    Prepares failure action objects for bulk create and returns the list of failed requests.
    """
    all_failed_requests = []
    actions_to_create = []
    for reason, requests in failed_requests_by_reason.items():
        for request in requests:
            failure_reason_str = getattr(request, 'failure_reason', reason)
            actions_to_create.append(
                LearnerCreditRequestActions(
                    learner_credit_request=request,
                    recent_action=get_action_choice(SubsidyRequestStates.APPROVED),
                    status=get_user_message_choice(SubsidyRequestStates.REQUESTED),
                    error_reason=LearnerCreditRequestActionErrorReasons.FAILED_APPROVAL,
                    traceback=f"Validation failed with reason: {failure_reason_str}",
                )
            )
        all_failed_requests.extend(requests)
    return all_failed_requests, actions_to_create
