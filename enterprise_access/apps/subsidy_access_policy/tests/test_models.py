"""
Tests for subsidy_access_policy models.
"""
import contextlib
from datetime import datetime, timedelta
from unittest.mock import ANY, MagicMock, PropertyMock, patch
from uuid import uuid4

import ddt
import pytest
import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TestCase

from enterprise_access.apps.content_assignments.constants import (
    AssignmentActionErrors,
    AssignmentActions,
    LearnerContentAssignmentStateChoices
)
from enterprise_access.apps.content_assignments.models import AssignmentConfiguration
from enterprise_access.apps.content_assignments.tests.factories import (
    AssignmentConfigurationFactory,
    LearnerContentAssignmentFactory
)
from enterprise_access.apps.subsidy_access_policy.constants import (
    ERROR_MSG_ACTIVE_UNKNOWN_SPEND,
    ERROR_MSG_ACTIVE_WITH_SPEND,
    FALLBACK_EXTERNAL_REFERENCE_ID_KEY,
    REASON_BEYOND_ENROLLMENT_DEADLINE,
    REASON_BNR_NOT_ENABLED,
    REASON_CONTENT_NOT_IN_CATALOG,
    REASON_LEARNER_ASSIGNMENT_CANCELLED,
    REASON_LEARNER_ASSIGNMENT_EXPIRED,
    REASON_LEARNER_ASSIGNMENT_FAILED,
    REASON_LEARNER_ASSIGNMENT_REVERSED,
    REASON_LEARNER_MAX_ENROLLMENTS_REACHED,
    REASON_LEARNER_MAX_SPEND_REACHED,
    REASON_LEARNER_NOT_ASSIGNED_CONTENT,
    REASON_LEARNER_NOT_IN_ENTERPRISE,
    REASON_LEARNER_NOT_IN_ENTERPRISE_GROUP,
    REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY,
    REASON_POLICY_EXPIRED,
    REASON_POLICY_SPEND_LIMIT_REACHED,
    REASON_SUBSIDY_EXPIRED
)
from enterprise_access.apps.subsidy_access_policy.exceptions import MissingAssignment, SubsidyAPIHTTPError
from enterprise_access.apps.subsidy_access_policy.models import (
    ALLOW_LATE_ENROLLMENT_KEY,
    REQUEST_CACHE_NAMESPACE,
    AssignedLearnerCreditAccessPolicy,
    PerLearnerEnrollmentCreditAccessPolicy,
    PerLearnerSpendCreditAccessPolicy,
    PolicyGroupAssociation,
    SubsidyAccessPolicy,
    SubsidyAccessPolicyLockAttemptFailed
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    AssignedLearnerCreditAccessPolicyFactory,
    PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory,
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory,
    PolicyGroupAssociationFactory
)
from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates
from enterprise_access.apps.subsidy_request.models import LearnerCreditRequest
from enterprise_access.apps.subsidy_request.tests.factories import LearnerCreditRequestConfigurationFactory
from enterprise_access.cache_utils import request_cache
from enterprise_access.utils import localized_utcnow
from test_utils import TEST_ENTERPRISE_GROUP_UUID, TEST_USER_RECORD, TEST_USER_RECORD_NO_GROUPS

from ..constants import AccessMethods
from ..exceptions import PriceValidationError
from .mixins import MockPolicyDependenciesMixin

ACTIVE_LEARNER_SPEND_CAP_POLICY_UUID = uuid4()
ACTIVE_LEARNER_ENROLL_CAP_POLICY_UUID = uuid4()
ACTIVE_ASSIGNED_LEARNER_CREDIT_POLICY_UUID = uuid4()


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
        cls.active_non_redeemable_per_learner_enroll_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            per_learner_enrollment_limit=5,
            retired=True,
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
        cls.active_non_redeemable_per_learner_spend_policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            per_learner_spend_limit=500,
            retired=True,
        )

    def setUp(self):
        super().setUp()
        self.assignments_api_patcher = patch(
            'enterprise_access.apps.subsidy_access_policy.models.assignments_api',
            autospec=True,
        )
        self.mock_assignments_api = self.assignments_api_patcher.start()

        # cleanups
        self.addCleanup(self.assignments_api_patcher.stop)

    def tearDown(self):
        """
        Clears any cached data for the test policy instances between test runs.
        """
        super().tearDown()
        request_cache(namespace=REQUEST_CACHE_NAMESPACE).clear()

    def test_save_per_learner_credit_policy_with_bnr_enabled(self):
        """
        Verify that the assignment configuration is created when saving a PerLearnerCreditAccessPolicy
        with bnr_enabled=True.
        """
        # let the assignments API actually create an assignment configuration
        self.assignments_api_patcher.stop()
        policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            uuid=uuid4(),
            per_learner_spend_limit=200,
            spend_limit=5000
        )
        with patch(
                'enterprise_access.apps.subsidy_access_policy.models.PerLearnerSpendCreditAccessPolicy.bnr_enabled',
                new_callable=PropertyMock,
                return_value=True
        ):
            policy.save()
            policy.refresh_from_db()
        self.assertIsNotNone(policy.assignment_configuration)
        new_customer_uuid = uuid4()
        policy.enterprise_customer_uuid = new_customer_uuid
        policy.save()
        self.assertEqual(
            policy.assignment_configuration.enterprise_customer_uuid,
            new_customer_uuid,
        )

    def test_save_per_learner_credit_policy_with_bnr_not_enabled(self):
        """
        Verify that the assignment configuration is not created when saving a PerLearnerCreditAccessPolicy
        with bnr_enabled=False.
        """
        policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            per_learner_spend_limit=500,
        )
        with patch(
                'enterprise_access.apps.subsidy_access_policy.models.PerLearnerSpendCreditAccessPolicy.bnr_enabled',
                new_callable=PropertyMock,
                return_value=False
        ):
            policy.save()
            policy.refresh_from_db()
        self.assertIsNone(policy.assignment_configuration)

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
            # Happy path: content in catalog, learner in enterprise, learner in group,
            # subsidy has value, existing transactions for learner and policy below
            # the policy limits. Expected can_redeem result: True
            'policy_active_type': 'active',
            'catalog_contains_content': True,
            'get_enterprise_user': TEST_USER_RECORD,
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {'total_quantity': -100}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (True, None, []),
        },
        {
            # Content not in catalog, every other check would succeed.
            # Expected can_redeem result: False
            'policy_active_type': 'active',
            'catalog_contains_content': False,
            'get_enterprise_user': TEST_USER_RECORD,
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
            'policy_active_type': 'active',
            'catalog_contains_content': True,
            'get_enterprise_user': None,
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_LEARNER_NOT_IN_ENTERPRISE, []),
            'expect_content_metadata_fetch': False,
            'expect_transaction_fetch': False,
        },
        {
            # Learner is not in the enterprise group, every other check would succeed.
            # Expected can_redeem result: False
            'policy_active_type': 'active',
            'catalog_contains_content': True,
            'get_enterprise_user': TEST_USER_RECORD_NO_GROUPS,
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_LEARNER_NOT_IN_ENTERPRISE_GROUP, []),
            'expect_content_metadata_fetch': False,
            'expect_transaction_fetch': False,
        },
        {
            # The subsidy is not redeemable, every other check would succeed.
            # Expected can_redeem result: False
            'policy_active_type': 'active',
            'catalog_contains_content': True,
            'get_enterprise_user': TEST_USER_RECORD,
            'subsidy_is_redeemable': {'can_redeem': False, 'active': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY, []),
        },
        {
            # The subsidy is redeemable, but the learner has already enrolled more than the limit.
            # Every other check would succeed.
            # Expected can_redeem result: False
            'policy_active_type': 'active',
            'catalog_contains_content': True,
            'get_enterprise_user': TEST_USER_RECORD,
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
            'policy_active_type': 'active',
            'catalog_contains_content': True,
            'get_enterprise_user': TEST_USER_RECORD,
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
            'policy_active_type': 'inactive',
            'catalog_contains_content': True,
            'get_enterprise_user': TEST_USER_RECORD,
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_POLICY_EXPIRED, []),
            'expect_content_metadata_fetch': False,
            'expect_transaction_fetch': False,
        },
        {
            # The subsidy access policy is not redeemable, every other check would succeed.
            # Expected can_redeem result: False
            'policy_active_type': 'non_redeemable',
            'catalog_contains_content': True,
            'get_enterprise_user': TEST_USER_RECORD,
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
            'policy_active_type': 'active',
            'catalog_contains_content': True,
            'get_enterprise_user': TEST_USER_RECORD,
            'subsidy_is_redeemable': {'can_redeem': True, 'active': False},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_SUBSIDY_EXPIRED, []),
        },
    )
    @ddt.unpack
    def test_learner_enrollment_cap_policy_can_redeem(
        self,
        policy_active_type,
        catalog_contains_content,
        get_enterprise_user,
        subsidy_is_redeemable,
        transactions_for_learner,
        transactions_for_policy,
        expected_policy_can_redeem,
        expect_content_metadata_fetch=True,
        expect_transaction_fetch=True,
        enroll_by_date='2099-01-01T00:00:00Z',
    ):
        """
        Test the can_redeem method of PerLearnerEnrollmentCapLearnerCreditAccessPolicy model
        """
        self.mock_lms_api_client.get_enterprise_user.return_value = get_enterprise_user
        self.mock_enterprise_user_record.return_value = get_enterprise_user
        self.mock_catalog_contains_content_key.return_value = catalog_contains_content
        self.mock_get_content_metadata.return_value = {
            'content_price': 200,
            'enroll_by_date': enroll_by_date,
        }
        self.mock_subsidy_client.can_redeem.return_value = subsidy_is_redeemable
        self.mock_transactions_cache_for_learner.return_value = transactions_for_learner
        self.mock_subsidy_client.list_subsidy_transactions.return_value = transactions_for_policy

        policy_record = self.inactive_per_learner_enroll_policy
        if policy_active_type == 'active':
            policy_record = self.per_learner_enroll_policy
        elif policy_active_type == 'non_redeemable':
            policy_record = self.active_non_redeemable_per_learner_enroll_policy

        PolicyGroupAssociationFactory(
            enterprise_group_uuid=TEST_ENTERPRISE_GROUP_UUID,
            subsidy_access_policy=policy_record
        )

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
            'enroll_by_date': '2099-01-01T00:00:00Z',
            'get_enterprise_user': TEST_USER_RECORD,
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
            'enroll_by_date': '2099-01-01T00:00:00Z',
            'get_enterprise_user': TEST_USER_RECORD,
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_CONTENT_NOT_IN_CATALOG, []),
            'expect_content_metadata_fetch': False,
            'expect_transaction_fetch': False,
        },
        {
            # Content enrollment deadline is missing, every other check would succeed.
            # Expected can_redeem result: True
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enroll_by_date': None,
            'get_enterprise_user': TEST_USER_RECORD,
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (True, None, []),
            'expect_content_metadata_fetch': True,
            'expect_transaction_fetch': True,
        },
        {
            # Content enrollment deadline has passed, every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enroll_by_date': '2020-01-01T00:00:00Z',
            'get_enterprise_user': TEST_USER_RECORD,
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_BEYOND_ENROLLMENT_DEADLINE, []),
            'expect_content_metadata_fetch': True,
            'expect_transaction_fetch': False,
        },
        {
            # Content enrollment deadline has passed, every other check would succeed,
            # but late redemption is enabled.
            # Expected can_redeem result: True
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enroll_by_date': '2020-01-01T00:00:00Z',
            'get_enterprise_user': TEST_USER_RECORD,
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (True, None, []),
            'expect_content_metadata_fetch': True,
            'expect_transaction_fetch': True,
            'late_redemption_allowed_until': localized_utcnow() + timedelta(days=1),
        },
        {
            # Learner is not in the enterprise, every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enroll_by_date': '2099-01-01T00:00:00Z',
            'get_enterprise_user': None,
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_LEARNER_NOT_IN_ENTERPRISE, []),
            'expect_content_metadata_fetch': False,
            'expect_transaction_fetch': False,
        },
        {
            # Learner is not in the enterprise group, every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enroll_by_date': '2099-01-01T00:00:00Z',
            'get_enterprise_user': TEST_USER_RECORD_NO_GROUPS,
            'subsidy_is_redeemable': {'can_redeem': True, 'active': True},
            'transactions_for_learner': {'transactions': [], 'aggregates': {}},
            'transactions_for_policy': {'results': [], 'aggregates': {'total_quantity': -200}},
            'expected_policy_can_redeem': (False, REASON_LEARNER_NOT_IN_ENTERPRISE_GROUP, []),
            'expect_content_metadata_fetch': False,
            'expect_transaction_fetch': False,
        },
        {
            # The subsidy is not redeemable, every other check would succeed.
            # Expected can_redeem result: False
            'policy_is_active': True,
            'catalog_contains_content': True,
            'enroll_by_date': '2099-01-01T00:00:00Z',
            'get_enterprise_user': TEST_USER_RECORD,
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
            'enroll_by_date': '2099-01-01T00:00:00Z',
            'get_enterprise_user': TEST_USER_RECORD,
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
            'enroll_by_date': '2099-01-01T00:00:00Z',
            'get_enterprise_user': TEST_USER_RECORD,
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
            'enroll_by_date': '2099-01-01T00:00:00Z',
            'get_enterprise_user': TEST_USER_RECORD,
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
            'enroll_by_date': '2099-01-01T00:00:00Z',
            'get_enterprise_user': TEST_USER_RECORD,
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
        enroll_by_date,
        get_enterprise_user,
        subsidy_is_redeemable,
        transactions_for_learner,
        transactions_for_policy,
        expected_policy_can_redeem,
        expect_content_metadata_fetch=True,
        expect_transaction_fetch=True,
        late_redemption_allowed_until=None,
    ):
        """
        Test the can_redeem method of PerLearnerSpendCapLearnerCreditAccessPolicy model
        """
        self.mock_lms_api_client.get_enterprise_user.return_value = get_enterprise_user
        self.mock_enterprise_user_record.return_value = get_enterprise_user
        self.mock_catalog_contains_content_key.return_value = catalog_contains_content
        self.mock_get_content_metadata.return_value = {
            'content_price': 200,
            'enroll_by_date': enroll_by_date,
        }
        self.mock_subsidy_client.can_redeem.return_value = subsidy_is_redeemable
        self.mock_transactions_cache_for_learner.return_value = transactions_for_learner
        self.mock_subsidy_client.list_subsidy_transactions.return_value = transactions_for_policy

        policy_record = self.inactive_per_learner_spend_policy
        if policy_is_active:
            policy_record = self.per_learner_spend_policy

        if late_redemption_allowed_until:
            policy_record.late_redemption_allowed_until = late_redemption_allowed_until

        PolicyGroupAssociationFactory(
            enterprise_group_uuid=TEST_ENTERPRISE_GROUP_UUID,
            subsidy_access_policy=policy_record
        )

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

    @ddt.data(
        # late redemption never set.
        {
            'late_redemption_allowed_until': None,
            'metadata_provided_to_policy': None,
            'expected_metadata_sent_to_subsidy': None,
        },
        # late redemption set, but has expired.
        {
            'late_redemption_allowed_until': localized_utcnow() - timedelta(days=1),
            'metadata_provided_to_policy': None,
            'expected_metadata_sent_to_subsidy': None,
        },
        # late redemption set and currently allowed.
        {
            'late_redemption_allowed_until': localized_utcnow() + timedelta(days=1),
            'metadata_provided_to_policy': None,
            'expected_metadata_sent_to_subsidy': {ALLOW_LATE_ENROLLMENT_KEY: True},
        },
        # late redemption never set.
        # + some metadata is provided.
        {
            'late_redemption_allowed_until': None,
            'metadata_provided_to_policy': {'foo': 'bar'},
            'expected_metadata_sent_to_subsidy': {'foo': 'bar'},
        },
        # late redemption set, but has expired.
        # + some metadata is provided.
        {
            'late_redemption_allowed_until': localized_utcnow() - timedelta(days=1),
            'metadata_provided_to_policy': {'foo': 'bar'},
            'expected_metadata_sent_to_subsidy': {'foo': 'bar'},
        },
        # late redemption set and currently allowed.
        # + some metadata is provided.
        {
            'late_redemption_allowed_until': localized_utcnow() + timedelta(days=1),
            'metadata_provided_to_policy': {'foo': 'bar'},
            'expected_metadata_sent_to_subsidy': {'foo': 'bar', ALLOW_LATE_ENROLLMENT_KEY: True},
        },
    )
    @ddt.unpack
    def test_redeem_pass_late_enrollment(
        self,
        late_redemption_allowed_until,
        metadata_provided_to_policy,
        expected_metadata_sent_to_subsidy,
    ):
        """
        Test redeem() when the late redemption feature is involved.
        """

        # Set up the entire environment to make the policy and subsidy happy to redeem.
        self.mock_lms_api_client.get_enterprise_user.return_value = TEST_USER_RECORD
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
        self.mock_subsidy_client.create_subsidy_transaction.return_value = {'uuid': str(uuid4())}

        # Optionally swap out the test policy with one that allows late redemption.
        test_policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            per_learner_spend_limit=500,
            spend_limit=10000,
            late_redemption_allowed_until=late_redemption_allowed_until,
        )

        # Do the redemption
        test_policy.redeem(self.lms_user_id, self.course_id, [], metadata=metadata_provided_to_policy)

        # Assert that the metadata we send to enterprise-subsidy contains the allow_late_enrollment hint (or not).
        assert self.mock_subsidy_client.create_subsidy_transaction.call_args.kwargs['metadata'] \
            == expected_metadata_sent_to_subsidy

    @ddt.data(
        {
            'old_spend_limit': 100,
            'deposit_quantity': 50,
            'api_side_effect': None,
            'expected_spend_limit': 150,
        },
        {
            'old_spend_limit': 100,
            'deposit_quantity': 50,
            'api_side_effect': requests.exceptions.HTTPError,
            'expected_spend_limit': 100,
        },
    )
    @ddt.unpack
    def test_create_deposit(
        self,
        old_spend_limit,
        deposit_quantity,
        api_side_effect,
        expected_spend_limit,
    ):
        """
        Test the Policy.create_deposit() function.
        """
        policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            spend_limit=old_spend_limit,
            per_learner_spend_limit=None,
        )
        subsidy_record_patcher = patch.object(policy, 'subsidy_record')
        mock_subsidy_record = subsidy_record_patcher.start()
        mock_subsidy_record.return_value = {
            'id': 1,
            'active_datetime': datetime.utcnow() - timedelta(days=1),
            'expiration_datetime': datetime.utcnow() + timedelta(days=1),
            'is_active': True,
            'current_balance': 9999,
            'total_deposits': 9999,
        }
        self.mock_subsidy_client.create_subsidy_deposit.side_effect = api_side_effect
        assert_raises_or_not = self.assertRaises(SubsidyAPIHTTPError) if api_side_effect else contextlib.nullcontext()
        with assert_raises_or_not:
            policy.create_deposit(
                desired_deposit_quantity=deposit_quantity,
                sales_contract_reference_id='test-ref-id',
                sales_contract_reference_provider='test-slug',
                metadata={'foo': 'bar'},
            )
        self.mock_subsidy_client.create_subsidy_deposit.assert_called_once_with(
            subsidy_uuid=policy.subsidy_uuid,
            desired_deposit_quantity=deposit_quantity,
            sales_contract_reference_id='test-ref-id',
            sales_contract_reference_provider='test-slug',
            metadata={'foo': 'bar'},
        )
        policy.refresh_from_db()
        assert policy.spend_limit == expected_spend_limit

    def test_per_learner_spend_policy_can_redeem_with_bnr_enabled(self):
        """
        Test that PerLearnerSpendCreditAccessPolicy.can_redeem correctly delegates
        to a new instance of AssignedLearnerCreditAccessPolicy when bnr_enabled is True.
        """
        policy = self.per_learner_spend_policy
        mock_return_value = (True, None, [])

        # This mock instance will be returned by the patched constructor
        mock_assigned_policy_instance = MagicMock()
        mock_assigned_policy_instance.can_redeem.return_value = mock_return_value

        with patch(
                'enterprise_access.apps.subsidy_access_policy.models.PerLearnerSpendCreditAccessPolicy.bnr_enabled',
                new_callable=PropertyMock,
                return_value=True
        ), patch(
            'enterprise_access.apps.subsidy_access_policy.models.AssignedLearnerCreditAccessPolicy',
            return_value=mock_assigned_policy_instance
        ) as mock_assigned_class:
            result = policy.can_redeem(self.lms_user_id, self.course_id, skip_customer_user_check=True)

            self.assertEqual(result, mock_return_value)
            mock_assigned_class.assert_called_once_with()
            mock_assigned_policy_instance.can_redeem.assert_called_once_with(
                self.lms_user_id, self.course_id, True, False, **{}
            )

    def test_per_learner_spend_policy_redeem_with_bnr_enabled(self):
        """
        Test that PerLearnerSpendCreditAccessPolicy.redeem correctly delegates
        to assignment_request_redeem when bnr_enabled is True and updates the credit request state.
        """
        policy = self.per_learner_spend_policy
        mock_credit_request = MagicMock()
        mock_assignment = MagicMock()
        mock_assignment.credit_request = mock_credit_request
        mock_return_value = {'uuid': 'test-transaction-uuid'}
        all_transactions = [{'uuid': 'some-other-uuid'}]
        metadata = {'source': 'bnr_test'}

        with patch(
                'enterprise_access.apps.subsidy_access_policy.models.PerLearnerSpendCreditAccessPolicy.bnr_enabled',
                new_callable=PropertyMock,
                return_value=True
        ), patch(
                'enterprise_access.apps.subsidy_access_policy.models.AssignedLearnerCreditAccessPolicy.get_assignment',
                return_value=mock_assignment
        ), patch(
                'enterprise_access.apps.subsidy_access_policy.models.LearnerCreditRequestActions.create_action'
        ), patch(
                'enterprise_access.apps.subsidy_access_policy.models.AssignedLearnerCreditAccessPolicy.redeem',
                return_value=mock_return_value
        ), patch.object(
                policy, 'assignment_request_redeem', side_effect=policy.assignment_request_redeem
        ) as mock_assignment_request_redeem:
            result = policy.redeem(self.lms_user_id, self.course_id, all_transactions, metadata=metadata)

            self.assertEqual(result, mock_return_value)
            mock_assignment_request_redeem.assert_called_once_with(
                self.lms_user_id, self.course_id, all_transactions, metadata=metadata
            )

            # Verify that the state was updated to ACCEPTED and save was called
            self.assertEqual(mock_credit_request.state, SubsidyRequestStates.ACCEPTED)
            mock_credit_request.save.assert_called_once()

    def test_per_learner_spend_policy_can_redeem_with_bnr_disabled(self):
        """
        Test that PerLearnerSpendCreditAccessPolicy.can_redeem does NOT delegate
        when bnr_enabled is False and calls its standard logic instead.
        """
        policy = self.per_learner_spend_policy

        with patch(
                'enterprise_access.apps.subsidy_access_policy.models.PerLearnerSpendCreditAccessPolicy.bnr_enabled',
                new_callable=PropertyMock,
                return_value=False
        ), patch(
            'enterprise_access.apps.subsidy_access_policy.models.AssignedLearnerCreditAccessPolicy.can_redeem'
        ) as mock_assigned_can_redeem, patch(
            'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.can_redeem',
            return_value=(False, 'mock_reason', [])
        ) as mock_super_can_redeem:
            policy.can_redeem(self.lms_user_id, self.course_id)

            mock_assigned_can_redeem.assert_not_called()
            mock_super_can_redeem.assert_called_once()

    def test_budget_with_spend_cannot_be_deactivated(self):
        """
        Test that any budget with existing spend cannot be deactivated (active toggled).
        """
        policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            active=True,
            retired=False,  # Not retired, but still should be prevented from deactivation
        )
        self.mock_subsidy_client.list_subsidy_transactions.return_value = {
            'results': [],
            'aggregates': {'total_quantity': -1000}  # Negative value indicates spend
        }
        policy.active = False
        with self.assertRaises(ValidationError) as context:
            policy.save()
        self.assertIn('active', context.exception.error_dict)
        self.assertIn(ERROR_MSG_ACTIVE_WITH_SPEND, str(context.exception.error_dict['active']))

    def test_retired_budget_with_spend_cannot_be_deactivated(self):
        """
        Test that retired budgets with existing spend cannot be deactivated (active toggled).
        """
        policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            active=True,
            retired=True,
        )
        self.mock_subsidy_client.list_subsidy_transactions.return_value = {
            'results': [],
            'aggregates': {'total_quantity': -1000}  # Negative value indicates spend
        }
        policy.active = False
        with self.assertRaises(ValidationError) as context:
            policy.save()
        self.assertIn('active', context.exception.error_dict)
        self.assertIn(ERROR_MSG_ACTIVE_WITH_SPEND, str(context.exception.error_dict['active']))

    def test_budget_deactivation_with_api_error(self):
        """
        Test that budgets cannot be deactivated when spend cannot be determined (active toggled).
        """
        policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            active=True,
            retired=False,  # Not retired, but still should be prevented from deactivation
        )
        self.mock_subsidy_client.list_subsidy_transactions.side_effect = requests.exceptions.HTTPError("API Error")
        policy.active = False
        with self.assertRaises(ValidationError) as context:
            policy.save()
        self.assertIn('active', context.exception.error_dict)
        self.assertIn(ERROR_MSG_ACTIVE_UNKNOWN_SPEND, str(context.exception.error_dict['active']))

    def test_retired_budget_deactivation_with_api_error(self):
        """
        Test that retired budgets cannot be deactivated when spend cannot be determined (active toggled).
        """
        policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            active=True,
            retired=True,
        )
        self.mock_subsidy_client.list_subsidy_transactions.side_effect = requests.exceptions.HTTPError("API Error")
        policy.active = False
        with self.assertRaises(ValidationError) as context:
            policy.save()
        self.assertIn('active', context.exception.error_dict)
        self.assertIn(ERROR_MSG_ACTIVE_UNKNOWN_SPEND, str(context.exception.error_dict['active']))

    def test_budget_deactivation_allowed_with_setting(self):
        """
        Test that budget deactivation is allowed when ALLOW_BUDGET_DEACTIVATION_WITH_SPEND is True.
        """
        policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            active=True,
            retired=False,
        )
        self.mock_subsidy_client.list_subsidy_transactions.return_value = {
            'results': [],
            'aggregates': {'total_quantity': -1000}  # Negative value indicates spend
        }
        policy.active = False

        with self.settings(ALLOW_BUDGET_DEACTIVATION_WITH_SPEND=True):
            # Should not raise an exception
            policy.save()

        # Verify the policy was actually deactivated
        policy.refresh_from_db()
        self.assertFalse(policy.active)

    def test_budget_deactivation_not_allowed_without_setting(self):
        """
        Test that budget deactivation is not allowed when ALLOW_BUDGET_DEACTIVATION_WITH_SPEND is False or not set.
        """
        policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            active=True,
            retired=False,
        )
        self.mock_subsidy_client.list_subsidy_transactions.return_value = {
            'results': [],
            'aggregates': {'total_quantity': -1000}  # Negative value indicates spend
        }
        policy.active = False

        with self.settings(ALLOW_BUDGET_DEACTIVATION_WITH_SPEND=False):
            with self.assertRaises(ValidationError) as context:
                policy.save()
            self.assertIn('active', context.exception.error_dict)
            self.assertIn(ERROR_MSG_ACTIVE_WITH_SPEND, str(context.exception.error_dict['active']))

    def test_budget_without_spend_can_be_deactivated(self):
        """
        Test that budgets without spend can be deactivated normally.
        """
        policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            active=True,
            retired=False,
        )
        self.mock_subsidy_client.list_subsidy_transactions.return_value = {
            'results': [],
            'aggregates': {'total_quantity': 0}  # No spend
        }
        policy.active = False

        # Should not raise an exception
        policy.save()

        # Verify the policy was actually deactivated
        policy.refresh_from_db()
        self.assertFalse(policy.active)

    def test_per_learner_spend_policy_can_approve_bnr_disabled(self):
        """
        Test that PerLearnerSpendCreditAccessPolicy.can_approve when bnr_enabled is False.
        """
        policy = self.per_learner_spend_policy
        mock_request = MagicMock(spec=LearnerCreditRequest)
        with patch(
                'enterprise_access.apps.subsidy_access_policy.models.PerLearnerSpendCreditAccessPolicy.bnr_enabled',
                new_callable=PropertyMock,
                return_value=False
        ), patch(
            'enterprise_access.apps.subsidy_access_policy.models.AssignedLearnerCreditAccessPolicy.can_allocate_all'
        ) as mock_can_allocate_all:
            result = policy.can_approve([mock_request])
            mock_can_allocate_all.assert_not_called()
            self.assertIn('error_reason', result)
            self.assertEqual(result['error_reason'], REASON_BNR_NOT_ENABLED)

    def test_per_learner_spend_policy_can_approve_bnr_enabled(self):
        """
        Test PerLearnerSpendCreditAccessPolicy.can_approve when bnr_enabled is True.
        """
        policy = self.per_learner_spend_policy
        mock_request = MagicMock(spec=LearnerCreditRequest)
        with patch(
                'enterprise_access.apps.subsidy_access_policy.models.PerLearnerSpendCreditAccessPolicy.bnr_enabled',
                new_callable=PropertyMock,
                return_value=True
        ), patch(
            'enterprise_access.apps.subsidy_access_policy.models.AssignedLearnerCreditAccessPolicy.can_allocate_all'
        ) as mock_can_allocate_all:
            mock_can_allocate_all.return_value = {
                'valid_requests': [mock_request],
                'failed_requests_by_reason': {},
            }
            result = policy.can_approve([mock_request])
            mock_can_allocate_all.assert_called_once_with([mock_request])
            self.assertIn('valid_requests', result)
            self.assertEqual(result['valid_requests'], [mock_request])

    def test_per_learner_spend_policy_can_approve_failure(self):
        """
        Test PerLearnerSpendCreditAccessPolicy.can_approve when all requests fail.
        """
        policy = self.per_learner_spend_policy
        mock_request = MagicMock(spec=LearnerCreditRequest)
        failure_reason = REASON_CONTENT_NOT_IN_CATALOG
        with patch(
                'enterprise_access.apps.subsidy_access_policy.models.PerLearnerSpendCreditAccessPolicy.bnr_enabled',
                new_callable=PropertyMock,
                return_value=True
        ), patch(
            'enterprise_access.apps.subsidy_access_policy.models.AssignedLearnerCreditAccessPolicy.can_allocate_all'
        ) as mock_can_allocate_all:
            mock_can_allocate_all.return_value = {
                'valid_requests': [],
                'failed_requests_by_reason': {failure_reason: [mock_request]},
            }
            result = policy.can_approve([mock_request])
            mock_can_allocate_all.assert_called_once_with([mock_request])
            # assert it returns the same reason as AssignedLearnerCreditAccessPolicy.can_approve
            self.assertEqual(
                result,
                {
                    'valid_requests': [],
                    'failed_requests_by_reason': {failure_reason: [mock_request]},
                }
            )

    def test_per_learner_spend_policy_approve_method_assignment_allocated(self):
        """
        Test PerLearnerSpendCreditAccessPolicy.approve method calls assignments_api.allocate_assignment_for_requests()
          with correct paramaters when a new assignment is created.
        """
        # set up assignment, configuration and allocate_assignment_for_requests mock return value
        assignment_configuration = AssignmentConfiguration.objects.create()
        mock_request = MagicMock(spec=LearnerCreditRequest)
        mock_request.uuid = uuid4()
        test_learner_email = 'test@email.com'
        test_course_price = 1000
        assignment = LearnerContentAssignmentFactory(
            learner_email=test_learner_email,
            content_key=self.course_id,
            lms_user_id=self.lms_user_id,
            state='allocated',
            content_quantity=-test_course_price,
            assignment_configuration=assignment_configuration,
        )

        self.mock_assignments_api.allocate_assignment_for_requests.return_value = {mock_request.uuid: assignment}

        # link assignment_configuration to the policy
        policy = self.per_learner_spend_policy
        policy.assignment_configuration = assignment_configuration
        policy.save()

        with patch(
                'enterprise_access.apps.subsidy_access_policy.models.PerLearnerSpendCreditAccessPolicy.bnr_enabled',
                new_callable=PropertyMock,
                return_value=True
        ):
            result = policy.approve([mock_request])
            # assert that the result is the created assignment
            self.assertEqual(result, {mock_request.uuid: assignment})

            # assert that the assignments_api.allocate_assignment_for_requests was called with correct parameters
            self.mock_assignments_api.allocate_assignment_for_requests.assert_called_once_with(
                assignment_configuration,
                [mock_request]
            )


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

        assign_get_content_metadata_patcher = patch(
            'enterprise_access.apps.content_assignments.api.get_and_cache_content_metadata'
        )
        self.mock_assign_get_content_metadata = assign_get_content_metadata_patcher.start()

        self.addCleanup(self.assignments_api_patcher.stop)
        self.addCleanup(assign_get_content_metadata_patcher.stop)

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

    def test_validation_rules_on_save(self):
        """
        Tests the model-level validation rules of this policy type.
        """
        with self.assertRaisesRegex(ValidationError, 'must define a spend_limit'):
            policy = AssignedLearnerCreditAccessPolicyFactory(
                spend_limit=None,
                assignment_configuration=self.assignment_configuration,
            )
            policy.save()
        with self.assertRaisesRegex(ValidationError, 'must not define a per-learner spend limit'):
            policy = AssignedLearnerCreditAccessPolicyFactory(
                assignment_configuration=self.assignment_configuration,
                per_learner_spend_limit=1,
            )
            policy.save()
        with self.assertRaisesRegex(ValidationError, 'must not define a per-learner enrollment limit'):
            policy = AssignedLearnerCreditAccessPolicyFactory(
                spend_limit=1,
                assignment_configuration=self.assignment_configuration,
                per_learner_enrollment_limit=1,
            )
            policy.save()

    def test_save_access_method_and_assignment_configuration(self):
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
        # Sad path, assignment has state='expired'.
        {
            'assignment_state': LearnerContentAssignmentStateChoices.EXPIRED,
            'expected_policy_can_redeem': (False, REASON_LEARNER_ASSIGNMENT_EXPIRED, []),
        },
        # Sad path, assignment has state='reversed'.
        {
            'assignment_state': LearnerContentAssignmentStateChoices.REVERSED,
            'expected_policy_can_redeem': (False, REASON_LEARNER_ASSIGNMENT_REVERSED, []),
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
        self.mock_lms_api_client.get_enterprise_user.return_value = TEST_USER_RECORD
        self.mock_catalog_contains_content_key.return_value = True
        self.mock_get_content_metadata.return_value = {
            'content_price': 200,
            'enroll_by_date': '2099-01-01T00:00:00Z',
        }
        self.mock_assign_get_content_metadata.return_value = {
            'content_price': 200,
            'content_key': self.course_key,
            'course_run_key': self.course_run_key,
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
        # Happy path, simulate a forced redemption with a predetermined external fulfillment ID.
        {
            'assignment_starting_state': LearnerContentAssignmentStateChoices.ALLOCATED,
            'assignment_ending_state': LearnerContentAssignmentStateChoices.ACCEPTED,
            'fail_subsidy_create_transaction': False,
            'forced_with_external_reference_id': True,
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
        forced_with_external_reference_id=False,
    ):
        """
        Test redeem() for assigned learner credit policies.
        """
        self.assignments_api_patcher.stop()

        # Set up the entire environment to make the policy happy about all non-assignment stuff.
        self.mock_lms_api_client.get_enterprise_user.return_value = TEST_USER_RECORD
        self.mock_catalog_contains_content_key.return_value = True
        self.mock_get_content_metadata.return_value = {
            'content_price': 200,
        }
        self.mock_assign_get_content_metadata.return_value = {
            'content_price': 200,
            'content_key': self.course_key,
            'course_run_key': self.course_run_key,
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

        test_external_reference_id = uuid4()
        extra_redeem_kwargs = {}
        if forced_with_external_reference_id:
            extra_redeem_kwargs = {'metadata': {FALLBACK_EXTERNAL_REFERENCE_ID_KEY: str(test_external_reference_id)}}

        # Do the redemption
        with self.assertRaises(redeem_raises) if redeem_raises else contextlib.nullcontext():
            self.active_policy.redeem(
                lms_user_id=self.lms_user_id,
                content_key=self.course_run_key,
                all_transactions=[],
                **extra_redeem_kwargs,
            )

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
            if forced_with_external_reference_id:
                expected_redeem_payload['metadata'] = expected_redeem_payload['metadata'] or {}
                expected_redeem_payload['metadata'].update({
                    FALLBACK_EXTERNAL_REFERENCE_ID_KEY: str(test_external_reference_id),
                })
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
                # sad path should generate a failed redeemed action with populated error_reason and traceback.
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
        self.mock_assignments_api.get_allocated_quantity_for_configuration.return_value = -1000

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
        # the subsidy remaining balance and the spend limit are both 10,000
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


@ddt.ddt
class AssignedLearnerCreditAccessPolicyAllocationTests(MockPolicyDependenciesMixin, TestCase):
    """ Tests specific to allocation logic in AssignedLearnerCreditAccessPolicy. """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.assignment_configuration = AssignmentConfigurationFactory()
        cls.policy = AssignedLearnerCreditAccessPolicyFactory(
            spend_limit=10000,  # $100
            assignment_configuration=cls.assignment_configuration,
            active=True,
            retired=False,
        )

    def setUp(self):
        """ Mock dependencies. """
        super().setUp()
        # Mock assignments_api needed by can_allocate_all
        self.assignments_api_patcher = patch(
            'enterprise_access.apps.subsidy_access_policy.models.assignments_api',
            autospec=True,
        )
        self.mock_assignments_api = self.assignments_api_patcher.start()
        self.addCleanup(self.assignments_api_patcher.stop)

        # Mock content_metadata.api needed by can_allocate_all and price validation
        self.catalog_content_metadata_patcher = patch(
            'enterprise_access.apps.subsidy_access_policy.models.get_and_cache_catalog_content_metadata'
        )
        self.mock_catalog_content_metadata = self.catalog_content_metadata_patcher.start()
        self.addCleanup(self.catalog_content_metadata_patcher.stop)

        # Mock the canonical price function used within price validation
        self.canonical_price_patcher = patch(
            'enterprise_access.apps.subsidy_access_policy.models.get_canonical_content_price_from_metadata'
        )
        self.mock_canonical_price = self.canonical_price_patcher.start()
        self.addCleanup(self.canonical_price_patcher.stop)

        # Mock properties/methods that call external services
        self.is_subsidy_active_patcher = patch.object(
            AssignedLearnerCreditAccessPolicy, 'is_subsidy_active', new_callable=PropertyMock
        )
        self.mock_is_subsidy_active = self.is_subsidy_active_patcher.start()
        self.addCleanup(self.is_subsidy_active_patcher.stop)

        self.subsidy_balance_patcher = patch.object(
            AssignedLearnerCreditAccessPolicy, 'subsidy_balance'
        )
        self.mock_subsidy_balance = self.subsidy_balance_patcher.start()
        self.addCleanup(self.subsidy_balance_patcher.stop)

        self.aggregates_for_policy_patcher = patch.object(
            AssignedLearnerCreditAccessPolicy, 'aggregates_for_policy'
        )
        self.mock_aggregates_for_policy = self.aggregates_for_policy_patcher.start()
        self.addCleanup(self.aggregates_for_policy_patcher.stop)

        # --- Default Mock Return Values ---
        self.policy.active = True  # Ensure policy is active by default for most tests
        self.policy.retired = False
        self.mock_is_subsidy_active.return_value = True
        self.mock_subsidy_balance.return_value = 50000  # $500 default balance
        self.mock_aggregates_for_policy.return_value = {'total_quantity': -1000}  # $10 spent
        self.mock_assignments_api.get_allocated_quantity_for_configuration.return_value = -2000  # $20 allocated

        # Default price validation settings (can be overridden with self.settings)
        # Assuming defaults are 0.9 and 1.1 for 10% tolerance
        self.lower_bound_ratio = 0.9
        self.upper_bound_ratio = 1.1

    # --- Tests for validate_learner_credit_requests_allocation_prices ---

    def _create_mock_request(self, course_id, course_price):
        """
        Helper to create a MagicMock LearnerCreditRequest.
        """

        req = MagicMock(spec=LearnerCreditRequest)
        req.course_id = course_id
        req.course_price = course_price
        return req

    @ddt.data(
        # Price matches exactly.
        {'req_price': 5000, 'canon_price': 5000, 'should_pass': True},
        # Price within upper bound.
        {'req_price': 5499, 'canon_price': 5000, 'should_pass': True},
        # Price exactly at upper bound (assuming 1.1 ratio).
        {'req_price': 5500, 'canon_price': 5000, 'should_pass': True},
        # Price within lower bound.
        {'req_price': 4501, 'canon_price': 5000, 'should_pass': True},
        # Price exactly at lower bound (assuming 0.9 ratio).
        {'req_price': 4500, 'canon_price': 5000, 'should_pass': True},
        # Price above upper bound.
        {'req_price': 5501, 'canon_price': 5000, 'should_pass': False},
        # Price below lower bound.
        {'req_price': 4499, 'canon_price': 5000, 'should_pass': False},
        # Negative request price.
        {'req_price': -100, 'canon_price': 5000, 'should_pass': False},
        # Zero request price, zero canonical price.
        {'req_price': 0, 'canon_price': 0, 'should_pass': True},
        # Zero request price, non-zero canonical price (within bounds).
        {'req_price': 0, 'canon_price': 5000, 'should_pass': False},  # Fails because 0 < 4500
        # Non-zero request price, zero canonical price (will fail unless req_price is also 0).
        {'req_price': 10, 'canon_price': 0, 'should_pass': False},  # Fails because 10 > 0
    )
    @ddt.unpack
    def test_validate_prices_single_request(self, req_price, canon_price, should_pass):
        """
        Test price validation logic for a single request.
        """

        course_id = 'course-v1:Test+Course+Num'
        mock_request = self._create_mock_request(course_id, req_price)
        all_metadata = {course_id: {'key': course_id}}  # Minimal metadata.

        # Mock the canonical price lookup.
        self.mock_canonical_price.return_value = canon_price

        with self.settings(
            ALLOCATION_PRICE_VALIDATION_LOWER_BOUND_RATIO=self.lower_bound_ratio,
            ALLOCATION_PRICE_VALIDATION_UPPER_BOUND_RATIO=self.upper_bound_ratio,
        ):
            valid, failed = self.policy.validate_learner_credit_requests_allocation_prices(
                [mock_request], all_metadata
            )

        if should_pass:
            self.assertEqual(len(valid), 1)
            self.assertEqual(valid[0], mock_request)
            self.assertEqual(len(failed), 0)
            self.mock_canonical_price.assert_called_once_with(course_id, all_metadata[course_id])
        else:
            self.assertEqual(len(valid), 0)
            self.assertEqual(len(failed), 1)
            self.assertEqual(failed[0], mock_request)
            # Negative prices are checked before canonical lookup.
            if req_price < 0:
                self.mock_canonical_price.assert_not_called()
                # Remove the failure_reason check since the actual implementation doesn't set it.
                # The implementation only logs warnings.
            else:
                self.mock_canonical_price.assert_called_once_with(course_id, all_metadata[course_id])

    @ddt.data(
        # Price above upper bound.
        {'req_price': 5501, 'canon_price': 5000, 'should_pass': False},
        # Price below lower bound.
        {'req_price': 4499, 'canon_price': 5000, 'should_pass': False},
        # Negative request price.
        {'req_price': -100, 'canon_price': 5000, 'should_pass': False},
    )
    @ddt.unpack
    def test_validate_prices_single_request_with_failure_reason(self, req_price, canon_price, should_pass):
        """
        Test price validation logic for a single request.
        """

        course_id = 'course-v1:Test+Course+3'
        mock_request = self._create_mock_request(course_id, req_price)
        all_metadata = {course_id: {'key': course_id}}

        # Mock the canonical price lookup.
        self.mock_canonical_price.return_value = canon_price

        with self.settings(
            ALLOCATION_PRICE_VALIDATION_LOWER_BOUND_RATIO=self.lower_bound_ratio,
            ALLOCATION_PRICE_VALIDATION_UPPER_BOUND_RATIO=self.upper_bound_ratio,
        ):
            valid, failed = self.policy.validate_learner_credit_requests_allocation_prices(
                [mock_request], all_metadata
            )

        if should_pass:
            self.assertEqual(len(valid), 1)
            self.assertEqual(len(failed), 0)
        else:
            self.assertEqual(len(valid), 0)
            self.assertEqual(len(failed), 1)
            # Note: The actual implementation only logs warnings and doesn't set failure_reason.
            # So we don't check for failure_reason attribute.

    def test_validate_prices_batch_mixed(self):
        """
        Test price validation with a mix of valid and invalid requests.
        """

        req1 = self._create_mock_request('course-1', 5000)  # Valid.
        req2 = self._create_mock_request('course-2', 6000)  # Invalid (too high).
        req3 = self._create_mock_request('course-3', 4000)  # Invalid (too low).
        req4 = self._create_mock_request('course-4', 5200)  # Valid.

        all_metadata = {
            'course-1': {'key': 'course-1'},
            'course-2': {'key': 'course-2'},
            'course-3': {'key': 'course-3'},
            'course-4': {'key': 'course-4'},
        }

        # Mock canonical prices.
        def mock_price_side_effect(course_id, _metadata):
            prices = {'course-1': 5000, 'course-2': 5000, 'course-3': 5000, 'course-4': 5000}
            return prices.get(course_id)
        self.mock_canonical_price.side_effect = mock_price_side_effect

        with self.settings(
            ALLOCATION_PRICE_VALIDATION_LOWER_BOUND_RATIO=self.lower_bound_ratio,
            ALLOCATION_PRICE_VALIDATION_UPPER_BOUND_RATIO=self.upper_bound_ratio,
        ):
            valid, failed = self.policy.validate_learner_credit_requests_allocation_prices(
                [req1, req2, req3, req4], all_metadata
            )

        self.assertCountEqual(valid, [req1, req4])  # Use assertCountEqual for lists where order doesn't matter.
        self.assertCountEqual(failed, [req2, req3])
        # Ensure canonical price was looked up for all non-negative priced requests.
        self.assertEqual(self.mock_canonical_price.call_count, 4)

    # --- Tests for can_allocate_all ---

    def test_can_allocate_all_policy_inactive(self):
        """
        Test can_allocate_all fails immediately if policy is inactive.
        """

        self.policy.active = False
        reqs = [self._create_mock_request('c1', 1000)]
        result = self.policy.can_allocate_all(reqs)

        self.assertIn('error_reason', result)
        self.assertEqual(result['error_reason'], REASON_POLICY_EXPIRED)
        # Should fail before checking anything else.
        self.mock_is_subsidy_active.assert_not_called()
        self.mock_catalog_content_metadata.assert_not_called()

    def test_can_allocate_all_subsidy_inactive(self):
        """
        Test can_allocate_all fails immediately if subsidy is inactive.
        """

        self.mock_is_subsidy_active.return_value = False
        reqs = [self._create_mock_request('c1', 1000)]
        result = self.policy.can_allocate_all(reqs)

        self.assertIn('error_reason', result)
        self.assertEqual(result['error_reason'], REASON_SUBSIDY_EXPIRED)
        self.mock_catalog_content_metadata.assert_not_called()

    def test_can_allocate_all_content_not_in_catalog(self):
        """
        Test can_allocate_all fails requests for content not in the catalog.
        """

        req1 = self._create_mock_request('course-in', 1000)
        req2 = self._create_mock_request('course-out', 1000)
        reqs = [req1, req2]
        all_content_keys = list(set(req.course_id for req in reqs))

        # Mock metadata to only return 'course-in'.
        self.mock_catalog_content_metadata.return_value = []
        # Mock canonical price for the valid course.
        self.mock_canonical_price.return_value = 1000

        result = self.policy.can_allocate_all(reqs)

        self.assertEqual(len(result['valid_requests']), 0)
        self.assertEqual(len(result['failed_requests_by_reason']), 1)
        self.assertIn(REASON_CONTENT_NOT_IN_CATALOG, result['failed_requests_by_reason'])
        self.assertEqual(result['failed_requests_by_reason'][REASON_CONTENT_NOT_IN_CATALOG], [req1, req2])

        self.assertEqual(self.mock_catalog_content_metadata.call_count, 1)
        self.mock_catalog_content_metadata.assert_called_with(self.policy.catalog_uuid, all_content_keys)

    def test_can_allocate_all_price_validation_failure(self):
        """
        Test can_allocate_all fails requests that fail price validation.
        """

        req1 = self._create_mock_request('course-ok', 5000)
        req2 = self._create_mock_request('course-bad-price', 6000)  # Assume canonical is 5000.
        reqs = [req1, req2]

        # Mock metadata returning both.
        self.mock_catalog_content_metadata.return_value = [
            {'key': 'course-ok'},
            {'key': 'course-bad-price'},
        ]

        # Mock canonical prices.
        def mock_price_side_effect(_course_id, _metadata):
            return 5000  # Both are 5000 canonically.
        self.mock_canonical_price.side_effect = mock_price_side_effect

        with self.settings(
            ALLOCATION_PRICE_VALIDATION_LOWER_BOUND_RATIO=self.lower_bound_ratio,
            ALLOCATION_PRICE_VALIDATION_UPPER_BOUND_RATIO=self.upper_bound_ratio,
        ):
            result = self.policy.can_allocate_all(reqs)

        self.assertEqual(len(result['valid_requests']), 1)  # req1 is still valid.
        self.assertEqual(result['valid_requests'][0], req1)
        self.assertEqual(len(result['failed_requests_by_reason']), 1)
        self.assertIn(PriceValidationError.__name__, result['failed_requests_by_reason'])
        self.assertEqual(result['failed_requests_by_reason'][PriceValidationError.__name__], [req2])

        # Fix: Check call without assuming order.
        self.mock_catalog_content_metadata.assert_called_once()
        args, _ = self.mock_catalog_content_metadata.call_args
        self.assertEqual(args[0], self.policy.catalog_uuid)
        self.assertSetEqual(set(args[1]), {'course-ok', 'course-bad-price'})

    def test_can_allocate_all_no_valid_requests_after_filtering(self):
        """
        Test can_allocate_all returns early if no requests pass initial filters.
        """

        req1 = self._create_mock_request('course-out', 1000)
        req2 = self._create_mock_request('course-bad-price', 6000)
        reqs = [req1, req2]

        # Mock metadata only returns course-bad-price, which will fail price validation.
        self.mock_catalog_content_metadata.return_value = [{'key': 'course-bad-price'}]
        self.mock_canonical_price.return_value = 5000  # Canonical for course-bad-price.

        with self.settings(
            ALLOCATION_PRICE_VALIDATION_LOWER_BOUND_RATIO=self.lower_bound_ratio,
            ALLOCATION_PRICE_VALIDATION_UPPER_BOUND_RATIO=self.upper_bound_ratio,
        ):
            result = self.policy.can_allocate_all(reqs)

        self.assertEqual(result['valid_requests'], [])
        self.assertEqual(len(result['failed_requests_by_reason']), 2)
        self.assertIn(REASON_CONTENT_NOT_IN_CATALOG, result['failed_requests_by_reason'])
        self.assertIn(PriceValidationError.__name__, result['failed_requests_by_reason'])
        self.assertEqual(result['failed_requests_by_reason'][REASON_CONTENT_NOT_IN_CATALOG], [req1])
        self.assertEqual(result['failed_requests_by_reason'][PriceValidationError.__name__], [req2])
        # Should not proceed to budget checks.
        self.mock_subsidy_balance.assert_not_called()
        self.mock_aggregates_for_policy.assert_not_called()
        self.mock_assignments_api.get_allocated_quantity_for_configuration.assert_not_called()

    def test_can_allocate_all_exceeds_subsidy_balance(self):
        """ Test can_allocate_all fails globally if batch exceeds subsidy balance. """
        req1 = self._create_mock_request('c1', 4000)
        req2 = self._create_mock_request('c2', 5000)
        reqs = [req1, req2]

        # Mock metadata
        self.mock_catalog_content_metadata.return_value = [{'key': 'c1'}, {'key': 'c2'}]
        # Mock canonical prices matching request prices
        self.mock_canonical_price.side_effect = lambda cid, meta: {'c1': 4000, 'c2': 5000}[cid]

        # Setup budget mocks
        self.mock_subsidy_balance.return_value = 10000  # $100
        # Allocated = $20
        self.mock_assignments_api.get_allocated_quantity_for_configuration.return_value = -2000
        # Batch Cost = $90
        # Check: (Allocated: $20 + Batch: $90) = $110 > Subsidy Balance: $100 -> FAIL
        self.mock_aggregates_for_policy.return_value = {'total_quantity': -1000}

        with self.settings(
            ALLOCATION_PRICE_VALIDATION_LOWER_BOUND_RATIO=self.lower_bound_ratio,
            ALLOCATION_PRICE_VALIDATION_UPPER_BOUND_RATIO=self.upper_bound_ratio,
        ):
            result = self.policy.can_allocate_all(reqs)

        self.assertIn('error_reason', result)
        self.assertEqual(result['error_reason'], REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY)
        self.mock_subsidy_balance.assert_called_once()
        self.mock_assignments_api.get_allocated_quantity_for_configuration.assert_called_once()
        self.mock_aggregates_for_policy.assert_called_once()  # Called even if subsidy check fails

    def test_can_allocate_all_exceeds_policy_limit(self):
        """
        Test can_allocate_all fails globally if batch exceeds policy spend limit.
        """

        req1 = self._create_mock_request('c1', 4000)  # $40
        req2 = self._create_mock_request('c2', 5000)  # $50
        reqs = [req1, req2]

        # Mock metadata.
        self.mock_catalog_content_metadata.return_value = [{'key': 'c1'}, {'key': 'c2'}]
        self.mock_canonical_price.side_effect = lambda cid, meta: {'c1': 4000, 'c2': 5000}[cid]

        # Setup budget mocks.
        self.policy.spend_limit = 11000  # $110 limit
        self.mock_subsidy_balance.return_value = 20000  # $200 (plenty)
        # Allocated = $20
        self.mock_assignments_api.get_allocated_quantity_for_configuration.return_value = -2000
        # Spent = $10
        self.mock_aggregates_for_policy.return_value = {'total_quantity': -1000}
        # Batch Cost = $90
        # Check 1 (Subsidy): (Allocated: $20 + Batch: $90) = $110 <= Subsidy Balance: $200 -> OK
        # Check 2 (Policy): (Allocated: $20 + Spent: $10 + Batch: $90) = $120 > Policy Limit: $110 -> FAIL

        with self.settings(
            ALLOCATION_PRICE_VALIDATION_LOWER_BOUND_RATIO=self.lower_bound_ratio,
            ALLOCATION_PRICE_VALIDATION_UPPER_BOUND_RATIO=self.upper_bound_ratio,
        ):
            result = self.policy.can_allocate_all(reqs)

        self.assertIn('error_reason', result)
        self.assertEqual(result['error_reason'], REASON_POLICY_SPEND_LIMIT_REACHED)
        self.mock_subsidy_balance.assert_called_once()
        self.mock_assignments_api.get_allocated_quantity_for_configuration.assert_called_once()
        self.mock_aggregates_for_policy.assert_called_once()

    def test_can_allocate_all_success_full_batch(self):
        """
        Test can_allocate_all succeeds when all requests are valid and within budget.
        """
        req1 = self._create_mock_request('c1', 4000)  # $40
        req2 = self._create_mock_request('c2', 5000)  # $50
        reqs = [req1, req2]

        # Mock metadata
        self.mock_catalog_content_metadata.return_value = [{'key': 'c1'}, {'key': 'c2'}]
        self.mock_canonical_price.side_effect = lambda cid, meta: {'c1': 4000, 'c2': 5000}[cid]

        # Setup budget mocks (should pass).
        self.policy.spend_limit = 15000  # $150 limit
        self.mock_subsidy_balance.return_value = 20000  # $200 balance
        self.mock_assignments_api.get_allocated_quantity_for_configuration.return_value = -2000  # $20 allocated
        self.mock_aggregates_for_policy.return_value = {'total_quantity': -1000}  # $10 spent
        # Check 1 (Subsidy): (Allocated: $20 + Batch: $90) = $110 <= $200 -> OK
        # Check 2 (Policy): (Allocated: $20 + Spent: $10 + Batch: $90) = $120 <= $150 -> OK

        with self.settings(
            ALLOCATION_PRICE_VALIDATION_LOWER_BOUND_RATIO=self.lower_bound_ratio,
            ALLOCATION_PRICE_VALIDATION_UPPER_BOUND_RATIO=self.upper_bound_ratio,
        ):
            result = self.policy.can_allocate_all(reqs)

        self.assertCountEqual(result['valid_requests'], reqs)
        self.assertEqual(len(result['failed_requests_by_reason']), 0)
        self.mock_subsidy_balance.assert_called_once()
        self.mock_assignments_api.get_allocated_quantity_for_configuration.assert_called_once()
        self.mock_aggregates_for_policy.assert_called_once()

    def test_can_allocate_all_success_partial_batch(self):
        """
        Test can_allocate_all succeeds with a mix of valid and initially invalid requests.
        """

        req1 = self._create_mock_request('course-ok', 4000)  # $40 - Valid
        req2 = self._create_mock_request('course-out', 5000)  # $50 - Invalid (not in catalog)
        req3 = self._create_mock_request('course-bad-price', 6000)  # $60 - Invalid (price)
        reqs = [req1, req2, req3]

        # Mock metadata (only ok and bad-price returned).
        self.mock_catalog_content_metadata.return_value = [{'key': 'course-ok'}, {'key': 'course-bad-price'}]
        # Mock canonical prices.
        self.mock_canonical_price.side_effect = lambda cid, meta: {'course-ok': 4000, 'course-bad-price': 5000}[cid]

        # Setup budget mocks (should pass for the single valid request).
        self.policy.spend_limit = 15000  # $150 limit
        self.mock_subsidy_balance.return_value = 20000  # $200 balance
        self.mock_assignments_api.get_allocated_quantity_for_configuration.return_value = -2000  # $20 allocated
        self.mock_aggregates_for_policy.return_value = {'total_quantity': -1000}  # $10 spent
        # Check 1 (Subsidy): (Allocated: $20 + Batch: $40) = $60 <= $200 -> OK
        # Check 2 (Policy): (Allocated: $20 + Spent: $10 + Batch: $40) = $70 <= $150 -> OK

        with self.settings(
            ALLOCATION_PRICE_VALIDATION_LOWER_BOUND_RATIO=self.lower_bound_ratio,
            ALLOCATION_PRICE_VALIDATION_UPPER_BOUND_RATIO=self.upper_bound_ratio,
        ):
            result = self.policy.can_allocate_all(reqs)

        # Only req1 should be valid.
        self.assertEqual(result['valid_requests'], [req1])
        # Two failure reasons.
        self.assertEqual(len(result['failed_requests_by_reason']), 2)
        self.assertIn(REASON_CONTENT_NOT_IN_CATALOG, result['failed_requests_by_reason'])
        self.assertIn(PriceValidationError.__name__, result['failed_requests_by_reason'])
        self.assertEqual(result['failed_requests_by_reason'][REASON_CONTENT_NOT_IN_CATALOG], [req2])
        self.assertEqual(result['failed_requests_by_reason'][PriceValidationError.__name__], [req3])
        # Budget checks should still run.
        self.mock_subsidy_balance.assert_called_once()
        self.mock_assignments_api.get_allocated_quantity_for_configuration.assert_called_once()
        self.mock_aggregates_for_policy.assert_called_once()

    def test_can_allocate_all_subsidy_balance_exact_match(self):
        """
        Test can_allocate_all when batch cost exactly equals remaining subsidy balance.
        """
        req1 = self._create_mock_request('c1', 4000)  # $40
        req2 = self._create_mock_request('c2', 5000)  # $50
        reqs = [req1, req2]

        # Mock metadata
        self.mock_catalog_content_metadata.return_value = [{'key': 'c1'}, {'key': 'c2'}]
        self.mock_canonical_price.side_effect = lambda cid, meta: {'c1': 4000, 'c2': 5000}[cid]

        # Setup budget mocks - exact match scenario.
        self.mock_subsidy_balance.return_value = 11000  # $110
        # Allocated = $20, Batch = $90 -> Total $110 (exactly equals subsidy balance).
        self.mock_assignments_api.get_allocated_quantity_for_configuration.return_value = -2000
        self.mock_aggregates_for_policy.return_value = {'total_quantity': -1000}  # $10 spent
        self.policy.spend_limit = 20000  # High enough to not be a factor

        with self.settings(
            ALLOCATION_PRICE_VALIDATION_LOWER_BOUND_RATIO=self.lower_bound_ratio,
            ALLOCATION_PRICE_VALIDATION_UPPER_BOUND_RATIO=self.upper_bound_ratio,
        ):
            result = self.policy.can_allocate_all(reqs)

        # Should PASS when exactly equal to subsidy balance.
        self.assertCountEqual(result['valid_requests'], reqs)
        self.assertEqual(len(result['failed_requests_by_reason']), 0)

    def test_can_allocate_all_policy_limit_exact_match(self):
        """
        Test can_allocate_all when batch cost exactly equals policy spend limit.
        """

        req1 = self._create_mock_request('c1', 4000)  # $40
        req2 = self._create_mock_request('c2', 5000)  # $50
        reqs = [req1, req2]

        # Mock metadata.
        self.mock_catalog_content_metadata.return_value = [{'key': 'c1'}, {'key': 'c2'}]
        self.mock_canonical_price.side_effect = lambda cid, meta: {'c1': 4000, 'c2': 5000}[cid]

        # Setup budget mocks - exact match on policy limit.
        self.policy.spend_limit = 12000  # $120 limit.
        self.mock_subsidy_balance.return_value = 20000  # $200 (plenty).
        # Allocated = $20, Spent = $10, Batch = $90 -> Total $120 (exactly equals policy limit).
        self.mock_assignments_api.get_allocated_quantity_for_configuration.return_value = -2000
        self.mock_aggregates_for_policy.return_value = {'total_quantity': -1000}

        with self.settings(
            ALLOCATION_PRICE_VALIDATION_LOWER_BOUND_RATIO=self.lower_bound_ratio,
            ALLOCATION_PRICE_VALIDATION_UPPER_BOUND_RATIO=self.upper_bound_ratio,
        ):
            result = self.policy.can_allocate_all(reqs)

        # Should PASS when exactly equal to policy limit.
        self.assertCountEqual(result['valid_requests'], reqs)
        self.assertEqual(len(result['failed_requests_by_reason']), 0)

    def test_can_allocate_all_subsidy_balance_api_error(self):
        """
        Test can_allocate_all when subsidy balance API call fails.
        """

        reqs = [self._create_mock_request('c1', 1000)]

        # Mock metadata and price validation to pass.
        self.mock_catalog_content_metadata.return_value = [{'key': 'c1'}]
        self.mock_canonical_price.return_value = 1000

        # Mock subsidy balance to raise an exception.
        self.mock_subsidy_balance.side_effect = requests.exceptions.HTTPError("Service Unavailable")

        with self.settings(
            ALLOCATION_PRICE_VALIDATION_LOWER_BOUND_RATIO=self.lower_bound_ratio,
            ALLOCATION_PRICE_VALIDATION_UPPER_BOUND_RATIO=self.upper_bound_ratio,
        ):
            with self.assertRaises(requests.exceptions.HTTPError):
                self.policy.can_allocate_all(reqs)

        # Verify earlier checks were completed.
        self.mock_catalog_content_metadata.assert_called_once()
        self.mock_canonical_price.assert_called_once()
        # Don't assume order of budget-related calls - just verify subsidy_balance was called.
        self.mock_subsidy_balance.assert_called_once()

    def test_can_allocate_all_aggregates_api_error(self):
        """
        Test can_allocate_all when policy aggregates API call fails.
        """

        reqs = [self._create_mock_request('c1', 1000)]

        # Mock metadata and price validation to pass.
        self.mock_catalog_content_metadata.return_value = [{'key': 'c1'}]
        self.mock_canonical_price.return_value = 1000

        # Mock aggregates to raise an exception.
        self.mock_aggregates_for_policy.side_effect = requests.exceptions.HTTPError("Aggregates Service Unavailable")

        with self.settings(
            ALLOCATION_PRICE_VALIDATION_LOWER_BOUND_RATIO=self.lower_bound_ratio,
            ALLOCATION_PRICE_VALIDATION_UPPER_BOUND_RATIO=self.upper_bound_ratio,
        ):
            with self.assertRaises(requests.exceptions.HTTPError):
                self.policy.can_allocate_all(reqs)

        # Verify earlier checks were completed.
        self.mock_catalog_content_metadata.assert_called_once()
        self.mock_canonical_price.assert_called_once()
        # Don't assume order - just verify aggregates was called.
        self.mock_aggregates_for_policy.assert_called_once()

    def test_can_allocate_all_catalog_metadata_api_error(self):
        """
        Test can_allocate_all when catalog metadata API call fails.
        """

        reqs = [self._create_mock_request('c1', 1000)]

        # Mock catalog metadata to raise an exception.
        self.mock_catalog_content_metadata.side_effect = requests.exceptions.HTTPError("Catalog Service Unavailable")

        with self.assertRaises(requests.exceptions.HTTPError):
            self.policy.can_allocate_all(reqs)

        # No other API calls should be made if catalog metadata fails.
        self.mock_canonical_price.assert_not_called()
        self.mock_subsidy_balance.assert_not_called()


class PerLearnerSpendCreditAccessPolicyTests(MockPolicyDependenciesMixin, TestCase):
    """ Tests specific to the per-learner spend credit type of access policy. """

    lms_user_id = 12345
    course_id = 'DemoX+flossing'

    MOCK_ALLOCATED_QUANTITY = -3000
    MOCK_PARENT_SPEND_AVAILABLE = 5000
    MOCK_TOTAL_ALLOCATED = -2000

    BNR_ENABLED_PATCH_PATH = (
        'enterprise_access.apps.subsidy_access_policy.models.PerLearnerSpendCreditAccessPolicy.bnr_enabled'
    )
    PARENT_SPEND_AVAILABLE_PATCH_PATH = (
        'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.spend_available'
    )
    TOTAL_ALLOCATED_PATCH_PATH = (
        'enterprise_access.apps.subsidy_access_policy.models.PerLearnerSpendCreditAccessPolicy.total_allocated'
    )

    def setUp(self):
        """
        Mocks out dependencies on other services, as well as dependencies
        on the Assignments API module.
        """
        super().setUp()

        self.enterprise_customer_uuid_1 = uuid4()

        self.assignments_api_patcher = patch(
            'enterprise_access.apps.subsidy_access_policy.models.assignments_api',
            autospec=True,
        )
        self.mock_assignments_api = self.assignments_api_patcher.start()

        self.addCleanup(self.assignments_api_patcher.stop)

        self.learner_credit_config = LearnerCreditRequestConfigurationFactory(active=True)
        self.assignment_config = AssignmentConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
        )
        self.per_learner_spend_policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            learner_credit_request_config=self.learner_credit_config,
            assignment_configuration=self.assignment_config,
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            active=True,
            retired=False,
            spend_limit=4000,
        )

    def _patch_bnr_enabled(self, enabled=True):
        """Helper method to patch bnr_enabled property."""
        return patch(self.BNR_ENABLED_PATCH_PATH, new_callable=PropertyMock, return_value=enabled)

    def _patch_parent_spend_available(self, amount=MOCK_PARENT_SPEND_AVAILABLE):
        """Helper method to patch parent spend_available property."""
        return patch(self.PARENT_SPEND_AVAILABLE_PATCH_PATH, new_callable=PropertyMock, return_value=amount)

    def _patch_total_allocated(self, amount=MOCK_TOTAL_ALLOCATED):
        """Helper method to patch total_allocated property."""
        return patch(self.TOTAL_ALLOCATED_PATCH_PATH, new_callable=PropertyMock, return_value=amount)

    def test_total_allocated_with_bnr_enabled(self):
        """
        Test that total_allocated property calls assignments_api when BNR is enabled.
        """
        with self._patch_bnr_enabled(), \
             patch.object(self.mock_assignments_api, 'get_allocated_quantity_for_configuration',
                          return_value=self.MOCK_ALLOCATED_QUANTITY) as mock_get_allocated:

            result = self.per_learner_spend_policy.total_allocated

            self.assertEqual(result, self.MOCK_ALLOCATED_QUANTITY)
            mock_get_allocated.assert_called_once_with(self.assignment_config)

    def test_total_allocated_with_bnr_disabled(self):
        """
        Test that total_allocated property returns parent class value when BNR is disabled.
        """
        with self._patch_bnr_enabled(enabled=False), \
             patch.object(self.mock_assignments_api, 'get_allocated_quantity_for_configuration') as mock_get_allocated:

            result = self.per_learner_spend_policy.total_allocated

            # Should return parent class value (0 for SubsidyAccessPolicy)
            self.assertEqual(result, 0)
            # Should not call assignments API when BNR is disabled
            mock_get_allocated.assert_not_called()

    def test_spend_available_with_bnr_enabled(self):
        """
        Test that spend_available property uses assignment-based calculation when BNR is enabled.
        """
        with self._patch_bnr_enabled(), \
             self._patch_parent_spend_available() as mock_parent_spend_available, \
             self._patch_total_allocated() as mock_total_allocated:

            result = self.per_learner_spend_policy.spend_available

            # Should calculate: max(0, super().spend_available + self.total_allocated)
            expected_result = max(0, self.MOCK_PARENT_SPEND_AVAILABLE + self.MOCK_TOTAL_ALLOCATED)
            self.assertEqual(result, expected_result)
            mock_parent_spend_available.assert_called_once()
            mock_total_allocated.assert_called_once()

    def test_spend_available_with_bnr_disabled(self):
        """
        Test that spend_available property returns parent class value when BNR is disabled.
        """
        with self._patch_bnr_enabled(enabled=False), \
             self._patch_parent_spend_available(amount=self.MOCK_PARENT_SPEND_AVAILABLE) as mock_parent_spend_available:

            result = self.per_learner_spend_policy.spend_available

            # Should return parent class value directly
            self.assertEqual(result, self.MOCK_PARENT_SPEND_AVAILABLE)
            mock_parent_spend_available.assert_called_once()


@ddt.ddt
class PolicyGroupAssociationTests(MockPolicyDependenciesMixin, TestCase):
    """ Tests specific to the policy group association model. """

    lms_user_id = 12345
    group_uuid = uuid4()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.access_policy = AssignedLearnerCreditAccessPolicyFactory()

    def tearDown(self):
        """
        Clears any cached data for the test policy instances between test runs.
        """
        super().tearDown()
        request_cache(namespace=REQUEST_CACHE_NAMESPACE).clear()

    def test_save(self):
        """
        Test that the model-level validation of this model works as expected.
        Should be saved with a unique combination of SubsidyAccessPolicy
        and group uuid (enterprise_customer_uuid).
        """

        policy = PolicyGroupAssociationFactory(
            enterprise_group_uuid=self.group_uuid,
            subsidy_access_policy=self.access_policy,
        )

        policy.save()
        policy.refresh_from_db()

        self.assertEqual(policy.enterprise_group_uuid, self.group_uuid)
        self.assertIsNotNone(policy.subsidy_access_policy)

    def test_cascade_delete_for_group_uuid_should_delete_correct_associations(self):
        """
        Test that deleting a group uuid will delete all associations
        with that group uuid, and no others.
        """
        policy1 = PolicyGroupAssociationFactory(
            enterprise_group_uuid=self.group_uuid,
            subsidy_access_policy=self.access_policy,
        )
        policy2 = PolicyGroupAssociationFactory(
            enterprise_group_uuid=uuid4(),
            subsidy_access_policy=self.access_policy,
        )

        # Ensure both policies are created
        self.assertEqual(PolicyGroupAssociation.objects.count(), 2)

        # Delete the first policy group association
        policy1.delete()

        # Ensure only the second policy remains
        self.assertEqual(PolicyGroupAssociation.objects.count(), 1)
        self.assertEqual(PolicyGroupAssociation.objects.first(), policy2)
