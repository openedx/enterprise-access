"""
Tests for subsidy_access_policy models.
"""
from datetime import datetime, timedelta
from unittest.mock import ANY, PropertyMock, patch
from uuid import uuid4

import ddt
import pytest
import requests
from django.conf import settings
from django.core.cache import cache as django_cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from enterprise_access.apps.content_assignments.constants import (
    AssignmentActionErrors,
    AssignmentActions,
    LearnerContentAssignmentStateChoices
)
from enterprise_access.apps.content_assignments.models import AssignmentConfiguration
from enterprise_access.apps.content_assignments.tests.factories import LearnerContentAssignmentFactory
from enterprise_access.apps.subsidy_access_policy.constants import (
    REASON_CONTENT_NOT_IN_CATALOG,
    REASON_LEARNER_ASSIGNMENT_CANCELLED,
    REASON_LEARNER_ASSIGNMENT_FAILED,
    REASON_LEARNER_MAX_ENROLLMENTS_REACHED,
    REASON_LEARNER_MAX_SPEND_REACHED,
    REASON_LEARNER_NOT_ASSIGNED_CONTENT,
    REASON_LEARNER_NOT_IN_ENTERPRISE,
    REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY,
    REASON_POLICY_EXPIRED,
    REASON_POLICY_SPEND_LIMIT_REACHED,
    REASON_SUBSIDY_EXPIRED
)
from enterprise_access.apps.subsidy_access_policy.exceptions import MissingAssignment, SubsidyAPIHTTPError
from enterprise_access.apps.subsidy_access_policy.models import (
    REQUEST_CACHE_NAMESPACE,
    AssignedLearnerCreditAccessPolicy,
    PerLearnerEnrollmentCreditAccessPolicy,
    PerLearnerSpendCreditAccessPolicy,
    SubsidyAccessPolicy,
    SubsidyAccessPolicyLockAttemptFailed
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    AssignedLearnerCreditAccessPolicyFactory,
    PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory,
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory
)
from enterprise_access.cache_utils import request_cache

from ..constants import AccessMethods
from ..exceptions import PriceValidationError

ACTIVE_LEARNER_SPEND_CAP_POLICY_UUID = uuid4()
ACTIVE_LEARNER_ENROLL_CAP_POLICY_UUID = uuid4()
ACTIVE_ASSIGNED_LEARNER_CREDIT_POLICY_UUID = uuid4()


class MockPolicyDependenciesMixin:
    """
    Mixin to help mock out all access policy dependencies
    on external services.
    """
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

        get_content_metadata_patcher = patch(
            'enterprise_access.apps.subsidy_access_policy.models.get_and_cache_content_metadata'
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


@ddt.ddt
class SubsidyAccessPolicyTests(MockPolicyDependenciesMixin, TestCase):
    """ SubsidyAccessPolicy model tests. """

    lms_user_id = 12345
    course_id = 'course-v1:DemoX+flossing'

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

    def tearDown(self):
        """
        Clears any cached data for the test policy instances between test runs.
        """
        super().tearDown()
        request_cache(namespace=REQUEST_CACHE_NAMESPACE).clear()

    def test_can_not_create_parent_model_object(self, *args):
        """
        Verify that correct exception raised when we try to create object of SubsidyAccessPolicy
        """
        with self.assertRaises(TypeError):
            SubsidyAccessPolicy.objects.create(
                description='Base policy',
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
            catalog_uuid='7c9daa69-519c-4313-ad81-90862bc08ca2',
            subsidy_uuid='7c9daa69-519c-4313-ad81-90862bc08ca3',
            per_learner_spend_limit=100,
            description='anything',
        )
        PerLearnerEnrollmentCreditAccessPolicy.objects.create(
            catalog_uuid='7c9daa69-519c-4313-ad81-90862bc08ca3',
            subsidy_uuid='7c9daa69-519c-4313-ad81-90862bc08ca4',
            per_learner_enrollment_limit=100,
            description='anything',
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
            catalog_uuid='7c9daa69-519c-4313-ad81-90862bc08ca2',
            subsidy_uuid='7c9daa69-519c-4313-ad81-90862bc08ca3',
            policy_type=expected_policy_type,
            description='anything',
            per_learner_spend_limit=500,
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
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
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
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_CONTENT_NOT_IN_CATALOG, []),
            'expect_content_metadata_fetch': False,
            'expect_transaction_fetch': False,
        },
        {
            # Learner is not in the enterprise, every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enterprise_contains_learner': False,
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_LEARNER_NOT_IN_ENTERPRISE, []),
            'expect_content_metadata_fetch': False,
            'expect_transaction_fetch': False,
        },
        {
            # The subsidy is not redeemable, every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': {'can_redeem': False, 'active': True},
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
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
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
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
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
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_POLICY_EXPIRED, []),
            'expect_content_metadata_fetch': False,
            'expect_transaction_fetch': False,
        },
        {
            # The subsidy is not active, every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': {'can_redeem': True, 'active': False},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_SUBSIDY_EXPIRED, []),
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
        expect_content_metadata_fetch=True,
        expect_transaction_fetch=True,
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

        can_redeem_result = policy_record.can_redeem(self.lms_user_id, self.course_id)

        self.assertEqual(can_redeem_result, expected_policy_can_redeem, [])

        if expect_content_metadata_fetch:
            # it's actually called twice
            self.mock_get_content_metadata.assert_called_with(policy_record.enterprise_customer_uuid, self.course_id)
        else:
            self.assertFalse(self.mock_get_content_metadata.called)

        if expect_transaction_fetch:
            self.mock_subsidy_client.can_redeem.assert_called_once_with(
                policy_record.subsidy_uuid,
                self.lms_user_id,
                self.course_id,
            )
        else:
            self.assertFalse(self.mock_subsidy_client.can_redeem.called)

    @ddt.data(
        {
            # Happy path: content in catalog, learner in enterprise, subsidy has value,
            # existing transactions for learner below the policy limit.
            # Expected can_redeem result: True
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
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
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_CONTENT_NOT_IN_CATALOG, []),
            'expect_content_metadata_fetch': False,
            'expect_transaction_fetch': False,
        },
        {
            # Learner is not in the enterprise, every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enterprise_contains_learner': False,
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_LEARNER_NOT_IN_ENTERPRISE, []),
            'expect_content_metadata_fetch': False,
            'expect_transaction_fetch': False,
        },
        {
            # The subsidy is not redeemable, every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': {'can_redeem': False, 'active': True},
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
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
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
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
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
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_POLICY_EXPIRED, []),
            'expect_content_metadata_fetch': False,
            'expect_transaction_fetch': False,
        },
        {
            # The subsidy is not active, every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enterprise_contains_learner': True,
            'subsidy_is_redeemable': {'can_redeem': True, 'active': False},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_SUBSIDY_EXPIRED, []),
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
        expect_content_metadata_fetch=True,
        expect_transaction_fetch=True,
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

        can_redeem_result = policy_record.can_redeem(self.lms_user_id, self.course_id)

        self.assertEqual(can_redeem_result, expected_policy_can_redeem)

        if expect_content_metadata_fetch:
            # it's actually called twice
            self.mock_get_content_metadata.assert_called_with(policy_record.enterprise_customer_uuid, self.course_id)
        else:
            self.assertFalse(self.mock_get_content_metadata.called)

        if expect_transaction_fetch:
            self.mock_subsidy_client.can_redeem.assert_called_once_with(
                policy_record.subsidy_uuid,
                self.lms_user_id,
                self.course_id,
            )
        else:
            self.assertFalse(self.mock_subsidy_client.can_redeem.called)

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

    def test_spend_limit_sum_and_content_price_equal_to_remaining_budget(self):
        """
        Ensures that passing a spent_amount equal to the remaining budget will return False.
        """
        self.assertFalse(self.per_learner_enroll_policy.content_would_exceed_limit(-90, 100, 10))

    def test_spend_limit_sum_and_content_price_less_than_remaining_budget(self):
        """
        Ensures that passing a spent_amount less than the remaining budget will return False.
        """
        self.assertFalse(self.per_learner_enroll_policy.content_would_exceed_limit(-90, 100, 5))

    def test_spend_limit_sum_and_content_price_greater_than_remaining_budget(self):
        """
        Ensures that passing a spent_amount equal to the remaining budget will return True.
        """
        self.assertTrue(self.per_learner_enroll_policy.content_would_exceed_limit(-90, 100, 11))

    def test_mock_subsidy_datetimes(self):
        yesterday = datetime.utcnow() - timedelta(days=1)
        tomorrow = datetime.utcnow() + timedelta(days=1)
        mock_subsidy = {
            'id': 123455,
            'active_datetime': yesterday,
            'expiration_datetime': tomorrow,
            'is_active': True,
        }
        self.mock_subsidy_client.retrieve_subsidy.return_value = mock_subsidy
        policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory.create()
        assert policy.subsidy_record() == mock_subsidy

        assert policy.subsidy_active_datetime == mock_subsidy.get('active_datetime')
        assert policy.subsidy_expiration_datetime == mock_subsidy.get('expiration_datetime')
        assert policy.is_subsidy_active == mock_subsidy.get('is_active')

    def test_subsidy_record_http_error(self):
        self.mock_subsidy_client.retrieve_subsidy.side_effect = requests.exceptions.HTTPError
        policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory.create()
        self.assertEqual(policy.subsidy_record(), {})
        self.assertIsNone(policy.subsidy_active_datetime)
        self.assertIsNone(policy.subsidy_expiration_datetime)
        self.assertIsNone(policy.is_subsidy_active)
        self.assertEqual(policy.subsidy_balance(), 0)


class SubsidyAccessPolicyResolverTests(TestCase):
    """ SubsidyAccessPolicy.resolve_policy() tests. """

    def setUp(self):
        """
        Initialize mocked service clients.
        """
        super().setUp()
        yesterday = datetime.utcnow() - timedelta(days=1)
        tomorrow = datetime.utcnow() + timedelta(days=1)
        day_after_tomorrow = datetime.utcnow() + timedelta(days=2)
        self.mock_subsidy_one = {
            'id': 1,
            'active_datetime': yesterday,
            'expiration_datetime': tomorrow,
            'is_active': True,
            'current_balance': 100,
        }
        self.mock_subsidy_two = {
            'id': 2,
            'active_datetime': yesterday,
            'expiration_datetime': tomorrow,
            'is_active': True,
            'current_balance': 50,
        }
        self.mock_subsidy_three = {
            'id': 3,
            'active_datetime': yesterday,
            'expiration_datetime': day_after_tomorrow,
            'is_active': True,
            'current_balance': 50,
        }
        self.mock_subsidy_four = {
            'id': 4,
            'active_datetime': yesterday,
            'expiration_datetime': tomorrow,
            'is_active': True,
            'current_balance': 100,
        }

        self.policy_one = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory.create()
        self.policy_two = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory.create()
        self.policy_three = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory.create()
        self.policy_four = PerLearnerSpendCapLearnerCreditAccessPolicyFactory.create()

        policy_one_subsity_patcher = patch.object(
            self.policy_one, 'subsidy_record'
        )
        self.mock_policy_one_subsidy_record = policy_one_subsity_patcher.start()
        self.mock_policy_one_subsidy_record.return_value = self.mock_subsidy_one

        policy_two_subsity_patcher = patch.object(
            self.policy_two, 'subsidy_record'
        )
        self.mock_policy_two_subsidy_record = policy_two_subsity_patcher.start()
        self.mock_policy_two_subsidy_record.return_value = self.mock_subsidy_two

        policy_three_subsity_patcher = patch.object(
            self.policy_three, 'subsidy_record'
        )
        self.mock_policy_three_subsidy_record = policy_three_subsity_patcher.start()
        self.mock_policy_three_subsidy_record.return_value = self.mock_subsidy_three

        policy_four_subsity_patcher = patch.object(
            self.policy_four, 'subsidy_record'
        )
        self.mock_policy_four_subsidy_record = policy_four_subsity_patcher.start()
        self.mock_policy_four_subsidy_record.return_value = self.mock_subsidy_four

        self.addCleanup(policy_one_subsity_patcher.stop)
        self.addCleanup(policy_two_subsity_patcher.stop)
        self.addCleanup(policy_three_subsity_patcher.stop)
        self.addCleanup(policy_four_subsity_patcher.stop)

    def test_setup(self):
        """
        Ensure each policy has the correctly mocked subsidy object
        """
        assert self.policy_one.subsidy_record() == self.mock_subsidy_one
        assert self.policy_two.subsidy_record() == self.mock_subsidy_two
        assert self.policy_three.subsidy_record() == self.mock_subsidy_three

    @override_settings(MULTI_POLICY_RESOLUTION_ENABLED=True)
    def test_resolve_one_policy(self):
        """
        Test resolve given a single policy
        """
        policies = [self.policy_one]
        assert SubsidyAccessPolicy.resolve_policy(policies) == self.policy_one

    @override_settings(MULTI_POLICY_RESOLUTION_ENABLED=True)
    def test_resolve_two_policies_by_balance(self):
        """
        Test resolve given a two policies with different balances, same expiration
        the smaller balance policy should be returned.
        """
        policies = [self.policy_one, self.policy_two]
        assert SubsidyAccessPolicy.resolve_policy(policies) == self.policy_two

    @override_settings(MULTI_POLICY_RESOLUTION_ENABLED=True)
    def test_resolve_two_policies_by_expiration(self):
        """
        Test resolve given a two policies with different balances, differet expiration
        the sooner expiration policy should be returned.
        """
        policies = [self.policy_one, self.policy_three]
        assert SubsidyAccessPolicy.resolve_policy(policies) == self.policy_one

    @override_settings(MULTI_POLICY_RESOLUTION_ENABLED=True)
    def test_resolve_two_policies_by_type_priority(self):
        """
        Test resolve given a two policies with same balances, same expiration
        but different type-priority.
        """
        policies = [self.policy_four, self.policy_one]
        # artificially set the priority attribute higher on one of the policies (lower priority takes precident)
        with patch.object(PerLearnerSpendCreditAccessPolicy, 'priority', new_callable=PropertyMock) as mock:
            mock.return_value = 100
            assert SubsidyAccessPolicy.resolve_policy(policies) == self.policy_one


@ddt.ddt
class AssignedLearnerCreditAccessPolicyTests(MockPolicyDependenciesMixin, TestCase):
    """ Tests specific to the assigned learner credit type of access policy. """

    lms_user_id = 12345
    course_key = 'DemoX+flossing'
    course_run_key = 'course-v1:DemoX+flossing+2T2023'

    def setUp(self):
        """
        Mocks out dependencies on other services, as well as dependencies
        on the Assignments API module.
        """
        super().setUp()

        self.assignments_api_patcher = patch(
            'enterprise_access.apps.subsidy_access_policy.models.assignments_api',
            autospec=True,
        )
        self.mock_assignments_api = self.assignments_api_patcher.start()
        self.addCleanup(self.assignments_api_patcher.stop)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.assignment_configuration = AssignmentConfiguration.objects.create()
        cls.active_policy = AssignedLearnerCreditAccessPolicyFactory(
            uuid=ACTIVE_ASSIGNED_LEARNER_CREDIT_POLICY_UUID,
            spend_limit=10000,
            assignment_configuration=cls.assignment_configuration,
        )
        cls.inactive_policy = AssignedLearnerCreditAccessPolicyFactory(
            active=False,
            spend_limit=10000,
            assignment_configuration=AssignmentConfiguration.objects.create(),
        )

    def tearDown(self):
        """
        Clears any cached data for the test policy instances between test runs.
        """
        super().tearDown()
        request_cache(namespace=REQUEST_CACHE_NAMESPACE).clear()

    def test_clean(self):
        """
        Tests the model-level validation of this policy type.
        """
        with self.assertRaisesRegex(ValidationError, 'must define a spend_limit'):
            AssignedLearnerCreditAccessPolicy(
                spend_limit=None,
                assignment_configuration=self.assignment_configuration,
            ).clean()
        with self.assertRaisesRegex(ValidationError, 'must not define a per-learner spend limit'):
            AssignedLearnerCreditAccessPolicy(
                spend_limit=1,
                assignment_configuration=self.assignment_configuration,
                per_learner_spend_limit=1,
            ).clean()
        with self.assertRaisesRegex(ValidationError, 'must not define a per-learner enrollment limit'):
            AssignedLearnerCreditAccessPolicy(
                spend_limit=1,
                assignment_configuration=self.assignment_configuration,
                per_learner_enrollment_limit=1,
            ).clean()

    def test_save(self):
        """
        These types of policies should always get saved with an
        access_method of 'assigned' and an ``assignment_configuration`` record.
        Furthermore, the related assignment_configuration should always be updated
        to have an ``enterprise_customer_uuid`` that matches the customer uuid
        defined on the policy record.
        """
        # let the assignments API actually create an assignment configuration
        self.assignments_api_patcher.stop()

        policy = AssignedLearnerCreditAccessPolicyFactory(
            access_method=AccessMethods.DIRECT,
            spend_limit=100,
        )

        policy.save()
        policy.refresh_from_db()

        self.assertEqual(policy.access_method, AccessMethods.ASSIGNED)
        self.assertIsNotNone(policy.assignment_configuration)

        new_customer_uuid = uuid4()
        policy.enterprise_customer_uuid = new_customer_uuid
        policy.save()
        self.assertEqual(
            policy.assignment_configuration.enterprise_customer_uuid,
            new_customer_uuid,
        )

    @ddt.data(
        # Happy path, assignment exists and state='allocated'.
        {
            'assignment_state': LearnerContentAssignmentStateChoices.ALLOCATED,
            'expected_policy_can_redeem': (True, None, []),
        },
        # Sad path, no assignment exists.
        {
            'assignment_state': None,
            'expected_policy_can_redeem': (False, REASON_LEARNER_NOT_ASSIGNED_CONTENT, []),
        },
        # Sad path, assignment has state='cancelled'.
        {
            'assignment_state': LearnerContentAssignmentStateChoices.CANCELLED,
            'expected_policy_can_redeem': (False, REASON_LEARNER_ASSIGNMENT_CANCELLED, []),
        },
        # Sad path, assignment has state='errored'.
        {
            'assignment_state': LearnerContentAssignmentStateChoices.ERRORED,
            'expected_policy_can_redeem': (False, REASON_LEARNER_ASSIGNMENT_FAILED, []),
        },
    )
    @ddt.unpack
    def test_can_redeem(
        self,
        assignment_state,
        expected_policy_can_redeem,
    ):
        """
        Test can_redeem() for assigned learner credit policies.
        """
        self.assignments_api_patcher.stop()

        # Set up the entire environment to make the policy happy about all non-assignment stuff.
        self.mock_lms_api_client.enterprise_contains_learner.return_value = True
        self.mock_catalog_contains_content_key.return_value = True
        self.mock_get_content_metadata.return_value = {
            'content_price': 200,
        }
        self.mock_subsidy_client.can_redeem.return_value = {'can_redeem': True, 'active': True}
        self.mock_transactions_cache_for_learner.return_value = {
            'transactions': [],
            'aggregates': {'total_quantity': -100},
        }
        self.mock_subsidy_client.list_subsidy_transactions.return_value = {
            'results': [],
            'aggregates': {'total_quantity': -200},
        }

        # Create a single assignment (or not) per the test case.
        if assignment_state:
            LearnerContentAssignmentFactory.create(
                assignment_configuration=self.assignment_configuration,
                content_key=self.course_key,
                lms_user_id=self.lms_user_id,
                state=assignment_state,
            )

        can_redeem_result = self.active_policy.can_redeem(
            self.lms_user_id,
            # Note that this string differs from the assignment content_key, but that's okay because the policy should
            # normalize everything to course key before comparison.
            self.course_run_key,
        )

        assert can_redeem_result == expected_policy_can_redeem

    @ddt.data(
        # Happy path, assignment exists and state='allocated', and afterwards gets updated to 'accepted'.
        {
            'assignment_starting_state': LearnerContentAssignmentStateChoices.ALLOCATED,
            'assignment_ending_state': LearnerContentAssignmentStateChoices.ACCEPTED,
            'fail_subsidy_create_transaction': False,
            'redeem_raises': None,
        },
        # Sad path, no assignment exists.
        {
            'assignment_starting_state': None,
            'assignment_ending_state': None,
            'fail_subsidy_create_transaction': False,
            'redeem_raises': MissingAssignment,
        },
        # Sad path, assignment has state='accepted'.
        {
            'assignment_starting_state': LearnerContentAssignmentStateChoices.ACCEPTED,
            'assignment_ending_state': LearnerContentAssignmentStateChoices.ACCEPTED,
            'fail_subsidy_create_transaction': False,
            'redeem_raises': MissingAssignment,
        },
        # Sad path, assignment has state='cancelled'.
        {
            'assignment_starting_state': LearnerContentAssignmentStateChoices.CANCELLED,
            'assignment_ending_state': LearnerContentAssignmentStateChoices.CANCELLED,
            'fail_subsidy_create_transaction': False,
            'redeem_raises': MissingAssignment,
        },
        # Sad path, assignment has state='errored'.
        {
            'assignment_starting_state': LearnerContentAssignmentStateChoices.ERRORED,
            'assignment_ending_state': LearnerContentAssignmentStateChoices.ERRORED,
            'fail_subsidy_create_transaction': False,
            'redeem_raises': MissingAssignment,
        },
        # Sad path, request to subsidy API failed.
        {
            'assignment_starting_state': LearnerContentAssignmentStateChoices.ALLOCATED,
            'assignment_ending_state': LearnerContentAssignmentStateChoices.ERRORED,
            'fail_subsidy_create_transaction': True,
            'redeem_raises': SubsidyAPIHTTPError,
        },
    )
    @ddt.unpack
    def test_redeem(
        self,
        assignment_starting_state,
        assignment_ending_state,
        fail_subsidy_create_transaction,
        redeem_raises,
    ):
        """
        Test redeem() for assigned learner credit policies.
        """
        self.assignments_api_patcher.stop()

        # Set up the entire environment to make the policy happy about all non-assignment stuff.
        self.mock_lms_api_client.enterprise_contains_learner.return_value = True
        self.mock_catalog_contains_content_key.return_value = True
        self.mock_get_content_metadata.return_value = {
            'content_price': 200,
        }
        self.mock_subsidy_client.can_redeem.return_value = {'can_redeem': True, 'active': True}
        self.mock_transactions_cache_for_learner.return_value = {
            'transactions': [],
            'aggregates': {'total_quantity': -100},
        }
        self.mock_subsidy_client.list_subsidy_transactions.return_value = {
            'results': [],
            'aggregates': {'total_quantity': -200},
        }

        # Optionally simulate a failed subsidy API request to create a transaction:
        test_transaction_uuid = uuid4()
        if fail_subsidy_create_transaction:
            self.mock_subsidy_client.create_subsidy_transaction.side_effect = requests.exceptions.HTTPError
        else:
            self.mock_subsidy_client.create_subsidy_transaction.return_value = {'uuid': str(test_transaction_uuid)}

        # Create a single assignment (or not) per the test case.
        assignment = None
        if assignment_starting_state:
            assignment = LearnerContentAssignmentFactory.create(
                assignment_configuration=self.assignment_configuration,
                content_key=self.course_key,
                lms_user_id=self.lms_user_id,
                state=assignment_starting_state,
            )

        # Do the redemption
        if redeem_raises:
            with self.assertRaises(redeem_raises):
                self.active_policy.redeem(self.lms_user_id, self.course_run_key, [])
        else:
            self.active_policy.redeem(self.lms_user_id, self.course_run_key, [])

        # Assert that we call the subsidy client's `create_subsidy_transaction` method
        # with the expected payload, but only for test conditions where redeem() doesn't
        # fail before getting to that point.
        if fail_subsidy_create_transaction or not redeem_raises:
            expected_redeem_payload = {
                'subsidy_uuid': str(self.active_policy.subsidy_uuid),
                'lms_user_id': self.lms_user_id,
                'content_key': self.course_run_key,
                'subsidy_access_policy_uuid': str(self.active_policy.uuid),
                'metadata': None,
                'idempotency_key': ANY,
            }
            if assignment:
                expected_redeem_payload['requested_price_cents'] = -1 * assignment.content_quantity
            self.mock_subsidy_client.create_subsidy_transaction.assert_called_once_with(
                **expected_redeem_payload,
            )

        # assert that the assignment object was correctly updated to reflect the success/failure.
        if assignment:
            assignment.refresh_from_db()
            assert assignment.state == assignment_ending_state
            if not redeem_raises:
                # happy path should result in an updated transaction_uuid.
                assert assignment.transaction_uuid == test_transaction_uuid

                # happy path should also result in a null error_reason on the redeemed action.
                redeemed_action = assignment.actions.last()
                assert redeemed_action.action_type == AssignmentActions.REDEEMED
                assert not redeemed_action.error_reason
            if fail_subsidy_create_transaction:
                # sad path should generate a failed redeememd action with populated error_reason and traceback.
                redeemed_action = assignment.actions.last()
                assert redeemed_action.action_type == AssignmentActions.REDEEMED
                assert redeemed_action.error_reason == AssignmentActionErrors.ENROLLMENT_ERROR
                assert redeemed_action.traceback

    def test_can_allocate_inactive_policy(self):
        """
        Tests that inactive policies can't be allocated against.
        """
        self.mock_get_content_metadata.return_value = {
            'content_price': 1000,
        }
        can_allocate, message = self.inactive_policy.can_allocate(10, self.course_key, 1000)

        self.assertFalse(can_allocate)
        self.assertEqual(message, REASON_POLICY_EXPIRED)

    def test_can_allocate_content_not_in_catalog(self):
        """
        Tests that active policies can't be allocated against for content
        that is not included in the related catalog.
        """
        self.mock_catalog_contains_content_key.return_value = False
        self.mock_get_content_metadata.return_value = {
            'content_price': 1000,
        }

        can_allocate, message = self.active_policy.can_allocate(10, self.course_key, 1000)

        self.assertFalse(can_allocate)
        self.assertEqual(message, REASON_CONTENT_NOT_IN_CATALOG)
        self.mock_catalog_contains_content_key.assert_called_once_with(self.course_key)

    def test_can_allocate_subsidy_inactive(self):
        """
        Test that active policies of this type can't be allocated
        against if the related subsidy is inactive.
        """
        self.mock_catalog_contains_content_key.return_value = True
        self.mock_get_content_metadata.return_value = {
            'content_price': 1000,
        }
        mock_subsidy = {
            'id': 12345,
            'is_active': False,
        }
        self.mock_subsidy_client.retrieve_subsidy.return_value = mock_subsidy

        can_allocate, message = self.active_policy.can_allocate(10, self.course_key, 1000)

        self.assertFalse(can_allocate)
        self.assertEqual(message, REASON_SUBSIDY_EXPIRED)
        self.mock_catalog_contains_content_key.assert_called_once_with(self.course_key)
        self.mock_subsidy_client.retrieve_subsidy.assert_called_once_with(
            subsidy_uuid=self.active_policy.subsidy_uuid,
        )

    def test_can_allocate_not_enough_subsidy_balance(self):
        """
        Test that active policies of this type can't be allocated
        against if the related subsidy does not have enough remaining balance.
        """
        self.mock_catalog_contains_content_key.return_value = True
        self.mock_get_content_metadata.return_value = {
            'content_price': 1000,
        }
        mock_subsidy = {
            'id': 12345,
            'is_active': True,
            'current_balance': 7999,
        }
        self.mock_subsidy_client.retrieve_subsidy.return_value = mock_subsidy
        transactions_for_policy = {
            'transactions': [],  # we don't actually use this
            'aggregates': {
                'total_quantity': -500,
            },
        }
        self.mock_subsidy_client.list_subsidy_transactions.return_value = transactions_for_policy
        self.mock_assignments_api.get_allocated_quantity_for_configuration.return_value = -500

        # The balance of the subsidy is just a bit less
        # than the amount to potentially allocated, e.g.
        # ((7 * 1000) + 500 + 500) > 7999
        can_allocate, message = self.active_policy.can_allocate(7, self.course_key, 1000)

        self.assertFalse(can_allocate)
        self.assertEqual(message, REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY)
        self.mock_assignments_api.get_allocated_quantity_for_configuration.assert_called_once_with(
            self.active_policy.assignment_configuration,
        )

    def test_can_allocate_spend_limit_exceeded(self):
        """
        Test that active policies of this type can't be allocated
        against if it would exceed the policy spend_limit.
        """
        self.mock_catalog_contains_content_key.return_value = True
        self.mock_get_content_metadata.return_value = {
            'content_price': 1000,
        }
        mock_subsidy = {
            'id': 12345,
            'is_active': True,
            'current_balance': 15000,
        }
        self.mock_subsidy_client.retrieve_subsidy.return_value = mock_subsidy
        transactions_for_policy = {
            'transactions': [],  # we don't actually use this
            'aggregates': {
                'total_quantity': -2000,
            },
        }
        self.mock_subsidy_client.list_subsidy_transactions.return_value = transactions_for_policy
        self.mock_assignments_api.get_allocated_quantity_for_configuration.return_value = -2000

        # The balance of the subsidy is just a bit less
        # than the amount to potentially allocated, e.g.
        # ((7 * 1000) + 2000 + 2000) < 15000 (the subsidy balance) but,
        # ((7 * 1000) + 2000 + 2000) > 10000 (the policy spend limit)
        can_allocate, message = self.active_policy.can_allocate(7, self.course_key, 1000)

        self.assertFalse(can_allocate)
        self.assertEqual(message, REASON_POLICY_SPEND_LIMIT_REACHED)
        self.mock_assignments_api.get_allocated_quantity_for_configuration.assert_called_once_with(
            self.active_policy.assignment_configuration,
        )

    def test_can_allocate_happy_path(self):
        """
        Test that active policies of this type can be allocated
        against if there's enough remaining balance and the total
        of (allocated + potentially allocated + spent) < spend_limit.
        """
        self.mock_catalog_contains_content_key.return_value = True
        self.mock_get_content_metadata.return_value = {
            'content_price': 1010,
        }
        mock_subsidy = {
            'id': 12345,
            'is_active': True,
            'current_balance': 10000,
        }
        self.mock_subsidy_client.retrieve_subsidy.return_value = mock_subsidy
        transactions_for_policy = {
            'transactions': [],  # we don't actually use this
            'aggregates': {
                'total_quantity': -1000,
            },
        }
        self.mock_subsidy_client.list_subsidy_transactions.return_value = transactions_for_policy
        self.mock_assignments_api.get_allocated_quantity_for_configuration.return_value = -1000

        # Request a price just slightly different from the canonical price
        # the subidy remaining balance and the spend limit are both 10,000
        # ((7 * 1000) + 1000 + 1000) < 10000
        can_allocate, _ = self.active_policy.can_allocate(7, self.course_key, 1000)

        self.assertTrue(can_allocate)
        self.mock_assignments_api.get_allocated_quantity_for_configuration.assert_called_once_with(
            self.active_policy.assignment_configuration,
        )

    def test_can_allocate_negative_quantity(self):
        """
        Test that attempting to allocate a negative quantity
        results in a PriceValidationError.  The cost of the content should
        be negated just prior to storing the ``content_quantity`` of
        an assignment record.
        """
        with self.assertRaisesRegex(PriceValidationError, 'non-negative'):
            self.active_policy.can_allocate(1, self.course_key, -1)

    @ddt.data(
        {'real_price': 100, 'requested_price': (100 * settings.ALLOCATION_PRICE_VALIDATION_UPPER_BOUND_RATIO) + 1},
        {'real_price': 100, 'requested_price': (100 * settings.ALLOCATION_PRICE_VALIDATION_LOWER_BOUND_RATIO) - 1},
    )
    @ddt.unpack
    def test_can_allocate_invalid_price(self, real_price, requested_price):
        """
        Test that attempting to allocate a price that is too far
        away from the price defined for the content in the enterprise-catalog
        service will result in a PriceValidationError.
        """
        self.mock_get_content_metadata.return_value = {
            'content_price': real_price,
        }
        with self.assertRaisesRegex(PriceValidationError, 'outside of acceptable interval'):
            self.active_policy.can_allocate(1, self.course_key, requested_price)
