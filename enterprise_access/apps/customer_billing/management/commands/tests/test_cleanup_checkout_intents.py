"""
Tests for the cleanup_checkout_intents management command.
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


class CleanupCheckoutIntentsCommandTests(TestCase):
    """Tests for the cleanup_checkout_intents management command."""

    def setUp(self):
        self.user = UserFactory()
        self.user_b = UserFactory()
        self.user_c = UserFactory()

        # Create an expired intent
        self.expired_intent = CheckoutIntent.objects.create(
            user=self.user,
            state=CheckoutIntentState.CREATED,
            enterprise_name="Expired Enterprise",
            enterprise_slug="expired-enterprise",
            quantity=10,
            expires_at=timezone.now() - timedelta(hours=1)
        )

        # Create a non-expired intent
        self.active_intent = CheckoutIntent.objects.create(
            user=self.user_b,
            state=CheckoutIntentState.CREATED,
            enterprise_name="Active Enterprise",
            enterprise_slug="active-enterprise",
            quantity=5,
            expires_at=timezone.now() + timedelta(hours=1)
        )

        # Create an intent that's already in a non-CREATED state
        self.paid_intent = CheckoutIntent.objects.create(
            user=self.user_c,
            state=CheckoutIntentState.PAID,
            enterprise_name="Paid Enterprise",
            enterprise_slug="paid-enterprise",
            quantity=15,
            expires_at=timezone.now() - timedelta(hours=1)  # Expired time but PAID state
        )

    def test_dry_run_mode(self):
        """Test that dry run mode shows expired intents without modifying them."""
        out = StringIO()
        call_command('cleanup_checkout_intents', dry_run=True, stdout=out)
        output = out.getvalue()

        # Check output contains the right message
        self.assertIn('Would update 1 expired', output)
        self.assertIn('expired-enterprise', output)

        # Verify no intents were actually updated
        self.expired_intent.refresh_from_db()
        self.assertEqual(self.expired_intent.state, CheckoutIntentState.CREATED)

    def test_normal_run(self):
        """Test that normal run updates expired intents."""
        out = StringIO()
        call_command('cleanup_checkout_intents', stdout=out)
        output = out.getvalue()

        # Check output contains success message
        self.assertIn('Successfully updated 1 expired', output)

        # Verify the expired intent was updated
        self.expired_intent.refresh_from_db()
        self.assertEqual(self.expired_intent.state, CheckoutIntentState.EXPIRED)

        # Verify other intents were not affected
        self.active_intent.refresh_from_db()
        self.assertEqual(self.active_intent.state, CheckoutIntentState.CREATED)

        self.paid_intent.refresh_from_db()
        self.assertEqual(self.paid_intent.state, CheckoutIntentState.PAID)

    def test_no_expired_intents(self):
        """Test handling when there are no expired intents."""
        # First update our expired intent so nothing is expired
        self.expired_intent.state = CheckoutIntentState.EXPIRED
        self.expired_intent.save()

        out = StringIO()
        call_command('cleanup_checkout_intents', stdout=out)
        output = out.getvalue()

        self.assertIn('No expired checkout intents found', output)
