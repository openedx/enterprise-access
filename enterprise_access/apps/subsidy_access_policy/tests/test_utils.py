"""
Tests for subsidy_access_policy utils.
"""
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import TestCase

from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    AssignedLearnerCreditAccessPolicyFactory,
    PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory,
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory
)
from enterprise_access.apps.subsidy_access_policy.utils import (
    create_idempotency_key_for_transaction,
    sort_subsidy_access_policies_for_redemption
)


class SubsidyAccessPolicyUtilsTests(TestCase):
    """
    SubsidyAccessPolicy utils tests.
    """

    def setUp(self):
        super().setUp()

        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)
        tomorrow = now + timedelta(days=1)
        day_after_tomorrow = now + timedelta(days=2)

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
        self.policy_five = AssignedLearnerCreditAccessPolicyFactory.create()

        # Map policies to subsidy mocks
        self.policy_subsidy_map = {
            self.policy_one.pk: self.mock_subsidy_one,
            self.policy_two.pk: self.mock_subsidy_two,
            self.policy_three.pk: self.mock_subsidy_three,
            self.policy_four.pk: self.mock_subsidy_four,
            self.policy_five.pk: self.mock_subsidy_one,
        }

        policy_subsidy_map = self.policy_subsidy_map

        def mocked_subsidy_record(policy_instance):
            return policy_subsidy_map[policy_instance.pk]

        self.subsidy_record_patcher = patch(
            'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_record',
            new=mocked_subsidy_record
        )
        self.subsidy_record_patcher.start()
        self.addCleanup(self.subsidy_record_patcher.stop)

    def test_setup(self):
        """
        Ensure each policy has the correctly mocked subsidy object.
        """
        assert self.policy_one.subsidy_record() == self.mock_subsidy_one
        assert self.policy_two.subsidy_record() == self.mock_subsidy_two
        assert self.policy_three.subsidy_record() == self.mock_subsidy_three
        assert self.policy_four.subsidy_record() == self.mock_subsidy_four
        assert self.policy_five.subsidy_record() == self.mock_subsidy_one

    def test_create_idempotency_key_for_transaction_fixed_output(self):
        """
        Test happy path for creating an idempotency key.

        Also catch any code change which might alter the output given a fixed input.  At the time of writing, we're
        relying only on the idempotency key as a long-term uniqueness constraint on transactions, so this test should
        help draw attention to any logic changes which would result in allowing transactions to be created when they
        should not.
        """
        expected_idempotency_key = \
            "ledger-for-subsidy-f492b063-42bb-40dd-8464-6b5668329f1d-423a5bd5f0c19e7d750b03e9f21a04c7"
        inputs = {
            "subsidy_uuid": "f492b063-42bb-40dd-8464-6b5668329f1d",
            "lms_user_id": 12345,
            "content_key": "course-v1:edX+test+content",
            "subsidy_access_policy_uuid": "4ca49903-b66a-4fe9-a139-79d829deb944",
            "historical_redemptions_uuids": [
                "d459818c-458d-485b-a758-a0a060f8a7c4",
                "69beac67-52e2-47e0-994b-b0e1fac85845",
            ],
        }
        actual_idempotency_key_1 = create_idempotency_key_for_transaction(**inputs)
        actual_idempotency_key_2 = create_idempotency_key_for_transaction(foo="bar", **inputs)
        assert actual_idempotency_key_1 == expected_idempotency_key
        assert actual_idempotency_key_2 == expected_idempotency_key

    def test_create_idempotency_key_for_transaction_collisions(self):
        """
        Verify that the idempotency key is either unique OR collides whenever it should.

        This test is not concerned with key formatting or prefixes.
        """
        baseline_inputs = {
            "subsidy_uuid": str(uuid.uuid4()),
            "lms_user_id": 12345,
            "content_key": "course-v1:edX+test+content",
            "subsidy_access_policy_uuid": str(uuid.uuid4()),
            "historical_redemptions_uuids": [],
            "foo": "foo-value",
        }
        # Applying any of these modifications SHOULD change the idempotency key.
        modified_inputs_should_change_output = {
            "subsidy_uuid": str(uuid.uuid4()),
            "lms_user_id": 23456,
            "content_key": "course-v1:edX+test+content_different",
            "subsidy_access_policy_uuid": str(uuid.uuid4()),
            "historical_redemptions_uuids": [str(uuid.uuid4())],
        }
        # Applying any of these modifications SHOULD NOT change the idempotency key.
        modified_inputs_should_not_change_output = {
            "foo": "foo-value-different",
            "bar": "bar-value",
        }

        baseline_key = create_idempotency_key_for_transaction(**baseline_inputs)

        different_keys = set([baseline_key])
        for modify_key, new_value in modified_inputs_should_change_output.items():
            modified_inputs = baseline_inputs.copy()
            modified_inputs[modify_key] = new_value
            different_keys.add(create_idempotency_key_for_transaction(**modified_inputs))

        same_keys = set([baseline_key])
        for modify_key, new_value in modified_inputs_should_not_change_output.items():
            modified_inputs = baseline_inputs.copy()
            modified_inputs[modify_key] = new_value
            same_keys.add(create_idempotency_key_for_transaction(**modified_inputs))

        assert len(different_keys) == len(modified_inputs_should_change_output) + 1
        assert len(same_keys) == 1

    def test_sort_subsidy_access_policies_for_redemption_priority(self):
        """
        Test resolve given two policies with different balances, different expiration
        the sooner expiration policy should be returned.
        """
        queryset = SubsidyAccessPolicy.objects.filter(pk__in=[
            self.policy_one.pk,
            self.policy_five.pk,
            self.policy_four.pk,
        ])
        sorted_policies = sort_subsidy_access_policies_for_redemption(queryset=queryset)
        assert sorted_policies[0] == self.policy_five

    def test_sort_subsidy_access_policies_for_redemption_subsidy_balance(self):
        """
        Test resolve given two policies with same balances, same expiration
        but different type-priority.
        """
        queryset = SubsidyAccessPolicy.objects.filter(pk__in=[
            self.policy_one.pk,
            self.policy_two.pk
        ])
        sorted_policies = sort_subsidy_access_policies_for_redemption(queryset=queryset)
        assert sorted_policies[0] == self.policy_two

    def test_sort_subsidy_access_policies_for_redemption_expiration(self):
        """
        Test resolve given two policies with different balances, different expiration
        the sooner expiration policy should be returned.
        """
        queryset = SubsidyAccessPolicy.objects.filter(pk__in=[
            self.policy_three.pk,
            self.policy_one.pk
        ])
        sorted_policies = sort_subsidy_access_policies_for_redemption(queryset=queryset)
        assert sorted_policies[0] == self.policy_one
