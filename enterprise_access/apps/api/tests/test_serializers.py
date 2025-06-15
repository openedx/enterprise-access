"""
Tests for the serializers in the API.
"""
from datetime import datetime, timedelta, timezone
from unittest import mock
from uuid import uuid4

import ddt
from django.conf import settings
from django.test import TestCase
from freezegun import freeze_time

from enterprise_access.apps.api.serializers.subsidy_access_policy import (
    SubsidyAccessPolicyAggregatesSerializer,
    SubsidyAccessPolicyCreditsAvailableResponseSerializer,
    SubsidyAccessPolicyRedeemableResponseSerializer
)
from enterprise_access.apps.api.serializers.subsidy_requests import LearnerCreditRequestSerializer
from enterprise_access.apps.content_assignments.tests.factories import (
    AssignmentConfigurationFactory,
    LearnerContentAssignmentFactory
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    AssignedLearnerCreditAccessPolicyFactory,
    PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory
)
from enterprise_access.apps.subsidy_request.constants import (
    LearnerCreditRequestActionErrorReasons,
    SubsidyRequestStates
)
from enterprise_access.apps.subsidy_request.tests.factories import (
    LearnerCreditRequestActionsFactory,
    LearnerCreditRequestFactory
)


@ddt.ddt
class TestSubsidyAccessPolicyResponseSerializer(TestCase):
    """
    Tests for the SubsidyAccessPolicyResponseSerializer.
    """
    @ddt.data(
        # Test environment: An oddball zero value subsidy, no redemptions, and no allocations.
        {'starting_balance': 0, 'spend_limit': 1, 'redeemed': 0, 'allocated': 0, 'available': 0},  # 000

        # Test environment: 9 cent subsidy, oddball 0 cent policy.
        # 4 possible cases, should always indicate no available spend.
        {'starting_balance': 9, 'spend_limit': 0, 'redeemed': 0, 'allocated': 0, 'available': 0},  # 000
        {'starting_balance': 9, 'spend_limit': 0, 'redeemed': 0, 'allocated': 1, 'available': 0},  # 010
        {'starting_balance': 9, 'spend_limit': 0, 'redeemed': 1, 'allocated': 0, 'available': 0},  # 100
        {'starting_balance': 9, 'spend_limit': 0, 'redeemed': 1, 'allocated': 1, 'available': 0},  # 110

        # Test environment: 9 cent subsidy, unlimited policy.
        # 7 possible cases, the sum of redeemed+allocated+available should always equal 9.
        {'starting_balance': 9, 'spend_limit': 999, 'redeemed': 0, 'allocated': 0, 'available': 9},  # 001
        {'starting_balance': 9, 'spend_limit': 999, 'redeemed': 0, 'allocated': 9, 'available': 0},  # 010
        {'starting_balance': 9, 'spend_limit': 999, 'redeemed': 0, 'allocated': 5, 'available': 4},  # 011
        {'starting_balance': 9, 'spend_limit': 999, 'redeemed': 9, 'allocated': 0, 'available': 0},  # 100
        {'starting_balance': 9, 'spend_limit': 999, 'redeemed': 8, 'allocated': 0, 'available': 1},  # 101
        {'starting_balance': 9, 'spend_limit': 999, 'redeemed': 5, 'allocated': 4, 'available': 0},  # 110
        {'starting_balance': 9, 'spend_limit': 999, 'redeemed': 3, 'allocated': 3, 'available': 3},  # 111

        # Test environment: 9 cent subsidy, 8 cent policy.
        # 7 possible cases, the sum of redeemed+allocated+available should always equal 8.
        {'starting_balance': 9, 'spend_limit': 8, 'redeemed': 0, 'allocated': 0, 'available': 8},  # 001
        {'starting_balance': 9, 'spend_limit': 8, 'redeemed': 0, 'allocated': 8, 'available': 0},  # 010
        {'starting_balance': 9, 'spend_limit': 8, 'redeemed': 0, 'allocated': 3, 'available': 5},  # 011
        {'starting_balance': 9, 'spend_limit': 8, 'redeemed': 8, 'allocated': 0, 'available': 0},  # 100
        {'starting_balance': 9, 'spend_limit': 8, 'redeemed': 7, 'allocated': 0, 'available': 1},  # 101
        {'starting_balance': 9, 'spend_limit': 8, 'redeemed': 4, 'allocated': 4, 'available': 0},  # 110
        {'starting_balance': 9, 'spend_limit': 8, 'redeemed': 3, 'allocated': 3, 'available': 2},  # 111
    )
    @ddt.unpack
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    def test_aggregates(
        self,
        mock_subsidy_client,
        starting_balance,
        spend_limit,
        redeemed,
        allocated,
        available,
    ):
        """
        Test that the policy aggregates serializer returns the correct aggregate values.
        """
        test_enterprise_uuid = uuid4()

        # Synthesize subsidy with the current_balance derived from ``starting_balance`` and ``redeemed``.
        test_subsidy_uuid = uuid4()
        mock_subsidy_client.retrieve_subsidy.return_value = {
            'uuid': str(test_subsidy_uuid),
            'enterprise_customer_uuid': str(test_enterprise_uuid),
            'active_datetime': datetime.utcnow() - timedelta(days=1),
            'expiration_datetime': datetime.utcnow() + timedelta(days=1),
            'current_balance': starting_balance - redeemed,
            'is_active': True,
            'total_deposits': starting_balance
        }

        # Create a test policy with a limit set to ``policy_spend_limit``.  Reminder: a value of 0 means no limit.
        assignment_configuration = AssignmentConfigurationFactory(
            enterprise_customer_uuid=test_enterprise_uuid,
        )
        policy = AssignedLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=test_enterprise_uuid,
            subsidy_uuid=test_subsidy_uuid,
            spend_limit=spend_limit,
            assignment_configuration=assignment_configuration,
            active=True,
        )

        # Synthesize a number of 1 cent transactions equal to ``redeemed``.
        mock_subsidy_client.list_subsidy_transactions.return_value = {
            "results": [{"quantity": -1} for _ in range(redeemed)],
            "aggregates": {"total_quantity": redeemed * -1},
        }

        # Synthesize a number of 1 cent assignments equal to ``allocated``.
        for _ in range(allocated):
            LearnerContentAssignmentFactory(
                assignment_configuration=assignment_configuration,
                content_quantity=-1,
            )

        serializer = SubsidyAccessPolicyAggregatesSerializer(policy)
        data = serializer.data

        assert data["amount_redeemed_usd_cents"] == redeemed
        assert data["amount_allocated_usd_cents"] == allocated
        assert data["spend_available_usd_cents"] == available


@ddt.ddt
class TestSubsidyAccessPolicyRedeemableResponseSerializer(TestCase):
    """
    Tests for the SubsidyAccessPolicyRedeemableResponseSerializer.
    """
    NOW = datetime(2017, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    def setUp(self):
        self.redeemable_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory()

    def test_get_policy_redemption_url(self):
        """
        Test that the get_policy_redemption_url method returns the correct
        URL for the policy redemption.
        """
        serializer = SubsidyAccessPolicyRedeemableResponseSerializer(self.redeemable_policy)

        data = serializer.data
        self.assertIn("policy_redemption_url", data)
        expected_url = f"{settings.ENTERPRISE_ACCESS_URL}/api/v1/policy-redemption/" \
                       f"{self.redeemable_policy.uuid}/redeem/"
        self.assertEqual(data["policy_redemption_url"], expected_url)

    @ddt.data(
        {
            'late_redemption_allowed_until': None,
            'expected_is_late_redemption_allowed': False,
        },
        {
            'late_redemption_allowed_until': NOW - timedelta(days=1),
            'expected_is_late_redemption_allowed': False,
        },
        {
            'late_redemption_allowed_until': NOW + timedelta(days=1),
            'expected_is_late_redemption_allowed': True,
        },
    )
    @ddt.unpack
    @freeze_time(NOW)
    def test_is_late_enrollment_allowed(self, late_redemption_allowed_until, expected_is_late_redemption_allowed):
        """
        Test that the `is_late_enrollment_allowed` computed field is present and correct.
        """
        policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            late_redemption_allowed_until=late_redemption_allowed_until
        )
        serializer = SubsidyAccessPolicyRedeemableResponseSerializer(policy)
        assert serializer.data["is_late_redemption_allowed"] == expected_is_late_redemption_allowed


class TestSubsidyAccessPolicyCreditsAvailableResponseSerializer(TestCase):
    """
    Tests for the SubsidyAccessPolicyCreditsAvailableResponseSerializer.
    """
    def setUp(self):
        self.user_id = 24
        self.enterprise_uuid = uuid4()
        self.redeemable_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            spend_limit=300,
            active=True
        )

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.transactions_for_learner')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_record')
    def test_get_subsidy_end_date(self, mock_subsidy_record, mock_transactions_for_learner):
        """
        Test that the get_subsidy_end_date method returns the correct
        subsidy expiration date.
        """
        mock_transactions_for_learner.return_value = {
            'transactions': [],
            'aggregates': {
                'total_quantity': 0,
            },
        }
        subsidy_exp_date = '2030-01-01 12:00:00Z'
        mock_subsidy_record.return_value = {
            'uuid': str(uuid4()),
            'title': 'Test Subsidy',
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'expiration_datetime': subsidy_exp_date,
            'active_datetime': '2020-01-01 12:00:00Z',
            'current_balance': '1000',
            'total_deposits': '1000',
        }
        serializer = SubsidyAccessPolicyCreditsAvailableResponseSerializer(
            [self.redeemable_policy],
            many=True,
            context={'lms_user_id': self.user_id}
        )
        data = serializer.data
        self.assertIn('subsidy_expiration_date', data[0])
        self.assertEqual(data[0].get('subsidy_expiration_date'), subsidy_exp_date)


class TestLearnerCreditRequestSerializer(TestCase):
    """
    Tests for the LearnerCreditRequestSerializer.
    """

    def test_latest_action(self):
        """
        Test that the latest_action field returns the most recent action.
        """
        learner_credit_request = LearnerCreditRequestFactory()

        # Create multiple actions for the request with different timestamps
        LearnerCreditRequestActionsFactory(
            learner_credit_request=learner_credit_request,
            recent_action=SubsidyRequestStates.REQUESTED,
            status=SubsidyRequestStates.REQUESTED,
            created=datetime.now(timezone.utc) - timedelta(days=3)
        )

        LearnerCreditRequestActionsFactory(
            learner_credit_request=learner_credit_request,
            recent_action=SubsidyRequestStates.APPROVED,
            status=SubsidyRequestStates.APPROVED,
            created=datetime.now(timezone.utc) - timedelta(days=2)
        )

        latest_action = LearnerCreditRequestActionsFactory(
            learner_credit_request=learner_credit_request,
            recent_action=SubsidyRequestStates.ACCEPTED,
            status=SubsidyRequestStates.ACCEPTED,
            error_reason=LearnerCreditRequestActionErrorReasons.FAILED_APPROVAL,
            created=datetime.now(timezone.utc) - timedelta(days=1)
        )

        serializer = LearnerCreditRequestSerializer(learner_credit_request)
        data = serializer.data

        # Check that the latest_action field exists and contains the correct data
        self.assertIn('latest_action', data)
        latest_action_data = data['latest_action']
        self.assertIsNotNone(latest_action_data)

        # Verify the content of the latest action
        self.assertEqual(str(latest_action.uuid), latest_action_data['uuid'])
        self.assertEqual(latest_action.recent_action, latest_action_data['recent_action'])
        self.assertEqual(latest_action.status, latest_action_data['status'])
        self.assertEqual(latest_action.error_reason, latest_action_data['error_reason'])

    def test_no_actions(self):
        """
        Test that the latest_action field returns None when there are no actions.
        """
        learner_credit_request = LearnerCreditRequestFactory()

        serializer = LearnerCreditRequestSerializer(learner_credit_request)
        data = serializer.data

        self.assertIn('latest_action', data)
        self.assertIsNone(data['latest_action'])
