"""
Unit tests for Stripe event handlers.
"""
import uuid
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
from enterprise_access.apps.customer_billing.models import (
    CheckoutIntent,
    SelfServiceSubscriptionRenewal,
    StripeEventData,
    StripeEventSummary
)
from enterprise_access.apps.customer_billing.stripe_event_handlers import StripeEventHandler, cancel_all_future_plans
from enterprise_access.apps.customer_billing.tests.factories import (
    SelfServiceSubscriptionRenewalFactory,
    StripeEventDataFactory,
    StripeEventSummaryFactory,
    get_stripe_object_for_event_type
)
from enterprise_access.apps.provisioning.tests.factories import ProvisionNewCustomerWorkflowFactory


def _rand_numeric_string():
    return str(randint(1, 100000)).zfill(6)


def _rand_created_at():
    return timezone.now() - timedelta(seconds=randint(1, 30))


def _rand_numeric_string():
    return str(randint(1, 100000)).zfill(6)


def _rand_created_at():
    return timezone.now() - timedelta(seconds=randint(1, 30))


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
        StripeEventSummary.objects.all().delete()

    def _create_mock_stripe_event(self, event_type, event_data, **event_attrs):
        """
        Creates an honest-to-goodness ``stripe.Event`` object with the given
        type and data.
        """
        event = stripe.Event()
        event.id = f'evt_test_{event_type.replace(".", "_")}_{_rand_numeric_string()}'
        event.created = int(_rand_created_at().timestamp())
        event.type = event_type
        event.data = stripe.StripeObject()
        event.data.object = AttrDict.wrap(event_data)

        for k, v in event_attrs.items():
            setattr(event, k, v)

        return event

    def _create_mock_stripe_subscription(self, checkout_intent_id):
        """Helper to create a mock Stripe subscription."""
        return {
            'id': randint(1, 100000),
            'checkout_intent_id': str(checkout_intent_id),
            'enterprise_customer_name': 'Test Enterprise',
            'enterprise_customer_slug': 'test-enterprise',
            'lms_user_id': str(self.user.lms_user_id),
        }

    def _create_existing_event_data_records(
        self,
        stripe_subscription_id,
        event_type='customer.subscription.created',
        subscription_status='trialing',
    ):
        """
        Helper to create a test StripeEventData/Summary corresponding to a past
        event of a given type
        """
        earlier_time = timezone.now() - timedelta(hours=1)
        event_data = StripeEventDataFactory(
            checkout_intent=self.checkout_intent,
            event_type=event_type,
        )
        event_data.data['data']['object']['status'] = subscription_status
        event_data.data['data']['object']['id'] = stripe_subscription_id
        event_data.save()

        # The summary record should already exist by virtue of the signal handler
        summary_record = event_data.summary
        summary_record.subscription_status = subscription_status
        summary_record.stripe_event_created_at = earlier_time
        summary_record.stripe_subscription_id = stripe_subscription_id
        summary_record.save()
        return event_data, summary_record

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
    @mock.patch(
        "enterprise_access.apps.customer_billing.stripe_event_handlers.send_payment_receipt_email"
    )
    def test_invoice_paid_handler(
        self,
        mock_send_payment_receipt_email,  # pylint: disable=unused-argument
        checkout_intent_state=CheckoutIntentState.CREATED,
        intent_id_override=None,
        expected_exception=None,
        expect_matching_intent=True,
        expected_final_state=CheckoutIntentState.PAID,
    ):
        """Test various scenarios for the invoice.paid event handler."""
        stripe_customer_id = 'cus_test_customer_456'

        if checkout_intent_state == CheckoutIntentState.PAID:
            self.checkout_intent.mark_as_paid(
                stripe_session_id=self.stripe_checkout_session_id,
                stripe_customer_id=stripe_customer_id,
            )

        subscription_id = 'sub_test_123456'
        mock_subscription = self._create_mock_stripe_subscription(intent_id_override or self.checkout_intent.id)
        invoice_data = {
            'id': 'in_test_123456',
            'customer': stripe_customer_id,
            'object': 'invoice',
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
            self.assertEqual(event_data.summary.checkout_intent, self.checkout_intent)
            self.assertIsNotNone(event_data.handled_at)
            self.assertEqual(self.checkout_intent.stripe_customer_id, stripe_customer_id)

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
            'object': 'invoice',
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

        mock_email_task.delay.assert_called_once_with(
            checkout_intent_id=self.checkout_intent.id,
            trial_end_timestamp=trial_end_timestamp,
        )

    def test_subscription_updated_skips_email_when_no_trial_end(self):
        """Test that subscription_updated skips email when trial_end is missing."""
        subscription_data = {
            "id": "sub_test_no_trial_123",
            "status": "canceled",
            "trial_end": None,
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

        # Ensure enterprise_uuid is present so handler proceeds with cancellation
        self.checkout_intent.enterprise_uuid = uuid.uuid4()
        self.checkout_intent.save(update_fields=["enterprise_uuid"])

        mock_event = self._create_mock_stripe_event(
            "customer.subscription.updated", subscription_data
        )

        StripeEventHandler.dispatch(mock_event)

        mock_cancel.assert_called_once_with(self.checkout_intent)
        mock_send_billing_error.delay.assert_called_once_with(
            checkout_intent_id=self.checkout_intent.id,
        )

    @mock.patch(
        "enterprise_access.apps.customer_billing.stripe_event_handlers.LicenseManagerApiClient",
        autospec=True,
    )
    def test_cancel_all_future_plans_deactivates_all(self, mock_lms_client):
        """cancel_all_future_plans patches all future plans and returns their uuids."""
        mock_client = mock_lms_client.return_value
        trial_plan_uuid = uuid.uuid4()
        paid_plan_a_uuid = uuid.uuid4()
        paid_plan_b_uuid = uuid.uuid4()
        SelfServiceSubscriptionRenewalFactory.create(
            checkout_intent=self.checkout_intent,
            prior_subscription_plan_uuid=trial_plan_uuid,
            renewed_subscription_plan_uuid=paid_plan_a_uuid,
            processed_at=None,
        )
        SelfServiceSubscriptionRenewalFactory.create(
            checkout_intent=self.checkout_intent,
            prior_subscription_plan_uuid=paid_plan_a_uuid,
            renewed_subscription_plan_uuid=paid_plan_b_uuid,
            processed_at=None,
        )

        deactivated = cancel_all_future_plans(self.checkout_intent)

        self.assertEqual(set(deactivated), {paid_plan_a_uuid, paid_plan_b_uuid})
        self.assertEqual(2, mock_client.update_subscription_plan.call_count)
        mock_client.update_subscription_plan.assert_any_call(
            str(paid_plan_a_uuid),
            is_active=False,
        )
        mock_client.update_subscription_plan.assert_any_call(
            str(paid_plan_b_uuid),
            is_active=False,
        )

    @mock.patch(
        "enterprise_access.apps.customer_billing.stripe_event_handlers.send_trial_ending_reminder_email_task"
    )
    def test_trial_will_end_handler_success(self, mock_email_task):
        """Test successful trial_will_end event handling."""
        trial_end_timestamp = 1640995200
        subscription_data = {
            "id": "sub_test_trial_will_end_123",
            "trial_end": trial_end_timestamp,
            "object": "subscription",
            "metadata": self._create_mock_stripe_subscription(
                self.checkout_intent.id
            ),
        }

        mock_event = self._create_mock_stripe_event(
            "customer.subscription.trial_will_end", subscription_data
        )

        StripeEventHandler.dispatch(mock_event)

        mock_email_task.delay.assert_called_once_with(
            checkout_intent_id=self.checkout_intent.id,
        )

        event_data = StripeEventData.objects.get(event_id=mock_event.id)
        self.assertEqual(event_data.checkout_intent, self.checkout_intent)

    @mock.patch(
        "enterprise_access.apps.customer_billing.stripe_event_handlers.send_trial_ending_reminder_email_task"
    )
    def test_trial_will_end_handler_checkout_intent_not_found(
        self, mock_email_task
    ):
        """Test trial_will_end when CheckoutIntent is not found."""
        trial_end_timestamp = 1640995200
        subscription_data = {
            "id": "sub_test_not_found_123",
            "trial_end": trial_end_timestamp,
            "metadata": self._create_mock_stripe_subscription(99999),
        }

        mock_event = self._create_mock_stripe_event(
            "customer.subscription.trial_will_end", subscription_data
        )

        StripeEventHandler.dispatch(mock_event)

        mock_email_task.delay.assert_not_called()

    @mock.patch(
        "enterprise_access.apps.customer_billing.stripe_event_handlers.send_trial_ending_reminder_email_task"
    )
    def test_trial_will_end_handler_no_checkout_intent_metadata(
        self, mock_email_task
    ):
        """Test trial_will_end when subscription has no checkout_intent_id in metadata."""
        subscription_data = {
            "id": "sub_test_no_metadata_123",
            "metadata": {},
        }

        mock_event = self._create_mock_stripe_event(
            "customer.subscription.trial_will_end", subscription_data
        )

        StripeEventHandler.dispatch(mock_event)

        mock_email_task.delay.assert_not_called()

    @mock.patch('enterprise_access.apps.customer_billing.stripe_event_handlers.LicenseManagerApiClient')
    def test_subscription_updated_trial_to_active_no_renewal_record(self, mock_license_manager_client):
        """Test trial -> active transition gracefully handles missing renewal record."""
        # Create previous summary with trial status but NO renewal record
        StripeEventSummaryFactory(
            checkout_intent=self.checkout_intent,
            subscription_status='trialing',
            stripe_subscription_id='sub_test_456'
        )

        subscription_data = {
            "id": "sub_test_456",
            "status": "active",
            "metadata": self._create_mock_stripe_subscription(self.checkout_intent.id),
        }

        mock_event = self._create_mock_stripe_event(
            "customer.subscription.updated", subscription_data
        )

        # This should not raise an exception - should be gracefully handled
        StripeEventHandler.dispatch(mock_event)

        # Verify license manager client was NOT called since no renewal record exists
        mock_license_manager_client.assert_not_called()

    @mock.patch('enterprise_access.apps.customer_billing.stripe_event_handlers.LicenseManagerApiClient')
    def test_subscription_updated_trial_to_active_already_processed(self, mock_license_manager_client):
        """Test that already processed renewals are not processed again."""
        # Create provisioning workflow (simulates renewal record creation during provisioning)
        workflow = ProvisionNewCustomerWorkflowFactory()
        self.checkout_intent.workflow = workflow
        self.checkout_intent.save()

        stripe_subscription_id = 'sub_test_222'
        trial_event_data, _ = self._create_existing_event_data_records(stripe_subscription_id)

        expected_renewal_id = 999
        renewal_record = SelfServiceSubscriptionRenewal.objects.create(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id,
            stripe_subscription_id='',
            stripe_event_data=trial_event_data,
            processed_at=timezone.now(),
        )

        # Simulate the trial -> active transition event
        subscription_data = get_stripe_object_for_event_type(
            'customer.subscription.updated',
            id=stripe_subscription_id,
            status="active",
            metadata=self._create_mock_stripe_subscription(self.checkout_intent.id),
        )

        mock_event = self._create_mock_stripe_event(
            "customer.subscription.updated",
            subscription_data,
        )

        # Ensure the mock event has a timestamp after the trial summary
        mock_event.created = int((timezone.now() + timedelta(hours=2)).timestamp())

        # Dispatch the event
        StripeEventHandler.dispatch(mock_event)

        # Verify license manager client was NOT called since renewal already processed
        mock_license_manager_client.assert_not_called()

        # Verify renewal record remains processed
        renewal_record.refresh_from_db()
        self.assertIsNotNone(renewal_record.processed_at)

    @mock.patch('enterprise_access.apps.customer_billing.stripe_event_handlers.LicenseManagerApiClient')
    def test_subscription_updated_license_manager_api_error(self, mock_license_manager_client):
        """Test error handling when license manager API fails during renewal processing."""
        # Create provisioning workflow (simulates renewal record creation during provisioning)
        workflow = ProvisionNewCustomerWorkflowFactory()
        self.checkout_intent.workflow = workflow
        self.checkout_intent.save()

        stripe_subscription_id = 'sub_test_789'
        trial_event_data, _ = self._create_existing_event_data_records(stripe_subscription_id)

        expected_renewal_id = 999
        renewal_record = SelfServiceSubscriptionRenewal.objects.create(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id,
            stripe_subscription_id='',
            stripe_event_data=trial_event_data
        )

        # Simulate the trial -> active transition event
        subscription_data = get_stripe_object_for_event_type(
            'customer.subscription.updated',
            id=stripe_subscription_id,
            status="active",
            metadata=self._create_mock_stripe_subscription(self.checkout_intent.id),
        )

        mock_event = self._create_mock_stripe_event(
            "customer.subscription.updated",
            subscription_data,
        )

        # Ensure the mock event has a timestamp after the trial summary
        mock_event.created = int((timezone.now() + timedelta(hours=2)).timestamp())

        # Mock license manager API failure
        mock_client_instance = mock_license_manager_client.return_value
        mock_client_instance.process_subscription_plan_renewal.side_effect = Exception("API Error")

        # Dispatch should raise the exception since _process_trial_to_paid_renewal re-raises
        with self.assertRaises(Exception) as context:
            StripeEventHandler.dispatch(mock_event)

        self.assertIn("API Error", str(context.exception))

        # Verify license manager was called but failed
        mock_client_instance.process_subscription_plan_renewal.assert_called_once_with(expected_renewal_id)

        # Verify renewal record was NOT marked as processed due to error
        renewal_record.refresh_from_db()
        self.assertIsNone(renewal_record.processed_at)

    @mock.patch('enterprise_access.apps.customer_billing.stripe_event_handlers.LicenseManagerApiClient')
    def test_full_subscription_renewal_flow(self, mock_license_manager_client):
        """Test the complete subscription renewal flow from provisioning to processing."""
        # Create provisioning workflow (simulates renewal record creation during provisioning)
        workflow = ProvisionNewCustomerWorkflowFactory()
        self.checkout_intent.workflow = workflow
        self.checkout_intent.save()

        stripe_subscription_id = 'sub_test_789'

        # Create existing StripeEventData and summary in a "trialing" state,
        # so that we can create a renewal record as it would exist after initial
        # provisioning workflow execution.
        trial_event_data, _ = self._create_existing_event_data_records(stripe_subscription_id)

        expected_renewal_id = 555
        renewal_record = SelfServiceSubscriptionRenewal.objects.create(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id,
            stripe_subscription_id=stripe_subscription_id,
            stripe_event_data=trial_event_data,
        )

        # Simulate the trial -> active transition event
        subscription_data = get_stripe_object_for_event_type(
            'customer.subscription.updated',
            id=stripe_subscription_id,
            status="active",
            metadata=self._create_mock_stripe_subscription(self.checkout_intent.id),
        )

        mock_event = self._create_mock_stripe_event(
            "customer.subscription.updated",
            subscription_data,
        )

        # Ensure the mock event has a timestamp after the trial summary
        mock_event.created = int((timezone.now() + timedelta(hours=2)).timestamp())

        # Mock the license manager client response
        mock_client_instance = mock_license_manager_client.return_value
        mock_client_instance.process_subscription_plan_renewal.return_value = {
            'id': 555, 'status': 'processed', 'processed_at': '2024-01-15T10:30:00Z'
        }

        # Step 4: Process the trial -> active event
        StripeEventHandler.dispatch(mock_event)

        # Step 5: Verify the complete flow worked end-to-end

        # Verify license manager was called to process the renewal
        mock_license_manager_client.assert_called_once()
        mock_client_instance.process_subscription_plan_renewal.assert_called_once_with(expected_renewal_id)

        # Verify renewal record was processed
        renewal_record.refresh_from_db()
        self.assertIsNotNone(renewal_record.processed_at)
        self.assertEqual(renewal_record.stripe_subscription_id, stripe_subscription_id)

        # Verify event was linked to renewal record
        event_data = StripeEventData.objects.get(event_id=mock_event.id)
        self.assertEqual(renewal_record.stripe_event_data, event_data)
        self.assertIsNotNone(event_data.handled_at)

        # Verify StripeEventSummary was created for the new event
        new_summary = event_data.summary
        self.assertEqual(new_summary.subscription_status, 'active')
        self.assertEqual(new_summary.stripe_subscription_id, stripe_subscription_id)
        self.assertEqual(new_summary.checkout_intent, self.checkout_intent)

        # Verify all data relationships are intact (including new_summary)
        self.assertEqual(renewal_record.checkout_intent, self.checkout_intent)
        self.assertEqual(event_data.checkout_intent, self.checkout_intent)
        self.assertEqual(new_summary.checkout_intent, self.checkout_intent)

    @mock.patch('stripe.Subscription.modify')
    def test_subscription_created_handler_success(self, mock_stripe_modify):
        """Test successful customer.subscription.created event handling."""
        subscription_id = 'sub_test_created_123'
        subscription_data = {
            'id': subscription_id,
            'status': 'trialing',
            'object': 'subscription',
            'metadata': self._create_mock_stripe_subscription(self.checkout_intent.id),
        }

        mock_event = self._create_mock_stripe_event(
            'customer.subscription.created', subscription_data
        )

        StripeEventHandler.dispatch(mock_event)

        # Verify stripe.Subscription.modify was called to enable pending updates
        mock_stripe_modify.assert_called_once_with(
            subscription_id,
            payment_behavior='pending_if_incomplete',
        )

        # Verify event data was created and linked to checkout intent
        event_data = StripeEventData.objects.get(event_id=mock_event.id)
        self.assertEqual(event_data.checkout_intent, self.checkout_intent)
        self.assertEqual(event_data.event_type, 'customer.subscription.created')
        self.assertIsNotNone(event_data.handled_at)

        # Verify summary was created and updated
        summary = event_data.summary
        self.assertEqual(summary.checkout_intent, self.checkout_intent)
        self.assertEqual(summary.subscription_status, 'trialing')

    @mock.patch('stripe.Subscription.modify')
    def test_subscription_created_handler_checkout_intent_not_found(self, mock_stripe_modify):
        """Test customer.subscription.created when CheckoutIntent is not found."""
        subscription_data = {
            'id': 'sub_test_not_found_123',
            'status': 'trialing',
            'object': 'subscription',
            'metadata': self._create_mock_stripe_subscription(99999),  # Non-existent ID
        }

        mock_event = self._create_mock_stripe_event(
            'customer.subscription.created', subscription_data
        )

        # Should raise CheckoutIntent.DoesNotExist
        with self.assertRaises(Exception):
            StripeEventHandler.dispatch(mock_event)

        # Verify stripe.Subscription.modify was NOT called
        mock_stripe_modify.assert_not_called()

    @mock.patch('stripe.Subscription.modify', side_effect=stripe.StripeError("API error"))
    def test_subscription_created_handler_stripe_error(self, mock_stripe_modify):
        """Test customer.subscription.created when Stripe API fails."""
        subscription_id = 'sub_test_stripe_error_123'
        subscription_data = {
            'id': subscription_id,
            'status': 'trialing',
            'object': 'subscription',
            'metadata': self._create_mock_stripe_subscription(self.checkout_intent.id),
        }

        mock_event = self._create_mock_stripe_event(
            'customer.subscription.created', subscription_data
        )

        # Should complete successfully despite Stripe error (error is logged but not re-raised)
        StripeEventHandler.dispatch(mock_event)

        # Verify stripe.Subscription.modify was called
        mock_stripe_modify.assert_called_once_with(
            subscription_id,
            payment_behavior='pending_if_incomplete',
        )

        # Verify event data was still created and linked to checkout intent
        event_data = StripeEventData.objects.get(event_id=mock_event.id)
        self.assertEqual(event_data.checkout_intent, self.checkout_intent)
        self.assertIsNotNone(event_data.handled_at)
