"""
Tests for the serializers in the API.
"""
from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from enterprise_access.apps.api.serializers.subsidy_access_policy import SubsidyAccessPolicyRedeemableResponseSerializer
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
            'api:v1:policy-redeem',
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
        expected_url = f"{settings.ENTERPRISE_ACCESS_URL}/api/v1/policy/{self.non_redeemable_policy.uuid}/redeem/"
        self.assertEqual(data["policy_redemption_url"], expected_url)
