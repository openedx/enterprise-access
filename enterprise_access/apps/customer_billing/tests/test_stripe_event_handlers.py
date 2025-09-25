"""
Unit tests for Stripe event handlers.
"""
from contextlib import nullcontext
from typing import Type, cast
from unittest import mock

import ddt
import stripe
from django.contrib.auth.models import AbstractUser
from django.test import TestCase

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.constants import CheckoutIntentState
from enterprise_access.apps.customer_billing.models import CheckoutIntent
from enterprise_access.apps.customer_billing.stripe_event_handlers import StripeEventHandler


class AttrDict(dict):
    """
    Minimal helper that allows both attribute (obj.foo) and item (obj['foo']) access.
    Recursively converts nested dicts to AttrDicts, but leaves non-dict values as-is.
    """
    def __getattr__(self, name):
        try:
            value = self[name]
        except KeyError as e:
            raise AttributeError(name) from e
        return value

    def __setattr__(self, name, value):
        self[name] = value

    @staticmethod
    def wrap(value):
        if isinstance(value, dict) and not isinstance(value, AttrDict):
            return AttrDict({k: AttrDict.wrap(v) for k, v in value.items()})
        return value


@ddt.ddt
class TestStripeEventHandler(TestCase):
    """
    Tests for the StripeEventHandler class and its event handling framework.
    """

    def setUp(self):
        """Set up test data."""
        self.user = UserFactory()
        self.checkout_intent = CheckoutIntent.create_intent(
            user=cast(Type[AbstractUser], self.user),
            slug='test-enterprise',
            name='Test Enterprise',
            quantity=10,
            country='US',
            terms_metadata={'version': '1.0', 'test_mode': True}
        )
        self.stripe_checkout_session_id = 'cs_test_1234'

    def tearDown(self):
        """Clean up after tests."""
        CheckoutIntent.objects.all().delete()

    def _create_mock_stripe_event(self, event_type, event_data):
        """Helper to create a mock Stripe event."""
        mock_event = mock.MagicMock(spec=stripe.Event)
        mock_event.type = event_type
        mock_event.id = f'evt_test_{event_type.replace(".", "_")}_123456'
        mock_event.data = mock.Mock()
        mock_event.data.object = AttrDict.wrap(event_data)
        return mock_event

    def _create_mock_stripe_subscription(self, checkout_intent_id):
        """Helper to create a mock Stripe subscription."""
        return {
            'checkout_intent_id': str(checkout_intent_id),
            'enterprise_customer_name': 'Test Enterprise',
            'enterprise_customer_slug': 'test-enterprise',
            'lms_user_id': str(self.user.lms_user_id),
        }

    def test_dispatch_unknown_event_type(self):
        """Test that dispatching an unknown event type raises KeyError."""
        mock_event = self._create_mock_stripe_event('unknown.event.type', {})

        with self.assertRaises(KeyError):
            StripeEventHandler.dispatch(mock_event)

    @ddt.data(
        # Happy Test case: successful invoice.paid handling
        {
            'checkout_intent_state': CheckoutIntentState.CREATED,  # Simulate a typical scenario.
            'expected_final_state': CheckoutIntentState.PAID,  # Changed!
        },
        # Happy Test case: CheckoutIntent already paid - result should be idempotent w/ no errors.
        {
            'checkout_intent_state': CheckoutIntentState.PAID,  # Network outage led to redundant webhook retries.
            'expected_final_state': CheckoutIntentState.PAID,  # Unchanged.
        },
        # Sad Test case: CheckoutIntent not found
        {
            'intent_id_override': '99999',  # certainly does not exist.
            'expected_exception': CheckoutIntent.DoesNotExist,
            'expected_final_state': CheckoutIntentState.CREATED,  # Unchanged.
        },
        # Sad Test case: invalid checkout_intent_id format
        {
            'intent_id_override': 'not_an_integer',
            'expected_exception': ValueError,
            'expected_final_state': CheckoutIntentState.CREATED,  # Unchanged.
        },
    )
    @ddt.unpack
    @mock.patch('enterprise_access.apps.customer_billing.stripe_event_handlers.logger')
    def test_invoice_paid_handler(
        self,
        mock_logger,
        checkout_intent_state=CheckoutIntentState.CREATED,
        intent_id_override=None,
        expected_exception=None,
        expected_final_state=CheckoutIntentState.PAID,
    ):
        """Test various scenarios for the invoice.paid event handler."""
        if checkout_intent_state == CheckoutIntentState.PAID:
            self.checkout_intent.update_stripe_session_id(self.stripe_checkout_session_id)
            self.checkout_intent.mark_as_paid()

        subscription_id = 'sub_test_123456'
        mock_subscription = self._create_mock_stripe_subscription(intent_id_override or self.checkout_intent.id)
        invoice_data = {
            'id': 'in_test_123456',
            'parent': {
                'subscription_details': {
                    'metadata': mock_subscription,
                    'subscription': subscription_id
                }
            },
        }

        mock_event = self._create_mock_stripe_event('invoice.paid', invoice_data)

        with self.assertRaises(expected_exception) if expected_exception else nullcontext():
            StripeEventHandler.dispatch(mock_event)

        # Verify the final state
        self.checkout_intent.refresh_from_db()
        self.assertEqual(self.checkout_intent.state, expected_final_state)

        # Verify logging for successful cases
        if not expected_exception:
            mock_logger.info.assert_any_call(
                f'[StripeEventHandler] handling <stripe.Event id={mock_event.id} type=invoice.paid>.'
            )
            mock_logger.info.assert_any_call(
                f'Found checkout_intent_id="{self.checkout_intent.id}" '
                f'stored on the Subscription <subscription_id="{subscription_id}"> '
                f'related to Invoice <invoice_id="{invoice_data["id"]}">.'
            )
            mock_logger.info.assert_any_call(
                'Found existing CheckoutIntent record with '
                f'id={self.checkout_intent.id}, '
                f'stripe_checkout_session_id={self.checkout_intent.stripe_checkout_session_id}, '
                f'state={checkout_intent_state}.  '
                'Marking intent as paid...'
            )
            mock_logger.info.assert_any_call(
                f'[StripeEventHandler] handler for <stripe.Event id={mock_event.id} type=invoice.paid> complete.'
            )
