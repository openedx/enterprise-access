"""
Unit tests for Stripe event handlers.
"""
from contextlib import nullcontext
from datetime import timedelta
from random import randint
from typing import Type, cast
from unittest import mock

import ddt
import stripe
from django.contrib.auth.models import AbstractUser
from django.test import TestCase
from django.utils import timezone

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.constants import CheckoutIntentState
from enterprise_access.apps.customer_billing.models import CheckoutIntent, StripeEventData
from enterprise_access.apps.customer_billing.stripe_event_handlers import (
    StripeEventHandler,
    future_plans_of_current,
    cancel_all_future_plans,
)


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
        StripeEventData.objects.all().delete()

    def _create_mock_stripe_event(self, event_type, event_data):
        """Helper to create a mock Stripe event."""
        mock_event = mock.MagicMock(spec=stripe.Event)
        created_at = timezone.now() - timedelta(seconds=randint(1, 30))
        mock_event.created = int(created_at.timestamp())
        mock_event.type = event_type
        numeric_id = str(randint(1, 100000)).zfill(6)
        mock_event.id = f'evt_test_{event_type.replace(".", "_")}_{numeric_id}'
        mock_event.data = mock.Mock()
        mock_event.data.object = AttrDict.wrap(event_data)
        return mock_event

    def _create_mock_stripe_subscription(self, checkout_intent_id):
        """Helper to create a mock Stripe subscription."""
        return {
            'id': randint(1, 100000),
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
            'expected_final_state': CheckoutIntentState.CREATED,  # Unchanged.
            'expect_matching_intent': False,
        },
        # Sad Test case: invalid checkout_intent_id format
        {
            'intent_id_override': 'not_an_integer',
            'expected_exception': ValueError,
            'expect_matching_intent': False,
            'expected_final_state': CheckoutIntentState.CREATED,  # Unchanged.
        },
    )
    @ddt.unpack
    def test_invoice_paid_handler(
        self,
        checkout_intent_state=CheckoutIntentState.CREATED,
        intent_id_override=None,
        expected_exception=None,
        expect_matching_intent=True,
        expected_final_state=CheckoutIntentState.PAID,
    ):
        """Test various scenarios for the invoice.paid event handler."""
        if checkout_intent_state == CheckoutIntentState.PAID:
            self.checkout_intent.mark_as_paid(
                stripe_session_id=self.stripe_checkout_session_id,
                stripe_customer_id='cus_test_789',
            )

        subscription_id = 'sub_test_123456'
        mock_subscription = self._create_mock_stripe_subscription(intent_id_override or self.checkout_intent.id)
        invoice_data = {
            'id': 'in_test_123456',
            'customer': 'cus_test_789',
            'parent': {
                'subscription_details': {
                    'metadata': mock_subscription,
                    'subscription': subscription_id,
                },
            },
        }

        mock_event = self._create_mock_stripe_event('invoice.paid', invoice_data)

        with self.assertRaises(expected_exception) if expected_exception else nullcontext():
            StripeEventHandler.dispatch(mock_event)

        # Verify the final state
        self.checkout_intent.refresh_from_db()
        self.assertEqual(self.checkout_intent.state, expected_final_state)

        if expect_matching_intent:
            event_data = StripeEventData.objects.get(event_id=mock_event.id)
            self.assertEqual(event_data.checkout_intent, self.checkout_intent)

    def test_invoice_paid_handler_sets_stripe_customer_id(self):
        """Test that invoice.paid handler correctly sets stripe_customer_id on CheckoutIntent."""
        subscription_id = 'sub_test_customer_id_123'
        stripe_customer_id = 'cus_test_customer_456'
        mock_subscription = self._create_mock_stripe_subscription(self.checkout_intent.id)

        invoice_data = {
            'id': 'in_test_customer_123',
            'customer': stripe_customer_id,
            'parent': {
                'subscription_details': {
                    'metadata': mock_subscription,
                    'subscription': subscription_id,
                }
            },
        }

        mock_event = self._create_mock_stripe_event('invoice.paid', invoice_data)

        # Verify initial state
        self.assertEqual(self.checkout_intent.state, CheckoutIntentState.CREATED)
        self.assertIsNone(self.checkout_intent.stripe_customer_id)

        # Handle the event
        StripeEventHandler.dispatch(mock_event)

        # Verify the checkout intent was updated correctly
        self.checkout_intent.refresh_from_db()
        self.assertEqual(self.checkout_intent.state, CheckoutIntentState.PAID)
        self.assertEqual(self.checkout_intent.stripe_customer_id, stripe_customer_id)
        event_data = StripeEventData.objects.get(event_id=mock_event.id)
        self.assertEqual(event_data.checkout_intent, self.checkout_intent)

    def test_invoice_paid_handler_idempotent_with_same_customer_id(self):
        """Test that invoice.paid handler is idempotent when called with same stripe_customer_id."""
        subscription_id = 'sub_test_idempotent_123'
        stripe_customer_id = 'cus_test_idempotent_456'
        mock_subscription = self._create_mock_stripe_subscription(self.checkout_intent.id)

        # First mark the intent as paid with the customer_id
        self.checkout_intent.mark_as_paid(stripe_customer_id=stripe_customer_id)
        self.assertEqual(self.checkout_intent.state, CheckoutIntentState.PAID)
        self.assertEqual(self.checkout_intent.stripe_customer_id, stripe_customer_id)

        invoice_data = {
            'id': 'in_test_idempotent_123',
            'customer': stripe_customer_id,
            'parent': {
                'subscription_details': {
                    'metadata': mock_subscription,
                    'subscription': subscription_id
                }
            },
        }

        mock_event = self._create_mock_stripe_event('invoice.paid', invoice_data)

        # Handle the event - should be idempotent
        StripeEventHandler.dispatch(mock_event)

        # Verify the checkout intent state remains unchanged
        self.checkout_intent.refresh_from_db()
        self.assertEqual(self.checkout_intent.state, CheckoutIntentState.PAID)
        self.assertEqual(self.checkout_intent.stripe_customer_id, stripe_customer_id)
        event_data = StripeEventData.objects.get(event_id=mock_event.id)
        self.assertEqual(event_data.checkout_intent, self.checkout_intent)

    @mock.patch(
        "enterprise_access.apps.customer_billing.stripe_event_handlers.send_trial_cancellation_email_task"
    )
    def test_subscription_updated_sends_cancellation_email_for_canceled_trial(
        self, mock_email_task
    ):
        """Test that subscription_updated sends email when trial is canceled."""
        trial_end_timestamp = 1234567890
        subscription_data = {
            "id": "sub_test_canceled_123",
            "status": "canceled",
            "trial_end": trial_end_timestamp,
            "metadata": self._create_mock_stripe_subscription(
                self.checkout_intent.id
            ),
        }

        mock_event = self._create_mock_stripe_event(
            "customer.subscription.updated", subscription_data
        )

        StripeEventHandler.dispatch(mock_event)

        # Verify the email task was queued
        mock_email_task.delay.assert_called_once_with(
            checkout_intent_id=self.checkout_intent.id,
            trial_end_timestamp=trial_end_timestamp,
        )

    def test_subscription_updated_skips_email_when_no_trial_end(self):
        """Test that subscription_updated skips email when trial_end is missing."""
        subscription_data = {
            "id": "sub_test_no_trial_123",
            "status": "canceled",
            "trial_end": None,  # No trial
            "metadata": self._create_mock_stripe_subscription(
                self.checkout_intent.id
            ),
        }

        mock_event = self._create_mock_stripe_event(
            "customer.subscription.updated", subscription_data
        )

        with mock.patch(
            "enterprise_access.apps.customer_billing.stripe_event_handlers.send_trial_cancellation_email_task"
        ) as mock_task:
            StripeEventHandler.dispatch(mock_event)
            mock_task.delay.assert_not_called()

    @mock.patch(
        "enterprise_access.apps.customer_billing.stripe_event_handlers.cancel_all_future_plans"
    )
    @mock.patch(
        "enterprise_access.apps.customer_billing.stripe_event_handlers.send_billing_error_email_task"
    )
    @mock.patch.object(CheckoutIntent, "previous_summary")
    def test_subscription_updated_past_due_cancels_future_plans(
        self, mock_prev_summary, mock_send_billing_error, mock_cancel
    ):
        """Past-due transition triggers cancel_all_future_plans with expected args."""
        subscription_id = "sub_test_past_due_123"
        subscription_data = {
            "id": subscription_id,
            "status": "past_due",
            "metadata": self._create_mock_stripe_subscription(self.checkout_intent.id),
        }

        # Simulate prior status was not past_due
        mock_prev_summary.return_value = AttrDict({"subscription_status": "active"})

        mock_event = self._create_mock_stripe_event(
            "customer.subscription.updated", subscription_data
        )

        StripeEventHandler.dispatch(mock_event)

        mock_cancel.assert_called_once()
        mock_send_billing_error.delay.assert_called_once_with(checkout_intent_id=self.checkout_intent.id)
        _, kwargs = mock_cancel.call_args
        self.assertEqual(
            kwargs["enterprise_uuid"], self.checkout_intent.enterprise_uuid
        )
        self.assertEqual(kwargs["reason"], "delayed_payment")
        self.assertEqual(
            kwargs["subscription_id_for_logs"], subscription_id
        )

    def test_future_plans_of_current_selects_children(self):
        """future_plans_of_current returns plans whose prior_renewals link to current plan."""
        current_uuid = "1111-aaaa"
        plans = [
            {"uuid": current_uuid, "prior_renewals": []},
            {"uuid": "2222-bbbb", "prior_renewals": [{"prior_subscription_plan_id": current_uuid}]},
            {"uuid": "3333-cccc", "prior_renewals": [{"prior_subscription_plan_id": current_uuid}]},
            {"uuid": "4444-dddd", "prior_renewals": []},
        ]

        result = future_plans_of_current(current_uuid, plans)
        self.assertEqual({p["uuid"] for p in result}, {"2222-bbbb", "3333-cccc"})

    @mock.patch("enterprise_access.apps.customer_billing.stripe_event_handlers.LicenseManagerApiClient")
    def test_cancel_all_future_plans_deactivates_all(self, MockClient):
        """cancel_all_future_plans patches all future plans and returns their uuids."""
        enterprise_uuid = "ent-123"
        current_uuid = "1111-aaaa"
        future1 = {"uuid": "2222-bbbb", "prior_renewals": [{"prior_subscription_plan_id": current_uuid}]}
        future2 = {"uuid": "3333-cccc", "prior_renewals": [{"prior_subscription_plan_id": current_uuid}]}

        mock_client = MockClient.return_value
        # list_subscriptions(current=True)
        mock_client.list_subscriptions.side_effect = [
            {"results": [{"uuid": current_uuid}]},  # current=True response
            {"results": [
                {"uuid": current_uuid, "prior_renewals": []},
                future1,
                future2,
            ]},  # all plans response
        ]

        deactivated = cancel_all_future_plans(enterprise_uuid, reason="delayed_payment", subscription_id_for_logs="sub-1")

        # Should have patched both future plans
        self.assertEqual(set(deactivated), {"2222-bbbb", "3333-cccc"})
        self.assertEqual(mock_client.update_subscription_plan.call_count, 2)
        mock_client.update_subscription_plan.assert_any_call("2222-bbbb", is_active=False, change_reason="delayed_payment")
        mock_client.update_subscription_plan.assert_any_call("3333-cccc", is_active=False, change_reason="delayed_payment")
