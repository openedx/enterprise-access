"""
Tests for the ``enterprise_access.customer_billing.signals`` module.
"""
from typing import cast
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.test import TestCase

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.constants import CheckoutIntentSegmentEvents, CheckoutIntentState
from enterprise_access.apps.customer_billing.models import CheckoutIntent
from enterprise_access.apps.provisioning.tests.factories import ProvisionNewCustomerWorkflowFactory

User = get_user_model()


class TestCheckoutIntentSignals(TestCase):
    """
    Tests for CheckoutIntent signal handlers.
    """

    def setUp(self):
        self.user = UserFactory()
        self.basic_data = {
            'enterprise_slug': 'test-enterprise',
            'enterprise_name': 'Test Enterprise',
            'quantity': 10,
        }

    def tearDown(self):
        CheckoutIntent.objects.all().delete()

    @mock.patch('enterprise_access.apps.customer_billing.signals.track_event')
    def test_creation_event_emitted(self, mock_track_event):
        """Test that creation event is emitted when CheckoutIntent is created."""
        CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Verify track_event was called once
        mock_track_event.assert_called_once()

        # Verify the call arguments
        call_args = mock_track_event.call_args
        self.assertEqual(call_args.kwargs['lms_user_id'], str(self.user.id))
        self.assertEqual(
            call_args.kwargs['event_name'],
            CheckoutIntentSegmentEvents.LIFECYCLE_EVENT
        )

        # Verify properties
        properties = call_args.kwargs['properties']
        self.assertIsNone(properties['previous_state'])
        self.assertEqual(properties['new_state'], CheckoutIntentState.CREATED)
        self.assertEqual(properties['enterprise_slug'], self.basic_data['enterprise_slug'])
        self.assertEqual(properties['enterprise_name'], self.basic_data['enterprise_name'])
        self.assertEqual(properties['quantity'], self.basic_data['quantity'])

    @mock.patch('enterprise_access.apps.customer_billing.signals.track_event')
    def test_transition_to_paid_event(self, mock_track_event):
        """Test that transition to paid event is emitted."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Reset mock after creation
        mock_track_event.reset_mock()

        # Transition to PAID
        intent.mark_as_paid('cs_test_123')

        # Verify track_event was called once
        mock_track_event.assert_called_once()

        # Verify the call arguments
        call_args = mock_track_event.call_args
        self.assertEqual(call_args.kwargs['lms_user_id'], str(self.user.id))
        self.assertEqual(
            call_args.kwargs['event_name'],
            CheckoutIntentSegmentEvents.LIFECYCLE_EVENT
        )

        # Verify state transition properties
        properties = call_args.kwargs['properties']
        self.assertEqual(properties['previous_state'], CheckoutIntentState.CREATED)
        self.assertEqual(properties['new_state'], CheckoutIntentState.PAID)
        self.assertEqual(properties['stripe_checkout_session_id'], 'cs_test_123')

    @mock.patch('enterprise_access.apps.customer_billing.signals.track_event')
    def test_transition_to_fulfilled_event(self, mock_track_event):
        """Test that transition to fulfilled event is emitted."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Move to PAID state
        intent.mark_as_paid('cs_test_123')

        # Reset mock after paid transition
        mock_track_event.reset_mock()

        # Transition to FULFILLED
        workflow = ProvisionNewCustomerWorkflowFactory()
        intent.mark_as_fulfilled(workflow)

        # Verify track_event was called once
        mock_track_event.assert_called_once()

        # Verify the call arguments
        call_args = mock_track_event.call_args
        self.assertEqual(call_args.kwargs['lms_user_id'], str(self.user.id))
        self.assertEqual(
            call_args.kwargs['event_name'],
            CheckoutIntentSegmentEvents.LIFECYCLE_EVENT
        )

        # Verify state transition properties
        properties = call_args.kwargs['properties']
        self.assertEqual(properties['previous_state'], CheckoutIntentState.PAID)
        self.assertEqual(properties['new_state'], CheckoutIntentState.FULFILLED)
        self.assertEqual(properties['workflow'], workflow.uuid)

    @mock.patch('enterprise_access.apps.customer_billing.signals.track_event')
    def test_transition_to_errored_fulfillment_stalled_event(self, mock_track_event):
        """Test that transition to errored_fulfillment_stalled event is emitted."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Move to PAID state
        intent.mark_as_paid('cs_test_123')

        # Reset mock after paid transition
        mock_track_event.reset_mock()

        # Transition to ERRORED_FULFILLMENT_STALLED
        error_message = 'Fulfillment stalled for 300 seconds'
        intent.mark_fulfillment_stalled(error_message)

        # Verify track_event was called once
        mock_track_event.assert_called_once()

        # Verify the call arguments
        call_args = mock_track_event.call_args
        self.assertEqual(call_args.kwargs['lms_user_id'], str(self.user.id))
        self.assertEqual(
            call_args.kwargs['event_name'],
            CheckoutIntentSegmentEvents.LIFECYCLE_EVENT
        )

        # Verify state transition properties
        properties = call_args.kwargs['properties']
        self.assertEqual(properties['previous_state'], CheckoutIntentState.PAID)
        self.assertEqual(properties['new_state'], CheckoutIntentState.ERRORED_FULFILLMENT_STALLED)
        self.assertEqual(properties['last_provisioning_error'], error_message)

    @mock.patch('enterprise_access.apps.customer_billing.signals.track_event')
    def test_transition_to_errored_provisioning_event(self, mock_track_event):
        """Test that transition to errored_provisioning event is emitted."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Move to PAID state
        intent.mark_as_paid('cs_test_123')

        # Reset mock after paid transition
        mock_track_event.reset_mock()

        # Transition to ERRORED_PROVISIONING
        error_message = 'Provisioning failed: API error'
        workflow = ProvisionNewCustomerWorkflowFactory()
        intent.mark_provisioning_error(error_message, workflow)

        # Verify track_event was called once
        mock_track_event.assert_called_once()

        # Verify the call arguments
        call_args = mock_track_event.call_args
        self.assertEqual(call_args.kwargs['lms_user_id'], str(self.user.id))
        self.assertEqual(
            call_args.kwargs['event_name'],
            CheckoutIntentSegmentEvents.LIFECYCLE_EVENT
        )

        # Verify state transition properties
        properties = call_args.kwargs['properties']
        self.assertEqual(properties['previous_state'], CheckoutIntentState.PAID)
        self.assertEqual(properties['new_state'], CheckoutIntentState.ERRORED_PROVISIONING)
        self.assertEqual(properties['last_provisioning_error'], error_message)
        self.assertEqual(properties['workflow'], workflow.uuid)

    @mock.patch('enterprise_access.apps.customer_billing.signals.track_event')
    def test_no_event_on_same_state_transition(self, mock_track_event):
        """Test that no event is emitted when state doesn't change."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Move to PAID
        intent.mark_as_paid('cs_test_123')

        # Reset mock
        mock_track_event.reset_mock()

        # Try to mark as PAID again with same session ID (allowed)
        intent.mark_as_paid('cs_test_123')

        # No event should be emitted since state didn't change
        mock_track_event.assert_not_called()

    @mock.patch('enterprise_access.apps.customer_billing.signals.track_event')
    def test_no_event_on_non_state_field_update(self, mock_track_event):
        """Test that no event is emitted when only non-state fields are updated."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Reset mock after creation
        mock_track_event.reset_mock()

        # Update a non-state field
        intent.update_stripe_session_id('cs_test_456')

        # No event should be emitted
        mock_track_event.assert_not_called()

    @mock.patch('enterprise_access.apps.customer_billing.signals.track_event')
    def test_full_happy_path_event_sequence(self, mock_track_event):
        """Test the complete sequence of events for the happy path."""
        # Create intent
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Verify creation event
        self.assertEqual(mock_track_event.call_count, 1)
        self.assertEqual(
            mock_track_event.call_args.kwargs['event_name'],
            CheckoutIntentSegmentEvents.LIFECYCLE_EVENT
        )

        # Transition to PAID
        intent.mark_as_paid('cs_test_123')
        self.assertEqual(mock_track_event.call_count, 2)
        self.assertEqual(
            mock_track_event.call_args.kwargs['event_name'],
            CheckoutIntentSegmentEvents.LIFECYCLE_EVENT
        )

        # Transition to FULFILLED
        workflow = ProvisionNewCustomerWorkflowFactory()
        intent.mark_as_fulfilled(workflow)
        self.assertEqual(mock_track_event.call_count, 3)
        self.assertEqual(
            mock_track_event.call_args.kwargs['event_name'],
            CheckoutIntentSegmentEvents.LIFECYCLE_EVENT
        )

    @mock.patch('enterprise_access.apps.customer_billing.signals.track_event')
    def test_event_contains_all_checkout_intent_fields(self, mock_track_event):
        """Test that events contain all CheckoutIntent fields."""
        terms_metadata = {'version': '1.0', 'accepted_at': '2024-01-15T10:30:00Z'}
        CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity'],
            country='US',
            terms_metadata=terms_metadata
        )

        # Verify all fields are included in the event properties
        call_args = mock_track_event.call_args
        properties = call_args.kwargs['properties']

        # Check all important fields are present
        self.assertEqual(properties['enterprise_slug'], self.basic_data['enterprise_slug'])
        self.assertEqual(properties['enterprise_name'], self.basic_data['enterprise_name'])
        self.assertEqual(properties['quantity'], self.basic_data['quantity'])
        self.assertEqual(properties['country'], 'US')
        self.assertEqual(properties['terms_metadata'], terms_metadata)
        self.assertEqual(properties['state'], CheckoutIntentState.CREATED)
        self.assertIn('id', properties)
        self.assertIn('created', properties)
        self.assertIn('modified', properties)

    @mock.patch('enterprise_access.apps.customer_billing.signals.track_event')
    def test_update_existing_intent_no_event_when_state_unchanged(self, mock_track_event):
        """Test that updating an existing CREATED intent doesn't emit event if state unchanged."""
        # Create initial intent
        CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user),
            slug='first-slug',
            name='First Enterprise',
            quantity=5
        )

        # Reset mock
        mock_track_event.reset_mock()

        # Update the intent with new data (state remains CREATED)
        CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user),
            slug='second-slug',
            name='Second Enterprise',
            quantity=10
        )

        # No event should be emitted since state didn't change (CREATED -> CREATED)
        mock_track_event.assert_not_called()
