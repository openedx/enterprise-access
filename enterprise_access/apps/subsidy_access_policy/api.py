"""
Python API for interacting with SubsidyAccessPolicy records.
"""
import logging
from typing import Iterable

from django.core.exceptions import ValidationError
from django.db import DatabaseError
from requests.exceptions import HTTPError
from rest_framework import status

from enterprise_access.apps.content_assignments.api import AllocationException
from enterprise_access.apps.subsidy_request.models import LearnerCreditRequest

from .exceptions import (
    ContentPriceNullException,
    PriceValidationError,
    SubisidyAccessPolicyRequestApprovalError,
    SubsidyAccessPolicyLockAttemptFailed
)
from .models import SubsidyAccessPolicy

logger = logging.getLogger(__name__)


def get_subsidy_access_policy(uuid):
    """
    Returns a `SubsidyAccessPolicy` record with the given uuid,
    or null if no such record exists.
    """
    try:
        return SubsidyAccessPolicy.objects.get(uuid=uuid)
    except SubsidyAccessPolicy.DoesNotExist:
        return None


def approve_learner_credit_requests_via_policy(
    policy_uuid: str,
    learner_credit_requests: Iterable[LearnerCreditRequest],
) -> dict:
    """
    Approves a batch of Learner Credit Requests via the specified SubsidyAccessPolicy.
    If the policy does not exist, raises a `SubisidyAccessPolicyRequestApprovalError`.
    This now handles partial success and failure, creating assignments only for valid requests.

    Args:
        policy_uuid (str): The UUID of the policy to approve against.
        learner_credit_requests (list[LearnerCreditRequest]): The requests to process.

    Returns:
        A dictionary containing 'approved_requests' (with their assignments) and
        'failed_requests_by_reason'.
    """
    policy = get_subsidy_access_policy(policy_uuid)
    if not policy:
        error_msg = f"Policy with UUID {policy_uuid} does not exist."
        logger.error(error_msg)
        raise SubisidyAccessPolicyRequestApprovalError(message=error_msg, status_code=status.HTTP_404_NOT_FOUND)

    try:
        with policy.lock():
            # 1. Call can_approve, which now returns a dictionary of valid and failed requests.
            validation_result = policy.can_approve(learner_credit_requests)

            error_reason = validation_result.get("error_reason", '')
            if error_reason:
                raise SubisidyAccessPolicyRequestApprovalError(
                    message=error_reason,
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
                )

            valid_requests = validation_result.get("valid_requests", [])
            failed_requests_by_reason = validation_result.get("failed_requests_by_reason", {})

            approved_requests_map = {}
            if valid_requests:
                # 2. If there are valid requests, call approve() only with that list.
                request_to_assignment_map = policy.approve(valid_requests)
                for request in valid_requests:
                    assignment = request_to_assignment_map.get(request.uuid)
                    if not assignment:
                        # This would indicate a major internal error, as allocation should be atomic.
                        raise SubisidyAccessPolicyRequestApprovalError(
                            f"Consistency Error: Missing assignment for approved request {request.uuid}"
                        )
                    approved_requests_map[request.uuid] = {
                        "request": request,
                        "assignment": assignment,
                    }

            return {
                "approved_requests": approved_requests_map,
                "failed_requests_by_reason": failed_requests_by_reason,
            }

    except SubsidyAccessPolicyLockAttemptFailed as exc:
        logger.exception(exc)
        error_msg = (
            f"Failed to acquire lock for policy UUID {policy_uuid}. "
            "Please try again later."
        )
        raise SubisidyAccessPolicyRequestApprovalError(
            message=error_msg,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
        ) from exc
    except (
        AllocationException, PriceValidationError, ValidationError, DatabaseError,
        HTTPError, ConnectionError, ContentPriceNullException
    ) as exc:
        logger.exception(
            "A validation or database error occurred during bulk approval for policy %s: %s", policy_uuid, exc
        )
        raise SubisidyAccessPolicyRequestApprovalError(
            message=str(exc),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
        ) from exc
