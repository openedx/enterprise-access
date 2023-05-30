"""
Tests for subsidy_access_policy utils.
"""
import uuid

from django.test import TestCase

from enterprise_access.apps.subsidy_access_policy.utils import create_idempotency_key_for_transaction


class SubsidyAccessPolicyUtilsTests(TestCase):
    """
    SubsidyAccessPolicy utils tests.
    """

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
