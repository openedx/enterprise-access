"""
Tests for the mark_stalled_checkout_intents management command.
"""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.constants import CheckoutIntentState
from enterprise_access.apps.customer_billing.models import CheckoutIntent


class MarkStalledCheckoutIntentsCommandTests(TestCase):
    """Tests for the mark_stalled_checkout_intents management command."""

    def setUp(self):
        self.now = timezone.now()
        self.user_a = UserFactory()
        self.user_b = UserFactory()
        self.user_c = UserFactory()
        self.user_d = UserFactory()

        # Create a stalled paid intent (5 minutes old)
        self.stalled_intent = CheckoutIntent.objects.create(
            user=self.user_a,
            state=CheckoutIntentState.PAID,
            enterprise_name="Stalled Enterprise",
            enterprise_slug="stalled-enterprise",
            quantity=10,
            expires_at=self.now + timedelta(hours=1),
            country='US',
            terms_metadata={'version': '1.0'}
        )
        # Manually set modified time to 5 minutes ago
        CheckoutIntent.objects.filter(pk=self.stalled_intent.pk).update(
            modified=self.now - timedelta(seconds=300)
        )
        self.stalled_intent.refresh_from_db()

        # Create a recent paid intent (1 minute old, not stalled)
        self.recent_paid_intent = CheckoutIntent.objects.create(
            user=self.user_b,
            state=CheckoutIntentState.PAID,
            enterprise_name="Recent Enterprise",
            enterprise_slug="recent-enterprise",
            quantity=5,
            expires_at=self.now + timedelta(hours=1),
            country='CA',
            terms_metadata={'version': '1.1'}
        )
        CheckoutIntent.objects.filter(pk=self.recent_paid_intent.pk).update(
            modified=self.now - timedelta(seconds=60)
        )
        self.recent_paid_intent.refresh_from_db()

        # Create a fulfilled intent (should be ignored)
        self.fulfilled_intent = CheckoutIntent.objects.create(
            user=self.user_c,
            state=CheckoutIntentState.FULFILLED,
            enterprise_name="Fulfilled Enterprise",
            enterprise_slug="fulfilled-enterprise",
            quantity=15,
            expires_at=self.now + timedelta(hours=1),
            country='GB',
            terms_metadata={'version': '2.0'}
        )
        CheckoutIntent.objects.filter(pk=self.fulfilled_intent.pk).update(
            modified=self.now - timedelta(seconds=300)
        )
        self.fulfilled_intent.refresh_from_db()

        # Create a created state intent (should be ignored)
        self.created_intent = CheckoutIntent.objects.create(
            user=self.user_d,
            state=CheckoutIntentState.CREATED,
            enterprise_name="Created Enterprise",
            enterprise_slug="created-enterprise",
            quantity=20,
            expires_at=self.now + timedelta(hours=1),
            country='DE',
            terms_metadata={'version': '3.0'}
        )
        CheckoutIntent.objects.filter(pk=self.created_intent.pk).update(
            modified=self.now - timedelta(seconds=300)
        )
        self.created_intent.refresh_from_db()

    def test_no_stalled_intents(self):
        """Test command when no stalled intents exist."""
        # Mark the stalled intent as recent
        CheckoutIntent.objects.filter(pk=self.stalled_intent.pk).update(
            modified=self.now - timedelta(seconds=60)
        )

        out = StringIO()
        call_command('mark_stalled_checkout_intents', stdout=out)
        output = out.getvalue()

        self.assertIn('No stalled CheckoutIntent records found', output)

    def test_marks_stalled_intent(self):
        """Test that stalled intent is properly marked."""
        out = StringIO()
        call_command('mark_stalled_checkout_intents', stdout=out)
        output = out.getvalue()

        self.assertIn('Successfully marked 1 CheckoutIntent', output)
        self.assertIn(str(self.stalled_intent.pk), output)

        # Verify state transition
        self.stalled_intent.refresh_from_db()
        self.assertEqual(
            self.stalled_intent.state,
            CheckoutIntentState.ERRORED_FULFILLMENT_STALLED
        )
        self.assertIsNotNone(self.stalled_intent.last_provisioning_error)
        self.assertIn('stalled', self.stalled_intent.last_provisioning_error.lower())

        # Verify other intents not affected
        self.recent_paid_intent.refresh_from_db()
        self.assertEqual(self.recent_paid_intent.state, CheckoutIntentState.PAID)

        self.fulfilled_intent.refresh_from_db()
        self.assertEqual(self.fulfilled_intent.state, CheckoutIntentState.FULFILLED)

        self.created_intent.refresh_from_db()
        self.assertEqual(self.created_intent.state, CheckoutIntentState.CREATED)

    def test_custom_threshold(self):
        """Test command with custom threshold."""
        # Mark the stalled intent from setUp as fulfilled so it doesn't interfere
        self.stalled_intent.state = CheckoutIntentState.FULFILLED
        self.stalled_intent.save()

        # Create intent that's 4 minutes old
        user = UserFactory()
        intent = CheckoutIntent.objects.create(
            user=user,
            state=CheckoutIntentState.PAID,
            enterprise_name="Test Enterprise",
            enterprise_slug="test-enterprise",
            quantity=10,
            expires_at=self.now + timedelta(hours=1),
            country='US',
        )
        CheckoutIntent.objects.filter(pk=intent.pk).update(
            modified=self.now - timedelta(seconds=240)
        )
        intent.refresh_from_db()

        # Should NOT be marked with 5-minute (300s) threshold
        out = StringIO()
        call_command('mark_stalled_checkout_intents', threshold_seconds=300, stdout=out)
        output = out.getvalue()
        self.assertIn('No stalled CheckoutIntent records found', output)

        intent.refresh_from_db()
        self.assertEqual(intent.state, CheckoutIntentState.PAID)

        # SHOULD be marked with 3-minute (180s) threshold
        out = StringIO()
        call_command('mark_stalled_checkout_intents', threshold_seconds=180, stdout=out)
        output = out.getvalue()
        self.assertIn('Successfully marked', output)

        intent.refresh_from_db()
        self.assertEqual(
            intent.state,
            CheckoutIntentState.ERRORED_FULFILLMENT_STALLED
        )

    def test_dry_run(self):
        """Test that dry-run doesn't actually update records."""
        out = StringIO()
        call_command('mark_stalled_checkout_intents', dry_run=True, stdout=out)
        output = out.getvalue()

        self.assertIn('[DRY RUN]', output)
        self.assertIn('Found 1 stalled CheckoutIntent', output)
        self.assertIn(str(self.stalled_intent.pk), output)
        self.assertIn('Stalled Enterprise', output)

        # Verify state NOT changed
        self.stalled_intent.refresh_from_db()
        self.assertEqual(self.stalled_intent.state, CheckoutIntentState.PAID)

    def test_ignores_non_paid_states(self):
        """Test that only PAID state intents are considered."""
        # Update all intents to non-paid states
        CheckoutIntent.objects.all().update(state=CheckoutIntentState.CREATED)

        out = StringIO()
        call_command('mark_stalled_checkout_intents', stdout=out)
        output = out.getvalue()

        self.assertIn('No stalled CheckoutIntent records found', output)

    def test_multiple_stalled_intents(self):
        """Test marking multiple stalled intents."""
        # Create 2 more stalled intents
        user_e = UserFactory()
        user_f = UserFactory()

        intent_2 = CheckoutIntent.objects.create(
            user=user_e,
            state=CheckoutIntentState.PAID,
            enterprise_name="Stalled 2",
            enterprise_slug="stalled-2",
            quantity=10,
            expires_at=self.now + timedelta(hours=1),
            country='FR',
        )
        CheckoutIntent.objects.filter(pk=intent_2.pk).update(
            modified=self.now - timedelta(seconds=400)
        )

        intent_3 = CheckoutIntent.objects.create(
            user=user_f,
            state=CheckoutIntentState.PAID,
            enterprise_name="Stalled 3",
            enterprise_slug="stalled-3",
            quantity=10,
            expires_at=self.now + timedelta(hours=1),
            country='IT',
        )
        CheckoutIntent.objects.filter(pk=intent_3.pk).update(
            modified=self.now - timedelta(seconds=500)
        )

        out = StringIO()
        call_command('mark_stalled_checkout_intents', stdout=out)
        output = out.getvalue()

        self.assertIn('Successfully marked 3 CheckoutIntent', output)

        # Verify all were updated
        for intent in [self.stalled_intent, intent_2, intent_3]:
            intent.refresh_from_db()
            self.assertEqual(
                intent.state,
                CheckoutIntentState.ERRORED_FULFILLMENT_STALLED
            )

    def test_handles_partial_failures(self):
        """Test that failure on one intent doesn't stop processing others."""
        # Create a second stalled intent
        user = UserFactory()
        intent_2 = CheckoutIntent.objects.create(
            user=user,
            state=CheckoutIntentState.PAID,
            enterprise_name="Stalled 2",
            enterprise_slug="stalled-2",
            quantity=10,
            expires_at=self.now + timedelta(hours=1),
            country='FR',
        )
        CheckoutIntent.objects.filter(pk=intent_2.pk).update(
            modified=self.now - timedelta(seconds=300)
        )

        # Mock mark_fulfillment_stalled to fail on first call, succeed on second
        with mock.patch.object(CheckoutIntent, 'mark_fulfillment_stalled') as mock_mark:
            mock_mark.side_effect = [Exception('Test error'), None]

            out = StringIO()
            call_command('mark_stalled_checkout_intents', stdout=out)
            output = out.getvalue()

            # Command should complete despite error
            self.assertIn('completed', output.lower())

    def test_error_message_content(self):
        """Test that error message contains useful information."""
        out = StringIO()
        call_command('mark_stalled_checkout_intents', stdout=out)

        self.stalled_intent.refresh_from_db()

        # Check error message contains key information
        error_msg = self.stalled_intent.last_provisioning_error
        self.assertIn('stalled', error_msg.lower())
        self.assertIn('threshold', error_msg.lower())
        self.assertIn('180', error_msg)  # Default threshold
        self.assertIn('seconds', error_msg.lower())

    def test_dry_run_shows_details(self):
        """Test that dry run shows detailed information about stalled intents."""
        out = StringIO()
        call_command('mark_stalled_checkout_intents', dry_run=True, stdout=out)
        output = out.getvalue()

        # Check that detailed information is shown
        self.assertIn(str(self.stalled_intent.pk), output)
        self.assertIn(self.stalled_intent.user.email, output)
        self.assertIn(self.stalled_intent.enterprise_name, output)
        self.assertIn('Time stalled:', output)
        self.assertIn('Last modified:', output)

    def test_command_with_zero_threshold(self):
        """Test command with zero threshold marks all paid intents."""
        out = StringIO()
        call_command('mark_stalled_checkout_intents', threshold_seconds=0, stdout=out)
        output = out.getvalue()

        # Both paid intents should be marked
        self.assertIn('Successfully marked 2 CheckoutIntent', output)

        self.stalled_intent.refresh_from_db()
        self.assertEqual(
            self.stalled_intent.state,
            CheckoutIntentState.ERRORED_FULFILLMENT_STALLED
        )

        self.recent_paid_intent.refresh_from_db()
        self.assertEqual(
            self.recent_paid_intent.state,
            CheckoutIntentState.ERRORED_FULFILLMENT_STALLED
        )
