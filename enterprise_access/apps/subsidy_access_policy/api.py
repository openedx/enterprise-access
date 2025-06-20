"""
Python API for interacting with SubsidyAccessPolicy records.
"""
import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework import status

from enterprise_access.apps.content_assignments.api import AllocationException

from .exceptions import SubisidyAccessPolicyRequestApprovalError, SubsidyAccessPolicyLockAttemptFailed
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
    If the request cannot be approved via policy, raises a `SubisidyAccessPolicyRequestApprovalError`
    If the policy is successfully approved, returns a `LearnerCreditRequestAssignment`
    object.
    """
    policy = get_subsidy_access_policy(policy_uuid)
    if not policy:
        error_msg = f"[LC REQUEST APPROVAL] Policy with UUID {policy_uuid} does not exist."
        logger.error(error_msg)
        raise SubisidyAccessPolicyRequestApprovalError(message=error_msg, status_code=status.HTTP_404_NOT_FOUND)
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
                    error_message = (
                        f"[LC REQUEST APPROVAL] Failed to approve Learner Credit Request via policy UUID {policy_uuid}. "
                        "No assignment was created."
                    )
                    logger.error(error_msg)
                    raise SubisidyAccessPolicyRequestApprovalError(message=error_msg, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)
                return learner_credit_request_assignment
            if reason:
                error_message = f"[LC REQUEST APPROVAL] Request cannot be approved. Reason: {reason}"
                logger.error(error_message)
                raise SubisidyAccessPolicyRequestApprovalError(message=error_message, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)
    except SubsidyAccessPolicyLockAttemptFailed as exc:
        logger.exception(exc)
        error_msg = (
            f"[LC REQUEST APPROVAL] Failed to acquire lock for policy UUID {policy_uuid}. "
            "Please try again later."
        )
        logger.exception(error_msg)
        raise SubisidyAccessPolicyRequestApprovalError(message=error_msg, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY) from exc
    except (AllocationException, ValidationError) as exc:
        error_detail = (
            f"[LC REQUEST APPROVAL] Failed to approve Learner Credit Request via policy UUID {policy_uuid}. "
            f"Error: {str(exc)}"
        )
        raise SubisidyAccessPolicyRequestApprovalError(message=error_detail, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY) from exc
