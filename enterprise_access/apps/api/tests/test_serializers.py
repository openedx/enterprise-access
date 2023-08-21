"""
Tests for the serializers in the API.
"""
from unittest import mock
from uuid import uuid4

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from enterprise_access.apps.api.serializers.subsidy_access_policy import (
    SubsidyAccessPolicyCreditsAvailableResponseSerializer,
    SubsidyAccessPolicyRedeemableResponseSerializer
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory
)


class TestSubsidyAccessPolicyRedeemableResponseSerializer(TestCase):
    """
    Tests for the SubsidyAccessPolicyRedeemableResponseSerializer.
    """
    def setUp(self):
        self.non_redeemable_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory()
        self.subsidy_access_policy_redeem_endpoint = reverse(
            'api:v1:policy-redemption-redeem',
            kwargs={'policy_uuid': self.non_redeemable_policy.uuid}
        )

    def test_get_policy_redemption_url(self):
        """
        Test that the get_policy_redemption_url method returns the correct
        URL for the policy redemption.
        """

        serializer = SubsidyAccessPolicyRedeemableResponseSerializer(self.non_redeemable_policy)

        data = serializer.data
        self.assertIn("policy_redemption_url", data)
        expected_url = f"{settings.ENTERPRISE_ACCESS_URL}/api/v1/policy-redemption/" \
                       f"{self.non_redeemable_policy.uuid}/redeem/"
        self.assertEqual(data["policy_redemption_url"], expected_url)


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
        }
        serializer = SubsidyAccessPolicyCreditsAvailableResponseSerializer(
            [self.redeemable_policy],
            many=True,
            context={'lms_user_id': self.user_id}
        )
        data = serializer.data
        self.assertIn('subsidy_expiration_date', data[0])
        self.assertEqual(data[0].get('subsidy_expiration_date'), subsidy_exp_date)
