""" Tests for subsidy_access_policy models. """
from unittest.mock import patch
from uuid import uuid4

import ddt
import factory
from django.test import TestCase

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.subsidy_access_policy.constants import AccessMethods
from enterprise_access.apps.subsidy_access_policy.models import (
    CappedEnrollmentLearnerCreditAccessPolicy,
    PerLearnerEnrollmentCreditAccessPolicy,
    PerLearnerSpendCreditAccessPolicy,
    SubscriptionAccessPolicy,
    SubsidyAccessPolicy
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    CappedEnrollmentLearnerCreditAccessPolicyFactory,
    PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory,
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory,
    SubscriptionAccessPolicyFactory
)


@ddt.ddt
@patch('enterprise_access.apps.subsidy_access_policy.models.group_client')
@patch('enterprise_access.apps.subsidy_access_policy.models.subsidy_client')
@patch('enterprise_access.apps.subsidy_access_policy.models.EnterpriseCatalogApiClient')
@patch('enterprise_access.apps.subsidy_access_policy.models.LmsApiClient')
@patch('enterprise_access.apps.subsidy_access_policy.models.DiscoveryApiClient')
class SubsidyAccessPolicyTests(TestCase):
    """ SubsidyAccessPolicy model tests. """

    user = factory.SubFactory(UserFactory)
    course_id = factory.LazyFunction(uuid4)

    def test_can_not_create_parent_model_object(self, *args):
        """
        Verify that correct exception raised when we try to create object of SubsidyAccessPolicy
        """
        with self.assertRaises(TypeError):
            SubsidyAccessPolicy.objects.create(
                description='Base policy',
                group_uuid='7c9daa69-519c-4313-ad81-90862bc08ca1',
                catalog_uuid='7c9daa69-519c-4313-ad81-90862bc08c21',
                subsidy_uuid='7c9daa69-519c-4313-ad81-90862bc08ca3',
            )

    def test_parent_model_queryset_has_correct_policy_type_objects(self, *args):
        """
        Verify that correct parent model queryset has policy type objects.
        """
        valid_policy_types = [
            'PerLearnerSpendCreditAccessPolicy',
            'PerLearnerEnrollmentCreditAccessPolicy',
            'CappedEnrollmentLearnerCreditAccessPolicy',
            'SubscriptionAccessPolicy',
        ]

        PerLearnerSpendCreditAccessPolicy.objects.create(
            group_uuid='7c9daa69-519c-4313-ad81-90862bc08ca1',
            catalog_uuid='7c9daa69-519c-4313-ad81-90862bc08ca2',
            subsidy_uuid='7c9daa69-519c-4313-ad81-90862bc08ca3'
        )
        PerLearnerEnrollmentCreditAccessPolicy.objects.create(
            group_uuid='7c9daa69-519c-4313-ad81-90862bc08ca2',
            catalog_uuid='7c9daa69-519c-4313-ad81-90862bc08ca3',
            subsidy_uuid='7c9daa69-519c-4313-ad81-90862bc08ca4'
        )
        CappedEnrollmentLearnerCreditAccessPolicy.objects.create(
            group_uuid='7c9daa69-519c-4313-ad81-90862bc08ca3',
            catalog_uuid='7c9daa69-519c-4313-ad81-90862bc08ca4',
            subsidy_uuid='7c9daa69-519c-4313-ad81-90862bc08ca5'
        )
        SubscriptionAccessPolicy.objects.create(
            group_uuid='7c9daa69-519c-4313-ad81-90862bc08ca4',
            catalog_uuid='7c9daa69-519c-4313-ad81-90862bc08ca5',
            subsidy_uuid='7c9daa69-519c-4313-ad81-90862bc08ca6'
        )

        created_policy_types = []
        all_policies = SubsidyAccessPolicy.objects.all()
        for policy in all_policies:
            created_policy_types.append(policy.__class__.__name__)

        self.assertEqual(
            sorted(created_policy_types),
            sorted(valid_policy_types)
        )

    def test_object_creation_with_policy_type_in_kwarg(self, *args):
        """
        Verify that correct policy object has been created with policy type in kwarg.
        """
        expected_policy_type = 'PerLearnerSpendCreditAccessPolicy'

        policy = SubsidyAccessPolicy.objects.create(
            group_uuid='7c9daa69-519c-4313-ad81-90862bc08ca1',
            catalog_uuid='7c9daa69-519c-4313-ad81-90862bc08ca2',
            subsidy_uuid='7c9daa69-519c-4313-ad81-90862bc08ca3',
            policy_type=expected_policy_type
        )

        self.assertEqual(policy.__class__.__name__, expected_policy_type)

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
    def test_subscription_access_policy_can_redeem(
        self,
        catalog_contains_content,
        enterprise_contains_learner,
        is_redeemable,
        is_license_for_learner,
        is_license_for_group,
        can_redeem_via_learner_license,
        can_redeem_via_group_license,
        mock_discovery_client,
        mock_lms_client,
        mock_catalog_client,
        mock_subsidy_client,
        mock_group_client,
    ):
        """
        Test the can_redeem method of SubscriptionAccessPolicy model
        """
        mock_catalog_client_instance = mock_catalog_client.return_value
        mock_catalog_client_instance.contains_content_items.return_value = catalog_contains_content
        mock_discovery_client_instance = mock_discovery_client.return_value
        mock_discovery_client_instance.get_course_price.return_value = 10
        mock_lms_client_instance = mock_lms_client.return_value
        mock_lms_client_instance.enterprise_contains_learner.return_value = enterprise_contains_learner
        mock_subsidy_client.can_redeem.return_value = is_redeemable
        mock_subsidy_client.get_license_for_learner.return_value = is_license_for_learner
        subscription_access_policy = SubscriptionAccessPolicyFactory()
        self.assertEqual(
            subscription_access_policy.can_redeem(self.user, self.course_id),
            can_redeem_via_learner_license
        )
        # test for redemption via group license
        mock_subsidy_client.get_license_for_learner.return_value = False
        subscription_access_policy.group_uuid = 'test-uuid'
        mock_group_client.get_groups_for_learner.return_value = [subscription_access_policy.group_uuid]
        mock_subsidy_client.get_license_for_group.return_value = is_license_for_group
        self.assertEqual(
            subscription_access_policy.can_redeem(self.user, self.course_id),
            can_redeem_via_group_license
        )

    def test_subscription_access_policy_redeem_with_invalid_access_method(self, *args):
        """
        Test the redeem method of SubscriptionAccessPolicy.redeem method returns None for invalid access method.
        """
        subscription_access_policy = SubscriptionAccessPolicyFactory(access_method=AccessMethods.ASSIGNED)
        self.assertIsNone(subscription_access_policy.redeem(self.user, self.course_id))

    @ddt.data(
        (True, True, False, None, None),
        (True, True, True, 999, 999)
    )
    @ddt.unpack
    def test_subscription_access_policy_redeem(
        self,
        catalog_contains_content,
        enterprise_contains_learner,
        is_redeemable,
        ledger_transaction_id,
        redeem_return_value,
        mock_discovery_client,  # lint-amnesty, pylint: disable=unused-argument
        mock_lms_client,
        mock_catalog_client,
        mock_subsidy_client,
        *args
    ):
        """
        Test the redeem method of SubscriptionAccessPolicy model
        """
        mock_catalog_client.catalog_contains_content.return_value = catalog_contains_content
        mock_lms_client_instance = mock_lms_client.return_value
        mock_lms_client_instance.enterprise_contains_learner.return_value = enterprise_contains_learner
        mock_subsidy_client.can_redeem.return_value = is_redeemable
        mock_subsidy_client.redeem.return_value = ledger_transaction_id
        subscription_access_policy = SubscriptionAccessPolicyFactory()
        self.assertEqual(subscription_access_policy.redeem(self.user, self.course_id), redeem_return_value)

    @ddt.data(
        (True, True, False, None, None),
        (True, True, True, 999, 999)
    )
    @ddt.unpack
    def test_subscription_request_access_policy_redeem(
        self,
        catalog_contains_content,
        enterprise_contains_learner,
        is_redeemable,
        ledger_transaction_id,
        request_redemption_return_value,
        mock_discovery_client,  # lint-amnesty, pylint: disable=unused-argument
        mock_lms_client,
        mock_catalog_client,
        mock_subsidy_client,
        *args
    ):
        """
        Test the redeem method of SubscriptionAccessPolicy model
        """
        mock_catalog_client.catalog_contains_content.return_value = catalog_contains_content
        mock_lms_client_instance = mock_lms_client.return_value
        mock_lms_client_instance.enterprise_contains_learner.return_value = enterprise_contains_learner
        mock_subsidy_client.can_redeem.return_value = is_redeemable
        mock_subsidy_client.request_redemption.return_value = ledger_transaction_id
        subscription_access_policy = SubscriptionAccessPolicyFactory(access_method=AccessMethods.REQUEST)
        self.assertEqual(
            subscription_access_policy.redeem(self.user, self.course_id),
            request_redemption_return_value
        )

    def test_subscription_access_policy_has_redeemed(
        self,
        mock_discovery_client,  # lint-amnesty, pylint: disable=unused-argument
        mock_lms_client,  # lint-amnesty, pylint: disable=unused-argument
        mock_catalog_client,  # lint-amnesty, pylint: disable=unused-argument
        mock_subsidy_client,
        *args
    ):
        """
        Test the has_redeemed method of SubscriptionAccessPolicy model
        """
        subscription_access_policy = SubscriptionAccessPolicyFactory()
        mock_subsidy_client.has_redeemed.return_value = True
        self.assertTrue(subscription_access_policy.has_redeemed(self.user, self.course_id))

        subscription_access_policy.access_method = 'unknown_test_method'
        with self.assertRaises(ValueError):
            subscription_access_policy.has_redeemed(self.user, self.course_id)

    def test_subscription_request_access_policy_has_redeemed(
        self,
        mock_discovery_client,  # lint-amnesty, pylint: disable=unused-argument
        mock_lms_client,  # lint-amnesty, pylint: disable=unused-argument
        mock_catalog_client,  # lint-amnesty, pylint: disable=unused-argument
        mock_subsidy_client,
        *args
    ):
        """
        Test the has_redeemed method of LicenseRequestAccessPolicy model
        """
        subscription_request_access_policy = SubscriptionAccessPolicyFactory(access_method=AccessMethods.REQUEST)
        mock_subsidy_client.has_requested.return_value = True
        self.assertTrue(subscription_request_access_policy.has_redeemed(self.user, self.course_id))

        subscription_request_access_policy.access_method = 'unknown_test_method'
        with self.assertRaises(ValueError):
            subscription_request_access_policy.has_redeemed(self.user, self.course_id)

    @ddt.data(
        (True, True, True, 6, False),
        (True, True, True, 5, False),
        (True, True, True, 4, True)
    )
    @ddt.unpack
    def test_per_learner_enrollment_cap_learner_credit_access_policy_can_redeem(
        self,
        catalog_contains_content,
        enterprise_contains_learner,
        is_redeemable,
        transactions_for_learner,
        can_redeem,
        mock_discovery_client,  # lint-amnesty, pylint: disable=unused-argument
        mock_lms_client,
        mock_catalog_client,
        mock_subsidy_client,
        *args
    ):
        """
        Test the can_redeem method of PerLearnerEnrollmentCapLearnerCreditAccessPolicy model
        """
        mock_catalog_client.catalog_contains_content.return_value = catalog_contains_content
        mock_lms_client_instance = mock_lms_client.return_value
        mock_lms_client_instance.enterprise_contains_learner.return_value = enterprise_contains_learner
        mock_subsidy_client.can_redeem.return_value = is_redeemable
        mock_subsidy_client.transactions_for_learner.return_value = transactions_for_learner

        per_learner_enrollment_cap_learner_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory()
        per_learner_enrollment_cap_learner_policy.per_learner_enrollment_limit = 5
        self.assertEqual(
            per_learner_enrollment_cap_learner_policy.can_redeem(self.user, self.course_id),
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
        enterprise_contains_learner,
        is_redeemable,
        amount_spent_for_learner,
        can_redeem,
        mock_discovery_client,
        mock_lms_client,
        mock_catalog_client,
        mock_subsidy_client,
        *args
    ):
        """
        Test the can_redeem method of PerLearnerSpendCapLearnerCreditAccessPolicy model
        """
        mock_catalog_client.catalog_contains_content.return_value = catalog_contains_content
        mock_discovery_client_instance = mock_discovery_client.return_value
        mock_discovery_client_instance.get_course_price.return_value = 10
        mock_lms_client_instance = mock_lms_client.return_value
        mock_lms_client_instance.enterprise_contains_learner.return_value = enterprise_contains_learner
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
        enterprise_contains_learner,
        is_redeemable,
        amount_spent_for_group_and_catalog,
        can_redeem,
        mock_discovery_client,
        mock_lms_client,
        mock_catalog_client,
        mock_subsidy_client,
        *args
    ):
        """
        Test the can_redeem method of CappedEnrollmentLearnerCreditAccessPolicyFactory model
        """
        mock_discovery_client_instance = mock_discovery_client.return_value
        mock_discovery_client_instance.get_course_price.return_value = 10
        mock_catalog_client.catalog_contains_content.return_value = catalog_contains_content
        mock_lms_client_instance = mock_lms_client.return_value
        mock_lms_client_instance.enterprise_contains_learner.return_value = enterprise_contains_learner
        mock_subsidy_client.can_redeem.return_value = is_redeemable
        mock_subsidy_client.amount_spent_for_group_and_catalog.return_value = amount_spent_for_group_and_catalog
        capped_enrollment_learner_credit_access_policy = CappedEnrollmentLearnerCreditAccessPolicyFactory()
        capped_enrollment_learner_credit_access_policy.spend_limit = 5000
        self.assertEqual(
            capped_enrollment_learner_credit_access_policy.can_redeem(self.user, self.course_id),
            can_redeem
        )
