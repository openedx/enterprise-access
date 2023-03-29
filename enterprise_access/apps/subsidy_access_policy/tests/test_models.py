""" Tests for subsidy_access_policy models. """
from unittest.mock import patch
from uuid import uuid4

import ddt
import factory
from django.test import TestCase

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.subsidy_access_policy.models import (
    PerLearnerEnrollmentCreditAccessPolicy,
    PerLearnerSpendCreditAccessPolicy,
    SubsidyAccessPolicy
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory,
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory
)


@ddt.ddt
class SubsidyAccessPolicyTests(TestCase):
    """ SubsidyAccessPolicy model tests. """

    user = factory.SubFactory(UserFactory)
    course_id = factory.LazyFunction(uuid4)

    def setUp(self):
        """
        It's really important that our mocked client
        instances get reset before each test,
        because we cache those instances
        in the SubsidyAccessPolicy class to reduce instantiation overhead;
        and we have singleton SubsidyAccesPolicy instances declared
        for this test class.
        """
        super().setUp()
        self.subsidy_client_patcher = patch.object(
            SubsidyAccessPolicy, 'subsidy_client'
        )
        self.mock_subsidy_client = self.subsidy_client_patcher.start()
        self.catalog_client_patcher = patch.object(
            SubsidyAccessPolicy, 'catalog_client'
        )
        self.mock_catalog_client = self.catalog_client_patcher.start()
        self.lms_api_client_patcher = patch.object(
            SubsidyAccessPolicy, 'lms_api_client'
        )
        self.mock_lms_api_client = self.lms_api_client_patcher.start()

    def tearDown(self):
        super().tearDown()
        self.subsidy_client_patcher.stop()
        self.catalog_client_patcher.stop()
        self.lms_api_client_patcher.stop()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.per_learner_enroll_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            per_learner_enrollment_limit=5,
        )
        cls.per_learner_spend_policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            per_learner_spend_limit=500
        )

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
        valid_policy_types = {
            'PerLearnerSpendCreditAccessPolicy',
            'PerLearnerEnrollmentCreditAccessPolicy',
        }

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

        created_policy_types = set()
        all_policies = SubsidyAccessPolicy.objects.all()
        for policy in all_policies:
            created_policy_types.add(policy.__class__.__name__)

        self.assertEqual(valid_policy_types, created_policy_types)

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
        {
            # Happy path: content in catalog, learner in enterprise, subsidy has value,
            # existing transactions for learner below the policy limit.
            # Expected can_redeem result: True
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': True,
            'transactions_for_learner': {'results': [], 'aggregates': {'total_quantity': 100}},
            'expected_policy_can_redeem': (True, None),
        },
        {
            # Content not in catalog, every other check would succeed.
            # Expected can_redeem result: False
            'catalog_contains_content': False,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': True,
            'transactions_for_learner': {'results': [], 'aggregates': {}},
            'expected_policy_can_redeem': (False, "Requested content_key not contained in policy's catalog."),
        },
        {
            # Learner is not in the enterprise, every other check would succeed.
            # Expected can_redeem result: False
            'catalog_contains_content': True,
            'enterprise_contains_learner': False,
            'subsidy_is_redeemable': True,
            'transactions_for_learner': {'results': [], 'aggregates': {}},
            'expected_policy_can_redeem': (False, 'Learner not part of enterprise associated with the access policy.'),
        },
        {
            # The subsidy is not redeemable, every other check would succeed.
            # Expected can_redeem result: False
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': False,
            'transactions_for_learner': {'results': [], 'aggregates': {}},
            'expected_policy_can_redeem': (False, 'Not enough remaining value in subsidy to redeem requested content.'),
        },
        {
            # The subsidy is redeemable, but the learner has already enrolled more than the limit.
            # Every other check would succeed.
            # Expected can_redeem result: False
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': True,
            'transactions_for_learner': {
                'results': [{'foo': 'bar'} for _ in range(10)],
                'aggregates': {'total_quantity': 100}
            },
            'expected_policy_can_redeem': (
                False,
                "The learner's maximum number of enrollments given by this subsidy access policy has been reached."
            ),
        },
    )
    @ddt.unpack
    def test_learner_enrollment_cap_policy_can_redeem(
        self,
        catalog_contains_content,
        enterprise_contains_learner,
        subsidy_is_redeemable,
        transactions_for_learner,
        expected_policy_can_redeem,
    ):
        """
        Test the can_redeem method of PerLearnerEnrollmentCapLearnerCreditAccessPolicy model
        """
        self.mock_catalog_client.contains_content_items.return_value = catalog_contains_content
        self.mock_lms_api_client.enterprise_contains_learner.return_value = enterprise_contains_learner
        self.mock_subsidy_client.can_redeem.return_value = subsidy_is_redeemable
        self.mock_subsidy_client.list_subsidy_transactions.return_value = transactions_for_learner
        self.mock_subsidy_client.get_subsidy_content_data.return_value = {
            'content_price': 200,
        }

        self.assertEqual(
            self.per_learner_enroll_policy.can_redeem(self.user, self.course_id),
            expected_policy_can_redeem
        )

    @ddt.data(
        {
            # Happy path: content in catalog, learner in enterprise, subsidy has value,
            # existing transactions for learner below the policy limit.
            # Expected can_redeem result: True
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': True,
            'transactions_for_learner': {'results': [], 'aggregates': {'total_quantity': 100}},
            'expected_policy_can_redeem': (True, None),
        },
        {
            # Content not in catalog, every other check would succeed.
            # Expected can_redeem result: False
            'catalog_contains_content': False,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': True,
            'transactions_for_learner': {'results': [], 'aggregates': {}},
            'expected_policy_can_redeem': (False, "Requested content_key not contained in policy's catalog."),
        },
        {
            # Learner is not in the enterprise, every other check would succeed.
            # Expected can_redeem result: False
            'catalog_contains_content': True,
            'enterprise_contains_learner': False,
            'subsidy_is_redeemable': True,
            'transactions_for_learner': {'results': [], 'aggregates': {}},
            'expected_policy_can_redeem': (False, 'Learner not part of enterprise associated with the access policy.'),
        },
        {
            # The subsidy is not redeemable, every other check would succeed.
            # Expected can_redeem result: False
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': False,
            'transactions_for_learner': {'results': [], 'aggregates': {}},
            'expected_policy_can_redeem': (False, 'Not enough remaining value in subsidy to redeem requested content.'),
        },
        {
            # The subsidy is redeemable, but the learner has already enrolled more than the limit.
            # Every other check would succeed.
            # Expected can_redeem result: False
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': True,
            'transactions_for_learner': {
                'results': [{'foo': 'bar'}],
                'aggregates': {'total_quantity': 50000}
            },
            'expected_policy_can_redeem': (
                False,
                "The learner's maximum spend in this subsidy access policy has been reached."
            ),
        },
    )
    @ddt.unpack
    def test_learner_spend_cap_policy_can_redeem(
        self,
        catalog_contains_content,
        enterprise_contains_learner,
        subsidy_is_redeemable,
        transactions_for_learner,
        expected_policy_can_redeem,
    ):
        """
        Test the can_redeem method of PerLearnerSpendCapLearnerCreditAccessPolicy model
        """
        self.mock_catalog_client.contains_content_items.return_value = catalog_contains_content
        self.mock_lms_api_client.enterprise_contains_learner.return_value = enterprise_contains_learner
        self.mock_subsidy_client.can_redeem.return_value = subsidy_is_redeemable
        self.mock_subsidy_client.list_subsidy_transactions.return_value = transactions_for_learner
        self.mock_subsidy_client.get_subsidy_content_data.return_value = {
            'content_price': 200,
        }

        self.assertEqual(
            self.per_learner_spend_policy.can_redeem(self.user, self.course_id),
            expected_policy_can_redeem
        )
