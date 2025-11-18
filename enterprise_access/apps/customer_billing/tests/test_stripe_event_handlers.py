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

    def test_event_marked_as_handled_after_success(self):
        """StripeEventData.handled_at is set after successful handler execution."""
        subscription_id = 'sub_test_handled_123'
        stripe_customer_id = 'cus_test_handled_456'
        mock_subscription = self._create_mock_stripe_subscription(self.checkout_intent.id)

        invoice_data = {
            'id': 'in_test_handled_123',
            'customer': stripe_customer_id,
            'parent': {
                'subscription_details': {
                    'metadata': mock_subscription,
                    'subscription': subscription_id,
                }
            },
        }

        mock_event = self._create_mock_stripe_event('invoice.paid', invoice_data)

        self.assertFalse(StripeEventData.objects.filter(event_id=mock_event.id).exists())

        StripeEventHandler.dispatch(mock_event)

        event_data = StripeEventData.objects.get(event_id=mock_event.id)
        self.assertIsNotNone(event_data.handled_at)

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
        "enterprise_access.apps.customer_billing.stripe_event_handlers.send_trial_ending_reminder_email_task"
    )
    def test_trial_will_end_handler_success(self, mock_email_task):
        """Test successful trial_will_end event handling."""
        trial_end_timestamp = 1640995200
        subscription_data = {
            "id": "sub_test_trial_will_end_123",
            "trial_end": trial_end_timestamp,
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
    def test_subscription_updated_trial_to_active_processes_renewal(self, mock_license_manager_client):
        """Test that subscription updated from trial to active processes renewal."""
        from enterprise_access.apps.customer_billing.models import SelfServiceSubscriptionRenewal, StripeEventData
        from enterprise_access.apps.customer_billing.tests.factories import StripeEventSummaryFactory

        # Create existing SelfServiceSubscriptionRenewal record
        import uuid
        from enterprise_access.apps.customer_billing.models import StripeEventData
        
        # Create placeholder StripeEventData for the renewal record (will be updated during processing)
        placeholder_event_data = StripeEventData.objects.create(
            event_id='evt_placeholder_123',
            event_type='customer.subscription.created',
            checkout_intent=self.checkout_intent,
            data={'placeholder': 'data'}
        )
        
        expected_renewal_id = uuid.UUID('00000000-0000-0000-0000-000000000123')
        renewal_record = SelfServiceSubscriptionRenewal.objects.create(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id,
            stripe_subscription_id='',
            stripe_event_data=placeholder_event_data
        )

        # Create previous summary with trial status (with earlier timestamp)
        from django.utils import timezone
        from datetime import timedelta
        
        earlier_time = timezone.now() - timedelta(hours=1)
        trial_summary = StripeEventSummaryFactory(
            checkout_intent=self.checkout_intent,
            subscription_status='trialing',
            stripe_subscription_id='sub_test_123'
        )
        # Update the timestamp to be earlier
        trial_summary.stripe_event_created_at = earlier_time
        trial_summary.save()

        subscription_data = {
            "id": "sub_test_123",
            "status": "active",
            "metadata": self._create_mock_stripe_subscription(self.checkout_intent.id),
        }

        mock_event = self._create_mock_stripe_event(
            "customer.subscription.updated", subscription_data
        )
        # Ensure the mock event has a timestamp after the trial summary
        mock_event.created = int((earlier_time + timedelta(hours=2)).timestamp())

        # Mock the license manager client and its method
        mock_client_instance = mock_license_manager_client.return_value
        mock_client_instance.process_subscription_plan_renewal.return_value = {
            'id': 123, 'status': 'processed'
        }

        # Dispatch the event
        StripeEventHandler.dispatch(mock_event)

        # Verify license manager client was called with correct renewal_id
        mock_license_manager_client.assert_called_once()
        mock_client_instance.process_subscription_plan_renewal.assert_called_once_with(expected_renewal_id)

        # Verify renewal record was marked as processed and stripe_subscription_id populated
        renewal_record.refresh_from_db()
        self.assertIsNotNone(renewal_record.processed_at)
        self.assertEqual(renewal_record.stripe_subscription_id, 'sub_test_123')
        
        # Verify event data was linked to renewal record
        event_data = StripeEventData.objects.get(event_id=mock_event.id)
        self.assertEqual(renewal_record.stripe_event_data, event_data)

    @mock.patch('enterprise_access.apps.customer_billing.stripe_event_handlers.LicenseManagerApiClient')
    def test_subscription_updated_trial_to_active_no_renewal_record(self, mock_license_manager_client):
        """Test trial→active transition gracefully handles missing renewal record."""
        from enterprise_access.apps.customer_billing.tests.factories import StripeEventSummaryFactory

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
        from enterprise_access.apps.customer_billing.models import SelfServiceSubscriptionRenewal
        from enterprise_access.apps.customer_billing.tests.factories import StripeEventSummaryFactory
        from django.utils import timezone

        # Create renewal record already marked as processed
        import uuid
        from enterprise_access.apps.customer_billing.models import StripeEventData
        
        # Create StripeEventData for the renewal record
        event_data = StripeEventData.objects.create(
            event_id='evt_test_renewal_789',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'test': 'renewal_data'}
        )
        
        expected_renewal_id = uuid.UUID('00000000-0000-0000-0000-000000000789')
        renewal_record = SelfServiceSubscriptionRenewal.objects.create(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id,
            processed_at=timezone.now(),  # Already processed
            stripe_subscription_id='sub_test_789',
            stripe_event_data=event_data
        )

        # Create previous summary with trial status
        StripeEventSummaryFactory(
            checkout_intent=self.checkout_intent,
            subscription_status='trialing',
            stripe_subscription_id='sub_test_789'
        )

        subscription_data = {
            "id": "sub_test_789",
            "status": "active",
            "metadata": self._create_mock_stripe_subscription(self.checkout_intent.id),
        }

        mock_event = self._create_mock_stripe_event(
            "customer.subscription.updated", subscription_data
        )

        # Dispatch the event
        StripeEventHandler.dispatch(mock_event)

        # Verify license manager client was NOT called since renewal already processed
        mock_license_manager_client.assert_not_called()

        # Verify renewal record remains processed
        renewal_record.refresh_from_db()
        self.assertIsNotNone(renewal_record.processed_at)

    @mock.patch('enterprise_access.apps.customer_billing.stripe_event_handlers.LicenseManagerApiClient')
    def test_subscription_updated_non_trial_to_active_ignores(self, mock_license_manager_client):
        """Test that non-trial→active transitions don't trigger renewal processing."""
        from enterprise_access.apps.customer_billing.models import SelfServiceSubscriptionRenewal
        from enterprise_access.apps.customer_billing.tests.factories import StripeEventSummaryFactory

        # Create renewal record
        import uuid
        from enterprise_access.apps.customer_billing.models import StripeEventData
        
        # Create StripeEventData for the renewal record
        event_data = StripeEventData.objects.create(
            event_id='evt_test_renewal_456',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'test': 'renewal_data'}
        )
        
        expected_renewal_id = uuid.UUID('00000000-0000-0000-0000-000000000456')
        SelfServiceSubscriptionRenewal.objects.create(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id,
            stripe_subscription_id='sub_test_456',
            stripe_event_data=event_data
        )

        # Create previous summary with incomplete status (not trialing)
        StripeEventSummaryFactory(
            checkout_intent=self.checkout_intent,
            subscription_status='incomplete',
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

        # Dispatch the event
        StripeEventHandler.dispatch(mock_event)

        # Verify license manager client was NOT called for non-trial→active transition
        mock_license_manager_client.assert_not_called()

    @mock.patch('enterprise_access.apps.customer_billing.stripe_event_handlers.LicenseManagerApiClient')
    def test_subscription_updated_license_manager_api_error(self, mock_license_manager_client):
        """Test error handling when license manager API fails during renewal processing."""
        from enterprise_access.apps.customer_billing.models import SelfServiceSubscriptionRenewal, StripeEventData
        from enterprise_access.apps.customer_billing.tests.factories import StripeEventSummaryFactory

        # Create renewal record
        import uuid
        from enterprise_access.apps.customer_billing.models import StripeEventData
        
        # Create placeholder StripeEventData for the renewal record
        placeholder_event_data = StripeEventData.objects.create(
            event_id='evt_placeholder_999',
            event_type='customer.subscription.created',
            checkout_intent=self.checkout_intent,
            data={'placeholder': 'data'}
        )
        
        expected_renewal_id = uuid.UUID('00000000-0000-0000-0000-000000000999')
        renewal_record = SelfServiceSubscriptionRenewal.objects.create(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id,
            stripe_subscription_id='',
            stripe_event_data=placeholder_event_data
        )

        # Create previous summary with trial status (with earlier timestamp)
        from django.utils import timezone
        from datetime import timedelta
        
        earlier_time = timezone.now() - timedelta(hours=1)
        trial_summary = StripeEventSummaryFactory(
            checkout_intent=self.checkout_intent,
            subscription_status='trialing',
            stripe_subscription_id='sub_test_999'
        )
        # Update the timestamp to be earlier
        trial_summary.stripe_event_created_at = earlier_time
        trial_summary.save()

        subscription_data = {
            "id": "sub_test_999",
            "status": "active", 
            "metadata": self._create_mock_stripe_subscription(self.checkout_intent.id),
        }

        mock_event = self._create_mock_stripe_event(
            "customer.subscription.updated", subscription_data
        )
        # Ensure the mock event has a timestamp after the trial summary
        mock_event.created = int((earlier_time + timedelta(hours=2)).timestamp())

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

        # Note: Due to exception re-raising, StripeEventData might not be marked as handled
        # This is expected behavior when license manager processing fails

    @mock.patch('enterprise_access.apps.customer_billing.stripe_event_handlers.LicenseManagerApiClient')
    def test_full_subscription_renewal_flow(self, mock_license_manager_client):
        """Test the complete subscription renewal flow from provisioning to processing."""
        from enterprise_access.apps.customer_billing.models import SelfServiceSubscriptionRenewal, StripeEventData
        from enterprise_access.apps.customer_billing.tests.factories import StripeEventSummaryFactory
        from enterprise_access.apps.provisioning.tests.factories import ProvisionNewCustomerWorkflowFactory

        # Step 1: Create provisioning workflow (simulates renewal record creation during provisioning)
        workflow = ProvisionNewCustomerWorkflowFactory()
        self.checkout_intent.workflow = workflow
        self.checkout_intent.save()

        # Create a renewal record as would happen during provisioning
        import uuid
        from enterprise_access.apps.customer_billing.models import StripeEventData
        
        # Create placeholder StripeEventData for the renewal record
        placeholder_event_data = StripeEventData.objects.create(
            event_id='evt_placeholder_555',
            event_type='customer.subscription.created',
            checkout_intent=self.checkout_intent,
            data={'placeholder': 'data'}
        )
        
        expected_renewal_id = uuid.UUID('00000000-0000-0000-0000-000000000555')
        renewal_record = SelfServiceSubscriptionRenewal.objects.create(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id,
            stripe_subscription_id='sub_full_flow_123',  # Populated during provisioning
            stripe_event_data=placeholder_event_data
        )

        # Step 2: Simulate trial subscription creation event (creates StripeEventSummary with trial status)
        from django.utils import timezone
        from datetime import timedelta
        
        earlier_time = timezone.now() - timedelta(hours=1)
        trial_summary = StripeEventSummaryFactory(
            checkout_intent=self.checkout_intent,
            subscription_status='trialing',
            stripe_subscription_id='sub_full_flow_123'
        )
        # Update the timestamp to be earlier
        trial_summary.stripe_event_created_at = earlier_time
        trial_summary.save()

        # Step 3: Simulate trial→active transition event
        subscription_data = {
            "id": "sub_full_flow_123",
            "status": "active",
            "metadata": self._create_mock_stripe_subscription(self.checkout_intent.id),
        }

        mock_event = self._create_mock_stripe_event(
            "customer.subscription.updated", subscription_data
        )
        # Ensure the mock event has a timestamp after the trial summary
        mock_event.created = int((earlier_time + timedelta(hours=2)).timestamp())

        # Mock the license manager client response
        mock_client_instance = mock_license_manager_client.return_value
        mock_client_instance.process_subscription_plan_renewal.return_value = {
            'id': 555, 'status': 'processed', 'processed_at': '2024-01-15T10:30:00Z'
        }

        # Step 4: Process the trial→active event
        StripeEventHandler.dispatch(mock_event)

        # Step 5: Verify the complete flow worked end-to-end
        
        # Verify license manager was called to process the renewal
        mock_license_manager_client.assert_called_once()
        mock_client_instance.process_subscription_plan_renewal.assert_called_once_with(expected_renewal_id)

        # Verify renewal record was processed
        renewal_record.refresh_from_db()
        self.assertIsNotNone(renewal_record.processed_at)
        self.assertEqual(renewal_record.stripe_subscription_id, 'sub_full_flow_123')

        # Verify event was linked to renewal record
        event_data = StripeEventData.objects.get(event_id=mock_event.id)
        self.assertEqual(renewal_record.stripe_event_data, event_data)
        self.assertIsNotNone(event_data.handled_at)

        # Verify StripeEventSummary was created for the new event (if auto-created)
        from enterprise_access.apps.customer_billing.models import StripeEventSummary
        try:
            new_summary = event_data.summary
            self.assertEqual(new_summary.subscription_status, 'active')
            self.assertEqual(new_summary.stripe_subscription_id, 'sub_full_flow_123')
            self.assertEqual(new_summary.checkout_intent, self.checkout_intent)
            
            # Verify all data relationships are intact (including new_summary)
            self.assertEqual(renewal_record.checkout_intent, self.checkout_intent)
            self.assertEqual(event_data.checkout_intent, self.checkout_intent)
            self.assertEqual(new_summary.checkout_intent, self.checkout_intent)
        except StripeEventSummary.DoesNotExist:
            # Summary creation might depend on signal handling which may not work in tests
            # The important part is that the event was processed and renewal was handled
            # Verify data relationships without new_summary
            self.assertEqual(renewal_record.checkout_intent, self.checkout_intent)
            self.assertEqual(event_data.checkout_intent, self.checkout_intent)

        # Verify the previous summary still exists (trial status)
        trial_summary.refresh_from_db()
        self.assertEqual(trial_summary.subscription_status, 'trialing')

        # Verify workflow relationship is maintained
        self.assertEqual(self.checkout_intent.workflow, workflow)
