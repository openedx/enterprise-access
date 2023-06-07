"""
Tests for subsidy_access_policy models.
"""
from unittest.mock import patch
from uuid import uuid4

import ddt
import factory
import pytest
from django.core.cache import cache as django_cache
from django.test import TestCase

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.subsidy_access_policy.constants import (
    REASON_CONTENT_NOT_IN_CATALOG,
    REASON_LEARNER_MAX_ENROLLMENTS_REACHED,
    REASON_LEARNER_MAX_SPEND_REACHED,
    REASON_LEARNER_NOT_IN_ENTERPRISE,
    REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY,
    REASON_POLICY_NOT_ACTIVE,
    REASON_POLICY_SPEND_LIMIT_REACHED
)
from enterprise_access.apps.subsidy_access_policy.models import (
    PerLearnerEnrollmentCreditAccessPolicy,
    PerLearnerSpendCreditAccessPolicy,
    SubsidyAccessPolicy,
    SubsidyAccessPolicyLockAttemptFailed
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory,
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory
)

ACTIVE_LEARNER_SPEND_CAP_POLICY_UUID = uuid4()
ACTIVE_LEARNER_ENROLL_CAP_POLICY_UUID = uuid4()


@ddt.ddt
class SubsidyAccessPolicyTests(TestCase):
    """ SubsidyAccessPolicy model tests. """

    user = factory.SubFactory(UserFactory)
    course_id = factory.LazyFunction(uuid4)

    def setUp(self):
        """
        Initialize mocked service clients.
        """
        super().setUp()
        subsidy_client_patcher = patch.object(
            SubsidyAccessPolicy, 'subsidy_client'
        )
        self.mock_subsidy_client = subsidy_client_patcher.start()

        transactions_cache_for_learner_patcher = patch(
            'enterprise_access.apps.subsidy_access_policy.models.get_and_cache_transactions_for_learner'
        )
        self.mock_transactions_cache_for_learner = transactions_cache_for_learner_patcher.start()

        catalog_contains_content_key_patcher = patch.object(
            SubsidyAccessPolicy, 'catalog_contains_content_key'
        )
        self.mock_catalog_contains_content_key = catalog_contains_content_key_patcher.start()

        get_content_metadata_patcher = patch.object(
            SubsidyAccessPolicy, 'get_content_metadata'
        )
        self.mock_get_content_metadata = get_content_metadata_patcher.start()

        lms_api_client_patcher = patch.object(
            SubsidyAccessPolicy, 'lms_api_client'
        )
        self.mock_lms_api_client = lms_api_client_patcher.start()

        self.addCleanup(subsidy_client_patcher.stop)
        self.addCleanup(transactions_cache_for_learner_patcher.stop)
        self.addCleanup(catalog_contains_content_key_patcher.stop)
        self.addCleanup(get_content_metadata_patcher.stop)
        self.addCleanup(lms_api_client_patcher.stop)
        self.addCleanup(django_cache.clear)  # clear any leftover policy locks.

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.per_learner_enroll_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            uuid=ACTIVE_LEARNER_ENROLL_CAP_POLICY_UUID,
            per_learner_enrollment_limit=5,
            spend_limit=10000,
        )
        cls.inactive_per_learner_enroll_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            per_learner_enrollment_limit=5,
            active=False,
        )
        cls.per_learner_spend_policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            uuid=ACTIVE_LEARNER_SPEND_CAP_POLICY_UUID,
            per_learner_spend_limit=500,
            spend_limit=10000
        )
        cls.inactive_per_learner_spend_policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            per_learner_spend_limit=500,
            active=False,
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
            # existing transactions for learner and policy below the policy limits.
            # Expected can_redeem result: True
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': {'can_redeem': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {'total_quantity': -100}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (True, None, []),
        },
        {
            # Content not in catalog, every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': False,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': {'can_redeem': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_CONTENT_NOT_IN_CATALOG, []),
        },
        {
            # Learner is not in the enterprise, every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enterprise_contains_learner': False,
            'subsidy_is_redeemable': {'can_redeem': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_LEARNER_NOT_IN_ENTERPRISE, []),
        },
        {
            # The subsidy is not redeemable, every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': {'can_redeem': False},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY, []),
        },
        {
            # The subsidy is redeemable, but the learner has already enrolled more than the limit.
            # Every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': {'can_redeem': True},
            'transactions_for_learner': {
                'transactions': [{
                    'subsidy_access_policy_uuid': str(ACTIVE_LEARNER_ENROLL_CAP_POLICY_UUID),
                    'uuid': str(uuid4()),
                    'content_key': 'anything',
                    'quantity': -5,
                } for _ in range(10)],
                'aggregates': {'total_quantity': -100},
            },
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_LEARNER_MAX_ENROLLMENTS_REACHED, []),
        },
        {
            # The subsidy is redeemable, but another redemption would exceed the policy-wide ``spend_limit``.
            # Every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': {'can_redeem': True},
            'transactions_for_learner': {
                'transactions': [{
                    'subsidy_access_policy_uuid': str(ACTIVE_LEARNER_ENROLL_CAP_POLICY_UUID),
                    'uuid': str(uuid4()),
                    'content_key': 'anything',
                    'quantity': -5,
                } for _ in range(3)],
                'aggregates': {'total_quantity': -100},
            },
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -10001}},
            'expected_policy_can_redeem': (False, REASON_POLICY_SPEND_LIMIT_REACHED, []),
        },
        {
            # The subsidy access policy is not active, every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': False,
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': {'can_redeem': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_POLICY_NOT_ACTIVE, []),
        },
    )
    @ddt.unpack
    def test_learner_enrollment_cap_policy_can_redeem(
        self,
        policy_is_active,
        catalog_contains_content,
        enterprise_contains_learner,
        subsidy_is_redeemable,
        transactions_for_learner,
        transactions_for_policy,
        expected_policy_can_redeem,
    ):
        """
        Test the can_redeem method of PerLearnerEnrollmentCapLearnerCreditAccessPolicy model
        """
        self.mock_lms_api_client.enterprise_contains_learner.return_value = enterprise_contains_learner
        self.mock_catalog_contains_content_key.return_value = catalog_contains_content
        self.mock_get_content_metadata.return_value = {
            'content_price': 200,
        }
        self.mock_subsidy_client.can_redeem.return_value = subsidy_is_redeemable
        self.mock_transactions_cache_for_learner.return_value = transactions_for_learner
        self.mock_subsidy_client.list_subsidy_transactions.return_value = transactions_for_policy

        policy_record = self.inactive_per_learner_enroll_policy
        if policy_is_active:
            policy_record = self.per_learner_enroll_policy

        can_redeem_result = policy_record.can_redeem(self.user, self.course_id)

        self.assertEqual(can_redeem_result, expected_policy_can_redeem, [])

    @ddt.data(
        {
            # Happy path: content in catalog, learner in enterprise, subsidy has value,
            # existing transactions for learner below the policy limit.
            # Expected can_redeem result: True
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': {'can_redeem': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {'total_quantity': -100}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (True, None, []),
        },
        {
            # Content not in catalog, every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': False,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': {'can_redeem': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_CONTENT_NOT_IN_CATALOG, []),
        },
        {
            # Learner is not in the enterprise, every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enterprise_contains_learner': False,
            'subsidy_is_redeemable': {'can_redeem': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_LEARNER_NOT_IN_ENTERPRISE, []),
        },
        {
            # The subsidy is not redeemable, every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': {'can_redeem': False},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY, []),
        },
        {
            # The subsidy is redeemable, but the learner has already enrolled more than the limit.
            # Every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': {'can_redeem': True},
            'transactions_for_learner': {
                'transactions': [{
                    'subsidy_access_policy_uuid': str(ACTIVE_LEARNER_SPEND_CAP_POLICY_UUID),
                    'uuid': str(uuid4()),
                    'content_key': 'anything',
                    'quantity': -50000,
                }],
                'aggregates': {'total_quantity': -50000}
            },
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_LEARNER_MAX_SPEND_REACHED, []),
        },
        {
            # The subsidy is redeemable, but another redemption would exceed the policy-wide ``spend_limit``.
            # Every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': {'can_redeem': True},
            'transactions_for_learner': {
                'transactions': [{
                    'subsidy_access_policy_uuid': str(ACTIVE_LEARNER_SPEND_CAP_POLICY_UUID),
                    'uuid': str(uuid4()),
                    'content_key': 'anything',
                    'quantity': 100,
                }],
                'aggregates': {'total_quantity': -100}
            },
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -15000}},
            'expected_policy_can_redeem': (False, REASON_POLICY_SPEND_LIMIT_REACHED, []),
        },
        {
            # The subsidy access policy is not active, every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': False,
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': {'can_redeem': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_POLICY_NOT_ACTIVE, []),
        },
    )
    @ddt.unpack
    def test_learner_spend_cap_policy_can_redeem(
        self,
        policy_is_active,
        catalog_contains_content,
        enterprise_contains_learner,
        subsidy_is_redeemable,
        transactions_for_learner,
        transactions_for_policy,
        expected_policy_can_redeem,
    ):
        """
        Test the can_redeem method of PerLearnerSpendCapLearnerCreditAccessPolicy model
        """
        self.mock_lms_api_client.enterprise_contains_learner.return_value = enterprise_contains_learner
        self.mock_catalog_contains_content_key.return_value = catalog_contains_content
        self.mock_get_content_metadata.return_value = {
            'content_price': 200,
        }
        self.mock_subsidy_client.can_redeem.return_value = subsidy_is_redeemable
        self.mock_transactions_cache_for_learner.return_value = transactions_for_learner
        self.mock_subsidy_client.list_subsidy_transactions.return_value = transactions_for_policy

        policy_record = self.inactive_per_learner_spend_policy
        if policy_is_active:
            policy_record = self.per_learner_spend_policy

        can_redeem_result = policy_record.can_redeem(self.user, self.course_id)

        self.assertEqual(can_redeem_result, expected_policy_can_redeem)

    def test_acquire_lock_release_lock_no_kwargs(self):
        """
        Create one hypothetical sequence consisting of three actors and two policies.  Each policy should only allow one
        lock to be grabbed at a time.
        """
        # Simple case, acquire lock on first policy.
        lock_1 = self.per_learner_enroll_policy.acquire_lock()
        assert lock_1  # Non-null means the lock was successfully acquired.
        # A second actor attempts to acquire lock on first policy, but it's already locked.
        lock_2 = self.per_learner_enroll_policy.acquire_lock()
        assert lock_2 is None
        # A third actor attempts to acquire lock on second policy, should work even though first policy is locked.
        lock_3 = self.per_learner_spend_policy.acquire_lock()
        assert lock_3
        assert lock_3 != lock_1
        # After releasing the first lock, the second actor should have success.
        self.per_learner_enroll_policy.release_lock()
        lock_2 = self.per_learner_enroll_policy.acquire_lock()
        assert lock_2
        # Finally, the third actor releases the lock on the second policy.
        self.per_learner_spend_policy.release_lock()

    def test_acquire_lock_release_lock_with_kwargs(self):
        """
        Create one hypothetical sequence consisting two actors trying to lock the same policy, but the locks are
        acquired with kwargs that prevent lock contention.  This simulates a per-learner cap (either spend or enroll
        cap) rather than a per-policy cap.
        """
        user_1_lock_1 = self.per_learner_enroll_policy.acquire_lock(lms_user_id=1)
        assert user_1_lock_1  # Non-null means the lock was successfully acquired.
        user_2_lock = self.per_learner_enroll_policy.acquire_lock(lms_user_id=2)
        assert user_2_lock
        user_1_lock_2 = self.per_learner_enroll_policy.acquire_lock(lms_user_id=1)
        assert user_1_lock_2 is None
        self.per_learner_enroll_policy.release_lock(lms_user_id=1)
        user_1_lock_2 = self.per_learner_enroll_policy.acquire_lock(lms_user_id=1)
        assert user_1_lock_2
        self.per_learner_enroll_policy.release_lock(lms_user_id=2)
        self.per_learner_enroll_policy.release_lock(lms_user_id=1)

    def test_lock_contextmanager_happy(self):
        """
        Ensure the lock contextmanager does not raise an exception if the policy is not locked.
        """
        with self.per_learner_enroll_policy.lock():
            pass

    def test_lock_contextmanager_already_locked(self):
        """
        Ensure the lock contextmanager raises SubsidyAccessPolicyLockAttemptFailed if the policy is locked.
        """
        self.per_learner_enroll_policy.acquire_lock()
        with pytest.raises(SubsidyAccessPolicyLockAttemptFailed, match=r"Failed to acquire lock.*"):
            with self.per_learner_enroll_policy.lock():
                pass
        self.per_learner_enroll_policy.release_lock()

    def test_content_would_exceed_limit_positive_spent_amount(self):
        """
        Ensures that passing a positive spent_amount will raise an exception.
        """
        with self.assertRaisesRegex(Exception, 'Expected a sum of transaction quantities <= 0'):
            self.per_learner_enroll_policy.content_would_exceed_limit(10, 100, 15)
