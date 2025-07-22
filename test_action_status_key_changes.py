#!/usr/bin/env python
"""
Test file to verify that status and recent_action return keys instead of display values.

This test verifies the changes made to get_status() and get_recent_action() methods
in LearnerCreditRequestActionsSerializer to return raw key values instead of
display values from the choices dictionaries.

Run with: python test_action_status_key_changes.py
"""

import os
import sys
import django
from datetime import datetime

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'enterprise_access.settings.test')
django.setup()

from django.test import TestCase
from enterprise_access.apps.subsidy_request.constants import (
    SubsidyRequestStates,
    LearnerCreditAdditionalActionStates,
    LearnerCreditRequestUserMessages,
    LearnerCreditRequestActionChoices
)
from enterprise_access.apps.subsidy_request.tests.factories import (
    LearnerCreditRequestFactory,
    LearnerCreditRequestActionsFactory,
    LearnerCreditRequestConfigurationFactory,
)
from enterprise_access.apps.api.serializers.subsidy_requests import LearnerCreditRequestActionsSerializer


class TestActionStatusKeyChanges(TestCase):
    """Test that status and recent_action return keys instead of display values."""

    def setUp(self):
        """Set up test data."""
        self.config = LearnerCreditRequestConfigurationFactory()
        self.request = LearnerCreditRequestFactory(
            state=SubsidyRequestStates.APPROVED,
            learner_credit_request_config=self.config
        )

    def test_status_returns_key_not_display_value(self):
        """Test that get_status returns the raw key value, not the display value."""
        print("\n" + "="*60)
        print("TESTING STATUS RETURNS KEY NOT DISPLAY VALUE")
        print("="*60)

        # Test cases: status_key -> what display value WOULD be if we were still looking it up
        test_cases = [
            (SubsidyRequestStates.REQUESTED, "Requested"),
            (LearnerCreditAdditionalActionStates.REMINDED, "Waiting For Learner"),
            (SubsidyRequestStates.APPROVED, "Waiting For Learner"),
            (SubsidyRequestStates.ACCEPTED, "Redeemed By Learner"),
            (SubsidyRequestStates.DECLINED, "Declined"),
            (SubsidyRequestStates.CANCELLED, "Cancelled"),
        ]

        for status_key, old_display_value in test_cases:
            print(f"\n--- Testing status: {status_key} ---")

            # Create action with specific status
            action = LearnerCreditRequestActionsFactory(
                learner_credit_request=self.request,
                status=status_key,
                recent_action=SubsidyRequestStates.APPROVED  # Use different value for recent_action
            )

            # Serialize the action
            serializer = LearnerCreditRequestActionsSerializer(action)
            data = serializer.data

            # Verify that status returns the KEY, not the display value
            actual_status = data.get('status')
            self.assertEqual(actual_status, status_key,
                           f"status should return key '{status_key}', not display value. Got '{actual_status}'")
            print(f"✅ status field returns key: '{actual_status}'")

            # Verify it's NOT returning the old display value
            self.assertNotEqual(actual_status, old_display_value,
                              f"status should NOT return display value '{old_display_value}', should return key '{status_key}'")
            print(f"✅ status field correctly does NOT return display value: '{old_display_value}'")

            # Clean up
            action.delete()

    def test_recent_action_returns_key_not_display_value(self):
        """Test that get_recent_action returns the raw key value, not the display value."""
        print(f"\n{'-'*60}")
        print("TESTING RECENT_ACTION RETURNS KEY NOT DISPLAY VALUE")
        print(f"{'-'*60}")

        # Test cases: action_key -> what display value WOULD be
        action_test_cases = [
            (SubsidyRequestStates.REQUESTED, "Requested"),
            (SubsidyRequestStates.APPROVED, "Approved"),
            (SubsidyRequestStates.DECLINED, "Declined"),
            (LearnerCreditAdditionalActionStates.REMINDED, "Reminded"),
            (SubsidyRequestStates.CANCELLED, "Cancelled"),
        ]

        for action_key, old_display_value in action_test_cases:
            print(f"\n--- Testing recent_action: {action_key} ---")

            # Create action with specific recent_action
            action = LearnerCreditRequestActionsFactory(
                learner_credit_request=self.request,
                recent_action=action_key,
                status=SubsidyRequestStates.APPROVED  # Use different value for status
            )

            # Serialize the action
            serializer = LearnerCreditRequestActionsSerializer(action)
            data = serializer.data

            # Verify that recent_action returns the KEY, not the display value
            actual_recent_action = data.get('recent_action')
            self.assertEqual(actual_recent_action, action_key,
                           f"recent_action should return key '{action_key}', not display value. Got '{actual_recent_action}'")
            print(f"✅ recent_action field returns key: '{actual_recent_action}'")

            # Verify it's NOT returning the old display value
            self.assertNotEqual(actual_recent_action, old_display_value,
                              f"recent_action should NOT return display value '{old_display_value}', should return key '{action_key}'")
            print(f"✅ recent_action field correctly does NOT return display value: '{old_display_value}'")

            # Clean up
            action.delete()

    def test_duplicate_display_values_now_distinguishable(self):
        """Test that statuses with same old display value are now distinguishable by their keys."""
        print(f"\n{'-'*60}")
        print("TESTING DUPLICATE DISPLAY VALUES NOW DISTINGUISHABLE")
        print(f"{'-'*60}")

        # Both 'reminded' and 'approved' used to return "Waiting For Learner"
        # Now they should return their actual keys
        test_cases = [
            (LearnerCreditAdditionalActionStates.REMINDED, "reminded"),
            (SubsidyRequestStates.APPROVED, "approved"),
        ]

        results = []

        for status_key, expected_key in test_cases:
            print(f"\nTesting status: {status_key}")

            action = LearnerCreditRequestActionsFactory(
                learner_credit_request=self.request,
                status=status_key,
                recent_action=status_key
            )

            serializer = LearnerCreditRequestActionsSerializer(action)
            data = serializer.data
            results.append(data)

            # Verify both status and recent_action return the key
            actual_status = data.get('status')
            actual_recent_action = data.get('recent_action')

            self.assertEqual(actual_status, status_key)
            self.assertEqual(actual_recent_action, status_key)

            print(f"  status: '{actual_status}' (key)")
            print(f"  recent_action: '{actual_recent_action}' (key)")

            action.delete()

        # Verify that the two results are now distinguishable
        status1 = results[0].get('status')
        status2 = results[1].get('status')

        self.assertNotEqual(status1, status2,
                          "Status values should now be distinguishable by their keys")

        print(f"\n✅ SUCCESS: Previously identical display values are now distinguishable:")
        print(f"   Status 1: '{status1}' (was 'Waiting For Learner')")
        print(f"   Status 2: '{status2}' (was 'Waiting For Learner')")

    def test_error_reason_unchanged(self):
        """Test that error_reason behavior is unchanged (still returns display value)."""
        print(f"\n{'-'*60}")
        print("TESTING ERROR_REASON BEHAVIOR UNCHANGED")
        print(f"{'-'*60}")

        from enterprise_access.apps.subsidy_request.constants import LearnerCreditRequestActionErrorReasons

        # error_reason should still return display values (unchanged)
        action = LearnerCreditRequestActionsFactory(
            learner_credit_request=self.request,
            status=SubsidyRequestStates.APPROVED,
            recent_action=SubsidyRequestStates.APPROVED,
            error_reason=LearnerCreditRequestActionErrorReasons.FAILED_APPROVAL
        )

        serializer = LearnerCreditRequestActionsSerializer(action)
        data = serializer.data

        # error_reason should still return display value (behavior unchanged)
        actual_error_reason = data.get('error_reason')
        expected_display = "Failed: Approval"  # From the choices

        self.assertEqual(actual_error_reason, expected_display,
                        "error_reason should still return display value (unchanged behavior)")

        print(f"✅ error_reason still returns display value: '{actual_error_reason}'")

        action.delete()

    def test_api_response_structure(self):
        """Test the overall API response structure with the changes."""
        print(f"\n{'-'*60}")
        print("TESTING API RESPONSE STRUCTURE")
        print(f"{'-'*60}")

        action = LearnerCreditRequestActionsFactory(
            learner_credit_request=self.request,
            status=SubsidyRequestStates.APPROVED,
            recent_action=LearnerCreditAdditionalActionStates.REMINDED,
            error_reason=None
        )

        serializer = LearnerCreditRequestActionsSerializer(action)
        data = serializer.data

        # Verify all expected fields exist
        expected_fields = [
            'uuid', 'recent_action', 'status', 'error_reason',
            'traceback', 'created', 'modified'
        ]

        for field in expected_fields:
            self.assertIn(field, data, f"Field '{field}' should exist in serialized data")

        # Verify the key fields return keys
        self.assertEqual(data.get('status'), SubsidyRequestStates.APPROVED)
        self.assertEqual(data.get('recent_action'), LearnerCreditAdditionalActionStates.REMINDED)

        print("✅ API response structure correct:")
        print(f"   status: '{data.get('status')}' (key)")
        print(f"   recent_action: '{data.get('recent_action')}' (key)")
        print(f"   error_reason: {data.get('error_reason')}")

        action.delete()


def run_tests():
    """Run all tests and provide summary."""
    import unittest

    print("Starting Action Status Key Changes Tests...")
    print(f"Time: {datetime.now()}")

    # Create test suite
    suite = unittest.TestSuite()
    suite.addTest(TestActionStatusKeyChanges('test_status_returns_key_not_display_value'))
    suite.addTest(TestActionStatusKeyChanges('test_recent_action_returns_key_not_display_value'))
    suite.addTest(TestActionStatusKeyChanges('test_duplicate_display_values_now_distinguishable'))
    suite.addTest(TestActionStatusKeyChanges('test_error_reason_unchanged'))
    suite.addTest(TestActionStatusKeyChanges('test_api_response_structure'))

    # Run tests
    runner = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, 'w'))
    result = runner.run(suite)

    # Print summary
    print(f"\n{'='*60}")
    print("TEST SUMMARY")
    print(f"{'='*60}")

    if result.wasSuccessful():
        print(f"✅ ALL {result.testsRun} TESTS PASSED!")
        print("\n🎉 Action status key changes are working correctly!")
        print("\nChanges verified:")
        print("  ✅ get_status() now returns key instead of display value")
        print("  ✅ get_recent_action() now returns key instead of display value")
        print("  ✅ Previously duplicate display values are now distinguishable")
        print("  ✅ error_reason behavior unchanged (still returns display value)")
        print("  ✅ API response structure maintained")
        print("\nThis resolves admin interface duplicate dropdown issues!")
    else:
        print(f"❌ {len(result.failures)} TESTS FAILED, {len(result.errors)} ERRORS")

        for failure in result.failures:
            print(f"\nFAILURE in {failure[0]}:")
            print(failure[1])

        for error in result.errors:
            print(f"\nERROR in {error[0]}:")
            print(error[1])

    print(f"\nTest completed at: {datetime.now()}")
    return result.wasSuccessful()


if __name__ == '__main__':
    try:
        success = run_tests()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\nTest execution failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
