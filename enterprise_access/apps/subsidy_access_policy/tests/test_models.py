""" Tests for subsidy_access_policy models. """
from unittest.mock import patch
from uuid import uuid4

import ddt
import factory
from django.test import TestCase

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    CappedEnrollmentLearnerCreditAccessPolicyFactory,
    LicenseAccessPolicyFactory,
    LicenseRequestAccessPolicyFactory,
    PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory,
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory
)


@ddt.ddt
@patch('enterprise_access.apps.subsidy_access_policy.models.group_client')
@patch('enterprise_access.apps.subsidy_access_policy.models.subsidy_client')
@patch('enterprise_access.apps.subsidy_access_policy.models.catalog_client')
class SubsidyAccessPolicyTests(TestCase):
    """ SubsidyAccessPolicy model tests. """

    user = factory.SubFactory(UserFactory)
    course_id = factory.LazyFunction(uuid4)

    @ddt.data(
        (False, True, True, True, False, False, False),
        (True, False, True, True, False, False, False),
        (True, True, False, True, False, False, False),
        (True, True, True, True, False, True, False),
        (False, True, True, False, True, False, False),
        (True, False, True, False, True, False, False),
        (True, True, False, False, True, False, False),
        (True, True, True, False, True, False, True)
    )
    @ddt.unpack
    def test_license_access_policy_can_redeem(
        self,
        catalog_contains_content,
        group_contains_learner,
        is_redeemable,
        is_license_for_learner,
        is_license_for_group,
        can_redeem_via_learner_license,
        can_redeem_via_group_license,
        mock_catalog_client,
        mock_subsidy_client,
        mock_group_client
        ):
        """
        Test the can_redeem method of LicenseAccessPolicy model
        """
        mock_catalog_client.catalog_contains_content.return_value = catalog_contains_content
        mock_group_client.group_contains_learner.return_value = group_contains_learner
        mock_subsidy_client.can_redeem.return_value = is_redeemable
        mock_subsidy_client.get_license_for_learner.return_value = is_license_for_learner
        license_access_policy = LicenseAccessPolicyFactory()
        self.assertEqual(
            license_access_policy.can_redeem(self.user, self.course_id),
            can_redeem_via_learner_license
            )
        # test for redemption via group license
        mock_subsidy_client.get_license_for_learner.return_value = False
        license_access_policy.group_uuid = 'test-uuid'
        mock_group_client.get_groups_for_learner.return_value = [license_access_policy.enterprise_customer_uuid]
        mock_subsidy_client.get_license_for_group.return_value = is_license_for_group
        self.assertEqual(
            license_access_policy.can_redeem(self.user, self.course_id),
            can_redeem_via_group_license
            )


    @ddt.data(
        (True, True, False, None, None),
        (True, True, True, 999, 999)
    )
    @ddt.unpack
    def test_license_access_policy_redeem(
        self,
        catalog_contains_content,
        group_contains_learner,
        is_redeemable,
        ledger_transaction_id,
        redeem_return_value,
        mock_catalog_client,
        mock_subsidy_client,
        mock_group_client
        ):
        """
        Test the redeem method of LicenseAccessPolicy model
        """
        mock_catalog_client.catalog_contains_content.return_value = catalog_contains_content
        mock_group_client.group_contains_learner.return_value = group_contains_learner
        mock_subsidy_client.can_redeem.return_value = is_redeemable
        mock_subsidy_client.redeem.return_value = ledger_transaction_id
        license_access_policy = LicenseAccessPolicyFactory()
        self.assertEqual(license_access_policy.redeem(self.user, self.course_id), redeem_return_value)

    def test_license_access_policy_has_redeemed(
        self,
        mock_catalog_client, # lint-amnesty, pylint: disable=unused-argument
        mock_subsidy_client,
        mock_group_client # lint-amnesty, pylint: disable=unused-argument
        ):
        """
        Test the has_redeemed method of LicenseAccessPolicy model
        """
        license_access_policy = LicenseAccessPolicyFactory()
        mock_subsidy_client.has_redeemed.return_value = True
        self.assertTrue(license_access_policy.has_redeemed(self.user, self.course_id))

        license_access_policy.access_method = 'unknown_test_method'
        with self.assertRaises(ValueError):
            license_access_policy.has_redeemed(self.user, self.course_id)

    @ddt.data(
        (False, True, True, False),
        (True, False, True, False),
        (True, True, False, False),
        (True, True, True, True)
    )
    @ddt.unpack
    def test_license_request_access_policy_can_redeem(
        self,
        catalog_contains_content,
        group_contains_learner,
        is_redeemable,
        can_redeem,
        mock_catalog_client,
        mock_subsidy_client,
        mock_group_client
        ):
        """
        Test the can_redeem method of LicenseRequestAccessPolicy model
        """
        mock_catalog_client.catalog_contains_content.return_value = catalog_contains_content
        mock_group_client.group_contains_learner.return_value = group_contains_learner
        mock_subsidy_client.can_redeem.return_value = is_redeemable
        license_request_access_policy = LicenseRequestAccessPolicyFactory()
        self.assertEqual(license_request_access_policy.can_redeem(self.user, self.course_id), can_redeem)

    @ddt.data(
        (True, True, False, None, None),
        (True, True, True, 999, 999)
    )
    @ddt.unpack
    def test_license_request_access_policy_redeem(
        self,
        catalog_contains_content,
        group_contains_learner,
        is_redeemable,
        ledger_transaction_id,
        request_redemption_return_value,
        mock_catalog_client,
        mock_subsidy_client,
        mock_group_client
        ):
        """
        Test the redeem method of LicenseRequestAccessPolicy model
        """
        mock_catalog_client.catalog_contains_content.return_value = catalog_contains_content
        mock_group_client.group_contains_learner.return_value = group_contains_learner
        mock_subsidy_client.can_redeem.return_value = is_redeemable
        mock_subsidy_client.request_redemption.return_value = ledger_transaction_id
        license_request_access_policy = LicenseRequestAccessPolicyFactory()
        self.assertEqual(
            license_request_access_policy.redeem(self.user, self.course_id),
            request_redemption_return_value
            )

    def test_license_request_access_policy_has_redeemed(
        self,
        mock_catalog_client, # lint-amnesty, pylint: disable=unused-argument
        mock_subsidy_client,
        mock_group_client # lint-amnesty, pylint: disable=unused-argument
        ):
        """
        Test the has_redeemed method of LicenseRequestAccessPolicy model
        """
        license_request_access_policy = LicenseRequestAccessPolicyFactory()
        mock_subsidy_client.has_requested.return_value = True
        self.assertTrue(license_request_access_policy.has_redeemed(self.user, self.course_id))

        license_request_access_policy.access_method = 'unknown_test_method'
        with self.assertRaises(ValueError):
            license_request_access_policy.has_redeemed(self.user, self.course_id)

    @ddt.data(
        (True, True, True, 6, False),
        (True, True, True, 5, False),
        (True, True, True, 4, True)
    )
    @ddt.unpack
    def test_per_learner_enrollment_cap_learner_credit_access_policy_can_redeem(
        self,
        catalog_contains_content,
        group_contains_learner,
        is_redeemable,
        transactions_for_learner,
        can_redeem,
        mock_catalog_client,
        mock_subsidy_client,
        mock_group_client
        ):
        """
        Test the can_redeem method of PerLearnerEnrollmentCapLearnerCreditAccessPolicy model
        """
        mock_catalog_client.catalog_contains_content.return_value = catalog_contains_content
        mock_group_client.group_contains_learner.return_value = group_contains_learner
        mock_subsidy_client.can_redeem.return_value = is_redeemable
        mock_subsidy_client.transactions_for_learner.return_value.count.return_value = transactions_for_learner
        per_learner_enrollment_cap_learner_credit_access_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory() # lint-amnesty, pylint: disable=line-too-long
        per_learner_enrollment_cap_learner_credit_access_policy.per_learner_enrollment_limit = 5
        self.assertEqual(
            per_learner_enrollment_cap_learner_credit_access_policy.can_redeem(self.user, self.course_id),
            can_redeem
            )

    @ddt.data(
        (True, True, True, 491, False),
        (True, True, True, 490, False),
        (True, True, True, 489, True)
    )
    @ddt.unpack
    def test_per_learner_spend_cap_learner_credit_access_policy_can_redeem(
        self,
        catalog_contains_content,
        group_contains_learner,
        is_redeemable,
        amount_spent_for_learner,
        can_redeem,
        mock_catalog_client,
        mock_subsidy_client,
        mock_group_client
        ):
        """
        Test the can_redeem method of PerLearnerSpendCapLearnerCreditAccessPolicy model
        """
        mock_catalog_client.catalog_contains_content.return_value = catalog_contains_content
        mock_catalog_client.get_course_price.return_value = 10
        mock_group_client.group_contains_learner.return_value = group_contains_learner
        mock_subsidy_client.can_redeem.return_value = is_redeemable
        mock_subsidy_client.amount_spent_for_learner.return_value = amount_spent_for_learner
        per_learner_spend_cap_learner_credit_access_policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory()
        per_learner_spend_cap_learner_credit_access_policy.per_learner_spend_limit = 500
        self.assertEqual(
            per_learner_spend_cap_learner_credit_access_policy.can_redeem(self.user, self.course_id),
            can_redeem
            )

    @ddt.data(
        (True, True, True, 4991, False),
        (True, True, True, 4990, False),
        (True, True, True, 4989, True)
    )
    @ddt.unpack
    def test_capped_enrollment_learner_credit_access_policy_can_redeem(
        self,
        catalog_contains_content,
        group_contains_learner,
        is_redeemable,
        amount_spent_for_group_and_catalog,
        can_redeem,
        mock_catalog_client,
        mock_subsidy_client,
        mock_group_client
        ):
        """
        Test the can_redeem method of CappedEnrollmentLearnerCreditAccessPolicy model
        """
        mock_catalog_client.catalog_contains_content.return_value = catalog_contains_content
        mock_catalog_client.get_course_price.return_value = 10
        mock_group_client.group_contains_learner.return_value = group_contains_learner
        mock_subsidy_client.can_redeem.return_value = is_redeemable
        mock_subsidy_client.amount_spent_for_group_and_catalog.return_value = amount_spent_for_group_and_catalog
        capped_enrollment_learner_credit_access_policy = CappedEnrollmentLearnerCreditAccessPolicyFactory()
        capped_enrollment_learner_credit_access_policy.spend_limit = 5000
        self.assertEqual(
            capped_enrollment_learner_credit_access_policy.can_redeem(self.user, self.course_id),
            can_redeem
            )
