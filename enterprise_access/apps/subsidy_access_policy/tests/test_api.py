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
from enterprise_access.apps.content_assignments.models import LearnerContentAssignment
from enterprise_access.apps.content_assignments.tests.factories import LearnerContentAssignmentFactory
from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.subsidy_access_policy.api import approve_learner_credit_request_via_policy
from enterprise_access.apps.subsidy_access_policy.constants import REASON_CONTENT_NOT_IN_CATALOG
from enterprise_access.apps.subsidy_access_policy.exceptions import (
    PriceValidationError,
    SubisidyAccessPolicyRequestApprovalError,
    SubsidyAccessPolicyLockAttemptFailed
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory
)


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
        content_key = "test_content_key"
        content_price_cents = 1000
        learner_email = "test_learner@example.com"
        lms_user_id = "12345678"

        mock_can_approve.return_value = True, None
        mock_approve.return_value = LearnerContentAssignmentFactory(
            content_quantity=content_price_cents * -1,
            state='allocated',
            learner_email=learner_email,
            lms_user_id=lms_user_id,
            content_key=content_key,
        )

        result = approve_learner_credit_request_via_policy(
            policy_uuid=self.policy.uuid,
            content_key=content_key,
            content_price_cents=content_price_cents,
            learner_email=learner_email,
            lms_user_id=lms_user_id,
        )

        self.assertIsNotNone(result)
        self.assertIsInstance(result, LearnerContentAssignment)
        self.assertEqual(result.content_key, content_key)
        self.assertEqual(result.content_quantity, content_price_cents * -1)
        self.assertEqual(result.state, 'allocated')
        self.assertEqual(result.learner_email, learner_email)
        self.assertEqual(result.lms_user_id, lms_user_id)

    def test_approve_learner_credit_request_via_policy_nonexistent_policy(self):
        """
        Test that if a policy does not exist, the correct exception is raised.
        """
        nonexistent_policy_uuid = str(uuid4())

        with self.assertRaises(SubisidyAccessPolicyRequestApprovalError) as context:
            approve_learner_credit_request_via_policy(
                policy_uuid=nonexistent_policy_uuid,
                content_key="test_content_key",
                content_price_cents=1000,
                learner_email="test@example.com",
                lms_user_id="12345678",
            )

        self.assertEqual(context.exception.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn(f"Policy with UUID {nonexistent_policy_uuid} does not exist", str(context.exception))

    @ddt.data(
        (None, "test@example.com", 1000),  # Missing content_key
        ("", "test@example.com", 1000),    # Empty content_key
        ("test_content_key", None, 1000),  # Missing learner_email
        ("test_content_key", "", 1000),    # Empty learner_email
        ("test_content_key", "test@example.com", None),  # Missing content_price_cents
    )
    @ddt.unpack
    def test_approve_learner_credit_request_via_policy_missing_required_params(
        self, content_key, learner_email, content_price_cents
    ):
        """
        Test that if required parameters are missing or empty, the correct exception is raised.
        """
        with self.assertRaises(SubisidyAccessPolicyRequestApprovalError) as context:
            approve_learner_credit_request_via_policy(
                policy_uuid=self.policy.uuid,
                content_key=content_key,
                content_price_cents=content_price_cents,
                learner_email=learner_email,
                lms_user_id="12345678",
            )

        self.assertEqual(context.exception.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn("Content key, learner email, and content price must be provided", str(context.exception))

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.can_approve')
    def test_approve_learner_credit_request_via_policy_cannot_approve_with_reason(self, mock_can_approve):
        """
        Test that if can_approve returns False with a reason, the correct exception is raised.
        """
        reason = REASON_CONTENT_NOT_IN_CATALOG
        mock_can_approve.return_value = False, reason

        with self.assertRaises(SubisidyAccessPolicyRequestApprovalError) as context:
            approve_learner_credit_request_via_policy(
                policy_uuid=self.policy.uuid,
                content_key="test_content_key",
                content_price_cents=1000,
                learner_email="test@example.com",
                lms_user_id="12345678",
            )

        self.assertEqual(context.exception.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(str(context.exception), reason)

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.approve')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.can_approve')
    def test_approve_learner_credit_request_via_policy_approve_returns_none(self, mock_can_approve, mock_approve):
        """
        Test that if policy.approve() returns None, the correct exception is raised.
        """
        mock_can_approve.return_value = True, None
        mock_approve.return_value = None

        with self.assertRaises(SubisidyAccessPolicyRequestApprovalError) as context:
            approve_learner_credit_request_via_policy(
                policy_uuid=self.policy.uuid,
                content_key="test_content_key",
                content_price_cents=1000,
                learner_email="test@example.com",
                lms_user_id="12345678",
            )

        self.assertEqual(context.exception.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn("Failed to create an assignment while approving request", str(context.exception))

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.lock')
    def test_approve_learner_credit_request_via_policy_lock_failed(self, mock_lock):
        """
        Test that if acquiring the policy lock fails, the correct exception is raised.
        """
        mock_lock.side_effect = SubsidyAccessPolicyLockAttemptFailed("Lock acquisition failed")

        with self.assertRaises(SubisidyAccessPolicyRequestApprovalError) as context:
            approve_learner_credit_request_via_policy(
                policy_uuid=self.policy.uuid,
                content_key="test_content_key",
                content_price_cents=1000,
                learner_email="test@example.com",
                lms_user_id="12345678",
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
    )
    def test_approve_learner_credit_request_via_policy_exceptions(self, exception, mock_can_approve):
        """
        Test that various exceptions are properly caught and re-raised as SubsidyAccessPolicyRequestApprovalError.
        """
        mock_can_approve.side_effect = exception

        with self.assertRaises(SubisidyAccessPolicyRequestApprovalError) as context:
            approve_learner_credit_request_via_policy(
                policy_uuid=self.policy.uuid,
                content_key="test_content_key",
                content_price_cents=1000,
                learner_email="test@example.com",
                lms_user_id="12345678",
            )

        self.assertEqual(context.exception.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(str(context.exception), str(exception))

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.approve')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.can_approve')
    def test_approve_learner_credit_request_via_policy_approve_exception(self, mock_can_approve, mock_approve):
        """
        Test that exceptions raised during policy.approve() are properly handled.
        """
        mock_can_approve.return_value = True, None
        mock_approve.side_effect = AllocationException("Allocation failed during approval")

        with self.assertRaises(SubisidyAccessPolicyRequestApprovalError) as context:
            approve_learner_credit_request_via_policy(
                policy_uuid=self.policy.uuid,
                content_key="test_content_key",
                content_price_cents=1000,
                learner_email="test@example.com",
                lms_user_id="12345678",
            )

        self.assertEqual(context.exception.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(str(context.exception), "Allocation failed during approval")
