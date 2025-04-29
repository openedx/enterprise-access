"""
Tests the ForcedPolicyRedemption model.
"""
from unittest import mock
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from enterprise_access.apps.content_assignments.models import LearnerContentAssignment
from enterprise_access.apps.content_assignments.tests.factories import AssignmentConfigurationFactory
from enterprise_access.apps.subsidy_access_policy.constants import FORCE_ENROLLMENT_KEYWORD
from enterprise_access.apps.subsidy_access_policy.exceptions import (
    SubsidyAccessPolicyLockAttemptFailed,
    SubsidyAPIHTTPError
)
from enterprise_access.apps.subsidy_access_policy.models import REQUEST_CACHE_NAMESPACE
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    AssignedLearnerCreditAccessPolicyFactory,
    ForcedPolicyRedemptionFactory,
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory
)
from enterprise_access.cache_utils import request_cache

from .mixins import MockPolicyDependenciesMixin

ACTIVE_LEARNER_SPEND_CAP_ENTERPRISE_UUID = uuid4()
ACTIVE_LEARNER_SPEND_CAP_POLICY_UUID = uuid4()

MOCK_DATETIME_1 = timezone.now()

MOCK_TRANSACTION_UUID_1 = uuid4()


class BaseForcedRedemptionTestCase(MockPolicyDependenciesMixin, TestCase):
    """
    Provides base functionality for tests of forced redemption.
    """
    lms_user_id = 12345
    course_run_key = 'course-v1:edX+DemoX+1T2042'
    course_key = 'edX+DemoX'
    default_content_price = 200

    def tearDown(self):
        """
        Clears any cached data for the test policy instances between test runs.
        """
        super().tearDown()
        request_cache(namespace=REQUEST_CACHE_NAMESPACE).clear()

    def _setup_redemption_state(
        self, content_price=None, course_key=None, course_run_key=None, can_redeem=True,
        subsidy_is_active=True, existing_transactions=None, existing_aggregates=None, user_email=None,
    ):
        """
        Helper to setup state of content metadata and the subsidy/transactions
        related to a policy/budget prior to forced redemption occuring.
        """
        self.mock_get_content_metadata.return_value = {
            'content_price': content_price or self.default_content_price,
            'content_key': course_key or self.course_key,
            'course_run_key': course_run_key or self.course_run_key,
        }
        self.mock_subsidy_client.can_redeem.return_value = {
            'can_redeem': can_redeem,
            'active': subsidy_is_active,
        }
        self.mock_transactions_cache_for_learner.return_value = {
            'transactions': existing_transactions or [],
            'aggregates': existing_aggregates or {'total_quantity': 0},
        }
        self.mock_subsidy_client.list_subsidy_transactions.return_value = {
            'results': existing_transactions or [],
            'aggregates': existing_aggregates or {'total_quantity': 0},
        }
        self.mock_subsidy_client.create_subsidy_transaction.return_value = {
            'uuid': MOCK_TRANSACTION_UUID_1,
            'modified': MOCK_DATETIME_1,
        }
        self.mock_lms_api_client.get_enterprise_user.return_value = {
            'user': {
                'email': user_email,
            }
        }


class ForcedPolicyRedemptionPerLearnerSpendTests(BaseForcedRedemptionTestCase):
    """
    Tests forced redemption against PerLearnerSpendCapLearnerCreditAccessPolicies.
    """
    def _new_per_learner_spend_budget(self, spend_limit, per_learner_spend_limit):
        return PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=ACTIVE_LEARNER_SPEND_CAP_ENTERPRISE_UUID,
            spend_limit=spend_limit,
            per_learner_spend_limit=per_learner_spend_limit,
        )

    def test_force_redemption_happy_path(self):
        """
        Starting from a clean, unspent state of some policy's subsidy,
        test that we can force redemption.
        """
        policy = self._new_per_learner_spend_budget(spend_limit=10000, per_learner_spend_limit=1000)
        self._setup_redemption_state()

        forced_redemption_record = ForcedPolicyRedemptionFactory(
            subsidy_access_policy=policy,
            lms_user_id=self.lms_user_id,
            course_run_key=self.course_run_key,
            content_price_cents=self.default_content_price,
        )

        extra_metadata = {'foo': 1, 'bar': 2}
        forced_redemption_record.force_redeem(extra_metadata=extra_metadata)

        forced_redemption_record.refresh_from_db()
        self.assertEqual(MOCK_DATETIME_1, forced_redemption_record.redeemed_at)
        self.assertEqual(MOCK_TRANSACTION_UUID_1, forced_redemption_record.transaction_uuid)

        self.mock_subsidy_client.create_subsidy_transaction.assert_called_once_with(
            subsidy_uuid=str(policy.subsidy_uuid),
            lms_user_id=self.lms_user_id,
            content_key=self.course_run_key,
            subsidy_access_policy_uuid=str(policy.uuid),
            metadata={FORCE_ENROLLMENT_KEYWORD: True, **extra_metadata},
            idempotency_key=mock.ANY,
        )

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.localized_utcnow', return_value=MOCK_DATETIME_1)
    def test_acquire_lock_fails(self, _):
        """
        Test that we don't force a redemption when the requested policy is locked.
        """
        policy = self._new_per_learner_spend_budget(spend_limit=10000, per_learner_spend_limit=1000)
        policy.acquire_lock()

        forced_redemption_record = ForcedPolicyRedemptionFactory(
            subsidy_access_policy=policy,
            lms_user_id=self.lms_user_id,
            course_run_key=self.course_run_key,
            content_price_cents=self.default_content_price,
        )

        with self.assertRaisesRegex(SubsidyAccessPolicyLockAttemptFailed, 'Failed to acquire lock'):
            forced_redemption_record.force_redeem()
            forced_redemption_record.refresh_from_db()
            self.assertEqual(MOCK_DATETIME_1, forced_redemption_record.errored_at)
            self.assertIn('Failed to acquire lock', forced_redemption_record.traceback)

        # release the lock when we're done
        policy.release_lock()

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.localized_utcnow', return_value=MOCK_DATETIME_1)
    def test_subsidy_api_fails(self, _):
        """
        Test that we don't force a redemption when the subsidy API fails.
        """
        policy = self._new_per_learner_spend_budget(spend_limit=10000, per_learner_spend_limit=1000)
        self._setup_redemption_state()

        self.mock_subsidy_client.create_subsidy_transaction.side_effect = SubsidyAPIHTTPError

        forced_redemption_record = ForcedPolicyRedemptionFactory(
            subsidy_access_policy=policy,
            lms_user_id=self.lms_user_id,
            course_run_key=self.course_run_key,
            content_price_cents=self.default_content_price,
        )

        with self.assertRaisesRegex(SubsidyAPIHTTPError, 'HTTPError occurred in Subsidy API request'):
            forced_redemption_record.force_redeem()
            forced_redemption_record.refresh_from_db()
            self.assertEqual(MOCK_DATETIME_1, forced_redemption_record.errored_at)
            self.assertIn('HTTPError occurred in Subsidy API request', forced_redemption_record.traceback)


class ForcedPolicyRedemptionAssignmentTests(BaseForcedRedemptionTestCase):
    """
    Tests forced redemption against Assignment-based policies.
    """
    def setUp(self):
        """
        Mocks out the ``content_assignments.api.get_and_cache_content_metadata`` function.
        """
        super().setUp()

        mock_assignment_content_metadata_patcher = mock.patch(
            'enterprise_access.apps.content_assignments.api.get_and_cache_content_metadata',
        )
        self.mock_assignment_content_metadata = mock_assignment_content_metadata_patcher.start()
        self.addCleanup(mock_assignment_content_metadata_patcher.stop)

    def _setup_redemption_state(
        self, content_price=None, course_key=None, course_run_key=None, can_redeem=True,
        subsidy_is_active=True, existing_transactions=None, existing_aggregates=None, user_email=None,
    ):
        """
        Setup state of the assignment content metadata mock.
        """
        super()._setup_redemption_state(
            content_price=content_price, course_key=course_key, course_run_key=course_run_key,
            can_redeem=can_redeem, subsidy_is_active=subsidy_is_active, existing_transactions=existing_transactions,
            existing_aggregates=existing_aggregates, user_email=user_email,
        )
        self.mock_assignment_content_metadata.return_value = {
            'content_price': content_price or self.default_content_price,
            'content_key': course_key or self.course_key,
            'course_run_key': course_run_key or self.course_run_key,
        }

    def _new_assignment_budget(self):
        """
        Helper to setup a new assignment-based budget.
        """
        customer_uuid = uuid4()
        assignment_config = AssignmentConfigurationFactory(
            enterprise_customer_uuid=customer_uuid,
        )
        return AssignedLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=customer_uuid,
            assignment_configuration=assignment_config,
        )

    @mock.patch('enterprise_access.apps.content_assignments.api.send_email_for_new_assignment')
    @mock.patch(
        'enterprise_access.apps.content_assignments.api.create_pending_enterprise_learner_for_assignment_task'
    )
    def test_force_redemption_with_assignment_happy_path(self, mock_pending_learner_task, mock_send_email):
        """
        Starting from a clean, unspent state of some policy's subsidy,
        test that we can force redemption.
        """
        policy = self._new_assignment_budget()
        self._setup_redemption_state(user_email='Alice@foo.com')

        forced_redemption_record = ForcedPolicyRedemptionFactory(
            subsidy_access_policy=policy,
            lms_user_id=self.lms_user_id,
            course_run_key=self.course_run_key,
            content_price_cents=self.default_content_price,
        )

        forced_redemption_record.force_redeem()

        forced_redemption_record.refresh_from_db()
        self.assertEqual(MOCK_DATETIME_1, forced_redemption_record.redeemed_at)
        self.assertEqual(MOCK_TRANSACTION_UUID_1, forced_redemption_record.transaction_uuid)

        self.mock_subsidy_client.create_subsidy_transaction.assert_called_once_with(
            subsidy_uuid=str(policy.subsidy_uuid),
            lms_user_id=self.lms_user_id,
            content_key=self.course_run_key,
            subsidy_access_policy_uuid=str(policy.uuid),
            metadata={FORCE_ENROLLMENT_KEYWORD: True},
            idempotency_key=mock.ANY,
            requested_price_cents=self.default_content_price,
        )

        assignment = LearnerContentAssignment.objects.filter(lms_user_id=self.lms_user_id).first()
        self.assertEqual(assignment.content_key, self.course_run_key)
        self.assertEqual(assignment.learner_email, 'Alice@foo.com')
        mock_send_email.delay.assert_called_once_with(assignment.uuid)
        mock_pending_learner_task.delay.assert_called_once_with(assignment.uuid)
