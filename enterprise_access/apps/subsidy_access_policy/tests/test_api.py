"""
Tests for the ``api.py`` module of the content_assignments app.
"""
from unittest import mock
from uuid import uuid4

import ddt
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.test import TestCase
from requests.exceptions import HTTPError
from rest_framework import status

from enterprise_access.apps.content_assignments.api import AllocationException
from enterprise_access.apps.content_assignments.tests.factories import LearnerContentAssignmentFactory
from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.subsidy_access_policy.api import approve_learner_credit_requests_via_policy
from enterprise_access.apps.subsidy_access_policy.exceptions import (
    ContentPriceNullException,
    PriceValidationError,
    SubisidyAccessPolicyRequestApprovalError,
    SubsidyAccessPolicyLockAttemptFailed
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory
)
from enterprise_access.apps.subsidy_request.models import LearnerCreditRequest


@ddt.ddt
class SubsidyAccessPolicyApiTests(TestCase):
    """
    Tests for the APIs in the subsidy_access_policy app.
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.enterprise_customer_uuid_1 = "12345678-1234-5678-1234-567812345678"
        self.policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            active=True,
            retired=False,
            per_learner_spend_limit=0,  # For B&R budget, limit should be set to 0.
            spend_limit=4000,
        )

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.approve')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.can_approve')
    def test_approve_learner_credit_request_via_policy_success(self, mock_can_approve, mock_approve):
        """
        Test that if a learner credit request is approved, the correct
        assignment is returned.
        """
        request = mock.MagicMock(spec=LearnerCreditRequest)
        assignment = LearnerContentAssignmentFactory()

        mock_can_approve.return_value = {
            "valid_requests": [request],
            "failed_requests_by_reason": {},
        }

        mock_approve.return_value = {request.uuid: assignment}

        result = approve_learner_credit_requests_via_policy(
            policy_uuid=self.policy.uuid,
            learner_credit_requests=[request],
        )

        self.assertIn("approved_requests", result)
        self.assertIn(request.uuid, result["approved_requests"])
        self.assertEqual(result["approved_requests"][request.uuid]["assignment"], assignment)
        self.assertEqual(result["approved_requests"][request.uuid]["request"], request)
        self.assertEqual(result["failed_requests_by_reason"], {})

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.approve')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.can_approve')
    def test_approve_learner_credit_requests_via_policy_partial_failure(self, mock_can_approve, mock_approve):
        """
        Test that failed requests are returned in failed_requests_by_reason.
        """
        valid_request = mock.MagicMock(spec=LearnerCreditRequest)
        failed_request = mock.MagicMock(spec=LearnerCreditRequest)
        assignment = LearnerContentAssignmentFactory()
        reason = "Some failure reason"
        mock_can_approve.return_value = {
            "valid_requests": [valid_request],
            "failed_requests_by_reason": {reason: [failed_request]},
        }
        mock_approve.return_value = {valid_request.uuid: assignment}

        result = approve_learner_credit_requests_via_policy(
            policy_uuid=self.policy.uuid,
            learner_credit_requests=[valid_request, failed_request],
        )

        self.assertIn(valid_request.uuid, result["approved_requests"])
        self.assertIn(reason, result["failed_requests_by_reason"])
        self.assertIn(failed_request, result["failed_requests_by_reason"][reason])

    def test_approve_learner_credit_request_via_policy_nonexistent_policy(self):
        """
        Test that if a policy does not exist, the correct exception is raised.
        """
        nonexistent_policy_uuid = str(uuid4())

        with self.assertRaises(SubisidyAccessPolicyRequestApprovalError) as context:
            approve_learner_credit_requests_via_policy(
                policy_uuid=nonexistent_policy_uuid,
                learner_credit_requests=[],
            )

        self.assertEqual(context.exception.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn(f"Policy with UUID {nonexistent_policy_uuid} does not exist", str(context.exception))

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.lock')
    def test_approve_learner_credit_request_via_policy_lock_failed(self, mock_lock):
        """
        Test that if acquiring the policy lock fails, the correct exception is raised.
        """
        mock_lock.side_effect = SubsidyAccessPolicyLockAttemptFailed("Lock acquisition failed")

        with self.assertRaises(SubisidyAccessPolicyRequestApprovalError) as context:
            approve_learner_credit_requests_via_policy(
                policy_uuid=self.policy.uuid,
                learner_credit_requests=[],
            )

        self.assertEqual(context.exception.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn(f"Failed to acquire lock for policy UUID {self.policy.uuid}", str(context.exception))

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.can_approve')
    @ddt.data(
        AllocationException("Allocation failed"),
        PriceValidationError("Price validation failed"),
        ValidationError("Validation error"),
        DatabaseError("Database error"),
        HTTPError("HTTP error"),
        ConnectionError("Connection error"),
        ContentPriceNullException("Content price is null"),
    )
    def test_approve_learner_credit_request_via_policy_exceptions(self, exception, mock_can_approve):
        """
        Test that various exceptions are properly caught and re-raised as SubsidyAccessPolicyRequestApprovalError.
        """
        mock_can_approve.side_effect = exception

        with self.assertRaises(SubisidyAccessPolicyRequestApprovalError) as context:
            approve_learner_credit_requests_via_policy(
                policy_uuid=self.policy.uuid,
                learner_credit_requests=[],
            )

        self.assertEqual(context.exception.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(str(context.exception), str(exception))

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.approve')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.can_approve')
    def test_approve_learner_credit_request_via_policy_approve_exception(self, mock_can_approve, mock_approve):
        """
        Test that exceptions raised during policy.approve() are properly handled.
        """
        request = mock.MagicMock(spec=LearnerCreditRequest)
        mock_can_approve.return_value = {
            "valid_requests": [request],
            "failed_requests_by_reason": {},
        }
        mock_approve.side_effect = AllocationException("Allocation failed during approval")

        with self.assertRaises(SubisidyAccessPolicyRequestApprovalError) as context:
            approve_learner_credit_requests_via_policy(
                policy_uuid=self.policy.uuid,
                learner_credit_requests=[],
            )

        self.assertEqual(context.exception.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(str(context.exception), "Allocation failed during approval")

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.approve')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.can_approve')
    def test_approve_learner_credit_request_via_policy_consistency_error(self, mock_can_approve, mock_approve):
        """
        Test that a consistency error is raised if approve() does not return
        an assignment for a request that was deemed valid by can_approve().
        """
        request = mock.MagicMock(spec=LearnerCreditRequest)

        # can_approve says this request is valid
        mock_can_approve.return_value = {
            "valid_requests": [request],
            "failed_requests_by_reason": {},
        }

        # But approve() returns an empty map, "forgetting" the assignment
        mock_approve.return_value = {}

        with self.assertRaises(SubisidyAccessPolicyRequestApprovalError) as context:
            approve_learner_credit_requests_via_policy(
                policy_uuid=self.policy.uuid,
                learner_credit_requests=[request],
            )

        self.assertIn("Consistency Error: Missing assignment for approved request", str(context.exception))
