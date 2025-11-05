"""
Tests for the mark_stalled_checkout_intents management command.
"""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.constants import CheckoutIntentState
from enterprise_access.apps.customer_billing.models import CheckoutIntent


class MarkStalledCheckoutIntentsCommandTests(TestCase):
    """Integration tests for the mark_stalled_checkout_intents management command."""

    def _create_intent(self, state, modified_minutes_ago=0):
        """Helper to create a CheckoutIntent with specific modified time."""
        user = UserFactory()
        intent = CheckoutIntent.objects.create(
            user=user,
            state=state,
            enterprise_name='Test Enterprise',
            enterprise_slug=f'test-enterprise-{user.id}',
            quantity=10,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        if modified_minutes_ago:
            CheckoutIntent.objects.filter(pk=intent.pk).update(
                modified=timezone.now() - timedelta(minutes=modified_minutes_ago)
            )
            intent.refresh_from_db()

        return intent

    def test_command_executes_successfully(self):
        """Test that command executes and marks stalled intents."""
        stalled = self._create_intent(CheckoutIntentState.PAID, modified_minutes_ago=5)

        out = StringIO()
        call_command('mark_stalled_checkout_intents', stdout=out)

        output = out.getvalue()
        self.assertIn('Command completed successfully', output)

        stalled.refresh_from_db()
        self.assertEqual(stalled.state, CheckoutIntentState.ERRORED_FULFILLMENT_STALLED)

    def test_command_dry_run_flag(self):
        """Test that --dry-run flag prevents database changes."""
        stalled = self._create_intent(CheckoutIntentState.PAID, modified_minutes_ago=5)

        out = StringIO()
        call_command('mark_stalled_checkout_intents', dry_run=True, stdout=out)

        output = out.getvalue()
        self.assertIn('[DRY RUN]', output)
        self.assertIn('Command completed successfully', output)

        stalled.refresh_from_db()
        self.assertEqual(stalled.state, CheckoutIntentState.PAID)
