"""
Python API for interacting with SubsidyAccessPolicy records.
"""
import logging

from django.core.exceptions import ValidationError
from django.db import DatabaseError
from requests.exceptions import HTTPError
from rest_framework import status

from enterprise_access.apps.content_assignments.api import AllocationException

from .exceptions import (
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


def approve_learner_credit_request_via_policy(
    policy_uuid,
    content_key,
    content_price_cents,
    learner_email,
    lms_user_id,
):
    """
    Approves a Learner Credit Request via the specified SubsidyAccessPolicy.
    If the policy does not exist, raises a `SubisidyAccessPolicyRequestApprovalError`.
    If the content key, learner email, or content price is not provided,
    raises a `SubisidyAccessPolicyRequestApprovalError`.
    If the request cannot be approved via policy, raises a `SubisidyAccessPolicyRequestApprovalError`
    If the policy is successfully approved, returns a `LearnerCreditRequestAssignment`
    object.
    """
    policy = get_subsidy_access_policy(policy_uuid)
    if not policy:
        error_msg = f"Policy with UUID {policy_uuid} does not exist."
        logger.error(error_msg)
        raise SubisidyAccessPolicyRequestApprovalError(message=error_msg, status_code=status.HTTP_404_NOT_FOUND)

    if not content_key or not learner_email or content_price_cents is None:
        error_msg = (
            "Content key, learner email, and content price must be provided."
        )
        logger.error(error_msg)
        raise SubisidyAccessPolicyRequestApprovalError(
            message=error_msg,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
        )
    try:
        with policy.lock():
            can_approve, reason = policy.can_approve(
                content_key,
                content_price_cents,
            )
            if can_approve:
                learner_credit_request_assignment = policy.approve(
                    learner_email,
                    content_key,
                    content_price_cents,
                    lms_user_id,
                )
                if not learner_credit_request_assignment:
                    error_msg = (
                        f"Failed to create an assignment while approving request for learner: "
                        f"{learner_email} with content key: {content_key} and price: {content_price_cents}"
                    )
                    logger.error(error_msg)
                    raise SubisidyAccessPolicyRequestApprovalError(
                        message=error_msg,
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
                    )
                return learner_credit_request_assignment
            if reason:
                raise SubisidyAccessPolicyRequestApprovalError(
                    message=reason,
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
                )
            # If we reach here, can_approve is False but no reason was provided
            raise SubisidyAccessPolicyRequestApprovalError(
                message="Request cannot be approved by this policy",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
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
    except (AllocationException, PriceValidationError, ValidationError, DatabaseError,
            HTTPError, ConnectionError) as exc:
        logger.exception(exc)
        raise SubisidyAccessPolicyRequestApprovalError(
            message=str(exc),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
        ) from exc
