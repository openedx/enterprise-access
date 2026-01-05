"""
Tests for the ``enterprise_access.customer_billing.models`` module.
"""
import time
from datetime import timedelta
from decimal import Decimal
from random import randint
from typing import cast
from unittest import mock
from uuid import uuid4

import ddt
import stripe
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.test import TestCase, override_settings
from django.utils import timezone

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing import stripe_api
from enterprise_access.apps.customer_billing.constants import CheckoutIntentState
from enterprise_access.apps.customer_billing.models import (
    CheckoutIntent,
    FailedCheckoutIntentConflict,
    SelfServiceSubscriptionRenewal,
    SlugReservationConflict,
    StripeEventData,
    StripeEventSummary
)
from enterprise_access.apps.customer_billing.stripe_event_handlers import StripeEventHandler
from enterprise_access.apps.customer_billing.tests.factories import StripeEventDataFactory
from enterprise_access.apps.customer_billing.tests.test_stripe_event_handlers import AttrDict
from enterprise_access.apps.provisioning.models import (
    GetCreateFirstPaidSubscriptionPlanStep,
    GetCreateTrialSubscriptionPlanStep
)
from enterprise_access.apps.provisioning.tests.factories import ProvisionNewCustomerWorkflowFactory

User = get_user_model()


@ddt.ddt
class TestCheckoutIntentModel(TestCase):
    """
    Tests for the CheckoutIntent model methods.
    """

    def setUp(self):
        self.user1 = UserFactory()
        self.user2 = UserFactory()
        self.basic_data = {
            'enterprise_slug': 'test-enterprise',
            'enterprise_name': 'Test Enterprise',
            'quantity': 10,
        }

    def tearDown(self):
        CheckoutIntent.objects.all().delete()

    def test_cleanup_expired_without_mocks(self):
        """Test that cleanup_expired correctly updates state without mocking."""
        # Create a user for our test
        user = UserFactory()

        # Create several intents in different states
        # Intent 1: Expired time but still in CREATED state - should be updated
        expired_intent = CheckoutIntent.objects.create(
            user=user,
            enterprise_name="Expired Enterprise",
            enterprise_slug="expired-enterprise",
            state=CheckoutIntentState.CREATED,
            quantity=10,
            expires_at=timezone.now() - timedelta(minutes=5),
            country='US',
            terms_metadata={'version': '1.0'}
        )

        # Intent 2: Not expired time - should not be updated
        active_intent = CheckoutIntent.objects.create(
            user=UserFactory(),
            enterprise_name="Active Enterprise",
            enterprise_slug="active-enterprise",
            state=CheckoutIntentState.CREATED,
            quantity=15,
            expires_at=timezone.now() + timedelta(minutes=30),
            country='CA',
            terms_metadata={'version': '1.1'}
        )

        # Intent 3: Expired time but already in PAID state - should not be updated
        paid_intent = CheckoutIntent.objects.create(
            user=UserFactory(),
            enterprise_name="Paid Enterprise",
            enterprise_slug="paid-enterprise",
            state=CheckoutIntentState.PAID,
            quantity=20,
            expires_at=timezone.now() - timedelta(minutes=10),
            country='GB',
            terms_metadata={'version': '2.0'}
        )

        # Run the cleanup method
        updated_count = CheckoutIntent.cleanup_expired()

        # Verify it returns the correct count of updated intents
        self.assertEqual(updated_count, 1, "Should have updated exactly one intent")

        # Verify the expired intent was updated to EXPIRED state
        expired_intent.refresh_from_db()
        self.assertEqual(expired_intent.state, CheckoutIntentState.EXPIRED)

        # Verify the active intent was not updated
        active_intent.refresh_from_db()
        self.assertEqual(active_intent.state, CheckoutIntentState.CREATED)

        # Verify the paid intent was not updated
        paid_intent.refresh_from_db()
        self.assertEqual(paid_intent.state, CheckoutIntentState.PAID)

    def test_create_intent_success(self):
        """Test successful creation of checkout intent."""
        terms_metadata = {'version': '1.0', 'accepted_at': '2024-01-15T10:30:00Z'}
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity'],
            terms_metadata=terms_metadata
        )

        self.assertEqual(intent.user, self.user1)
        self.assertEqual(intent.enterprise_slug, self.basic_data['enterprise_slug'])
        self.assertEqual(intent.enterprise_name, self.basic_data['enterprise_name'])
        self.assertEqual(intent.quantity, self.basic_data['quantity'])
        self.assertEqual(intent.terms_metadata, terms_metadata)
        self.assertEqual(intent.state, CheckoutIntentState.CREATED)
        self.assertIsNone(intent.workflow)
        self.assertFalse(intent.is_expired())

    def test_create_intent_name_slug_conflict(self):
        """Test that creating an intent with conflicting slug/name fails."""
        # First user reserves slug and name
        CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Second user tries to use same slug
        with self.assertRaises(SlugReservationConflict):
            CheckoutIntent.create_intent(
                user=cast(AbstractUser, self.user2),
                slug=self.basic_data['enterprise_slug'],
                name='Different Enterprise',
                quantity=self.basic_data['quantity']
            )

        # Second user tries to use same name
        with self.assertRaises(SlugReservationConflict):
            CheckoutIntent.create_intent(
                user=cast(AbstractUser, self.user2),
                slug='different-slug',
                name=self.basic_data['enterprise_name'],
                quantity=self.basic_data['quantity']
            )

    def test_create_intent_updates_existing(self):
        """Test that creating an intent updates the user's existing one."""
        # Create first intent
        first_terms = {'version': '1.0', 'accepted_at': '2024-01-15T10:30:00Z'}
        first_intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug='first-slug',
            name='First Enterprise',
            quantity=5,
            terms_metadata=first_terms
        )

        # Create second intent for same user
        second_terms = {'version': '1.1', 'accepted_at': '2024-01-20T14:45:00Z'}
        second_intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug='second-slug',
            name='Second Enterprise',
            quantity=10,
            terms_metadata=second_terms
        )

        # Should be the same object but with updated fields
        self.assertEqual(first_intent.id, second_intent.id)
        self.assertEqual(second_intent.enterprise_slug, 'second-slug')
        self.assertEqual(second_intent.enterprise_name, 'Second Enterprise')
        self.assertEqual(second_intent.quantity, 10)
        self.assertEqual(second_intent.terms_metadata, second_terms)

        # Should still only have one intent for this user
        self.assertEqual(CheckoutIntent.objects.filter(user=self.user1).count(), 1)

    def test_create_intent_with_existing_failed_intent(self):
        """
        Test that trying to update an intent that's in a failure state raises an exception.
        """
        # First create a normal intent
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug='original-slug',
            name='Original Enterprise',
            quantity=5
        )

        # Now put it in a failure state
        intent.mark_as_paid('cs_test_123')
        intent.mark_backoffice_error('Salesforce integration failed')
        self.assertIn(intent.state, CheckoutIntent.FAILURE_STATES)

        # Trying to create a new intent for the same user should fail
        with self.assertRaises(FailedCheckoutIntentConflict) as context:
            CheckoutIntent.create_intent(
                user=cast(AbstractUser, self.user1),
                slug='new-slug',
                name='New Enterprise',
                quantity=10
            )

        # Verify error message mentions the failed intent
        self.assertIn("failed", str(context.exception).lower())

        # The original intent should remain unchanged
        intent.refresh_from_db()
        self.assertEqual(intent.enterprise_slug, 'original-slug')
        self.assertEqual(intent.enterprise_name, 'Original Enterprise')
        self.assertEqual(intent.quantity, 5)
        self.assertEqual(intent.state, CheckoutIntentState.ERRORED_BACKOFFICE)

    def test_state_transitions_happy_path(self):
        """Test the state transitions for the happy path."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # CREATED → PAID
        intent.mark_as_paid('cs_test_123')
        self.assertEqual(intent.state, CheckoutIntentState.PAID)
        self.assertEqual(intent.stripe_checkout_session_id, 'cs_test_123')

        # PAID → PAID
        intent.mark_as_paid('cs_test_123')
        self.assertEqual(intent.state, CheckoutIntentState.PAID)
        self.assertEqual(intent.stripe_checkout_session_id, 'cs_test_123')

        # PAID → FULFILLED
        workflow = ProvisionNewCustomerWorkflowFactory()
        intent.mark_as_fulfilled(workflow)
        self.assertEqual(intent.state, CheckoutIntentState.FULFILLED)
        self.assertEqual(intent.workflow, workflow)

    def test_state_transitions_error_paths(self):
        """Test state transitions for error paths."""
        # Test PAID → ERRORED_BACKOFFICE
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )
        intent.mark_as_paid('cs_test_123')
        intent.mark_backoffice_error('Salesforce integration failed')
        self.assertEqual(intent.state, CheckoutIntentState.ERRORED_BACKOFFICE)
        self.assertEqual(intent.last_provisioning_error, 'Salesforce integration failed')

        # Test PAID → ERRORED_PROVISIONING
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user2),
            slug='another-slug',
            name='Another Enterprise',
            quantity=7
        )
        intent.mark_as_paid('cs_test_456')
        workflow = ProvisionNewCustomerWorkflowFactory()
        intent.mark_provisioning_error('Provisioning failed: API error', workflow)
        self.assertEqual(intent.state, CheckoutIntentState.ERRORED_PROVISIONING)
        self.assertEqual(intent.last_provisioning_error, 'Provisioning failed: API error')
        self.assertEqual(intent.workflow, workflow)

    def test_invalid_state_transitions(self):
        """Test that invalid state transitions raise exceptions."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Move to ERRORED_PROVISIONING.
        intent.mark_as_paid()
        intent.mark_provisioning_error('Provisioning failed')

        # Should not be able to transition from ERRORED_PROVISIONING to PAID,
        # as this would require going back in time.
        with self.assertRaises(ValueError):
            intent.mark_as_paid()

        # Create a new intent
        intent2 = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user2),
            slug='another-slug',
            name='Another Enterprise',
            quantity=7
        )

        # Should not be able to go from CREATED to FULFILLED (skipping PAID)
        with self.assertRaises(ValueError):
            intent2.mark_as_fulfilled()

    def test_invalid_state_transitions_paid_different_session(self):
        """Attempting a PAID->PAID state transition with different checkout_session_id raises an exception."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Mark as paid
        intent.mark_as_paid('foobar')

        # Should not be able to transition from paid to paid while changing the stripe checkout session ID
        with self.assertRaises(ValueError):
            intent.mark_as_paid('binbaz')

    def test_intent_expiration(self):
        """Test intent expiration logic."""
        # Create an intent with expiry in the past
        past_time = timezone.now() - timedelta(minutes=5)

        intent = CheckoutIntent.objects.create(
            user=self.user1,
            state=CheckoutIntentState.CREATED,
            enterprise_slug='expired-slug',
            enterprise_name='Expired Enterprise',
            quantity=5,
            expires_at=past_time,
            country='FR',
            terms_metadata={'version': '1.0', 'test': True}
        )

        self.assertTrue(intent.is_expired())

        # Test the cleanup method
        with mock.patch.object(CheckoutIntent, 'cleanup_expired') as mock_cleanup:
            CheckoutIntent.cleanup_expired.return_value = 1
            CheckoutIntent.create_intent(
                user=cast(AbstractUser, self.user2),
                slug='new-slug',
                name='New Enterprise',
                quantity=10
            )
            mock_cleanup.assert_called_once()

    def test_admin_portal_url(self):
        """Test the admin_portal_url property."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Should be None before FULFILLED
        self.assertIsNone(intent.admin_portal_url)

        # Move to PAID
        intent.mark_as_paid()
        self.assertIsNone(intent.admin_portal_url)

        # Move to FULFILLED
        with self.settings(ENTERPRISE_ADMIN_PORTAL_URL='https://admin.example.com'):
            intent.mark_as_fulfilled()
            self.assertEqual(
                intent.admin_portal_url,
                f"https://admin.example.com/{self.basic_data['enterprise_slug']}"
            )

    def test_can_reserve(self):
        """Test the can_reserve method."""
        # Initially available
        self.assertTrue(CheckoutIntent.can_reserve(
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name']
        ))

        # Create an intent
        CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Now it should not be available
        self.assertFalse(CheckoutIntent.can_reserve(
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name']
        ))

        # But should be available for the same user
        self.assertTrue(CheckoutIntent.can_reserve(
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            exclude_user=self.user1
        ))

    def test_can_reserve_with_expired_state_future_date(self):
        """
        Test that can_reserve returns True when an intent with matching name/slug
        exists but is already marked as EXPIRED, even with a future expiration date.
        """
        # Create a user for our test
        user = UserFactory()

        # Create an intent with a future expiration date but EXPIRED state
        CheckoutIntent.objects.create(
            user=user,
            enterprise_name="Future Enterprise",
            enterprise_slug="future-enterprise",
            state=CheckoutIntentState.EXPIRED,  # Already expired state
            quantity=10,
            expires_at=timezone.now() + timedelta(hours=2),  # Future date
            country='DE',
            terms_metadata={'version': '1.5', 'future': True}
        )

        # Check if we can reserve the same name and slug
        can_reserve_name = CheckoutIntent.can_reserve(name="Future Enterprise")
        can_reserve_slug = CheckoutIntent.can_reserve(slug="future-enterprise")

        # Both should return True because the existing intent is in EXPIRED state
        self.assertTrue(
            can_reserve_name,
            "Should be able to reserve a name used by an EXPIRED intent even with future date"
        )
        self.assertTrue(
            can_reserve_slug,
            "Should be able to reserve a slug used by an EXPIRED intent even with future date"
        )

        can_reserve_both = CheckoutIntent.can_reserve(
            name="Future Enterprise",
            slug="future-enterprise",
        )
        self.assertTrue(
            can_reserve_both,
            "Combined can_reserve should return True for EXPIRED intent"
        )

    def test_filter_by_name_and_slug(self):
        """Test the filter_by_name_and_slug method."""
        # Create an intent
        CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug='test-slug-123',
            name='Test Name 123',
            quantity=5
        )

        # Should be able to find by slug
        results = CheckoutIntent.filter_by_name_and_slug(slug='test-slug-123')
        self.assertEqual(results.count(), 1)

        # Should be able to find by name
        results = CheckoutIntent.filter_by_name_and_slug(name='Test Name 123')
        self.assertEqual(results.count(), 1)

        # Should find nothing for non-existent values
        results = CheckoutIntent.filter_by_name_and_slug(slug='nonexistent')
        self.assertEqual(results.count(), 0)

        # Should raise error if neither slug nor name is provided
        with self.assertRaises(ValueError):
            CheckoutIntent.filter_by_name_and_slug()

    @override_settings(INTENT_RESERVATION_DURATION_MINUTES=60)
    def test_custom_reservation_duration(self):
        """Test that custom reservation duration is respected."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Should expire in 60 minutes based on settings
        expected_expiry = timezone.now() + timedelta(minutes=60)
        time_diff = abs((intent.expires_at - expected_expiry).total_seconds())

        # Allow a 1 second tolerance for test execution time
        self.assertLess(time_diff, 1)

    def test_update_stripe_session_id(self):
        """Test updating Stripe session ID."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        original_modified = intent.modified

        # Update session ID
        intent.update_stripe_session_id('cs_test_789')

        intent.refresh_from_db()
        self.assertEqual(intent.stripe_checkout_session_id, 'cs_test_789')
        self.assertGreater(intent.modified, original_modified)

    def test_create_intent_with_terms_metadata(self):
        """Test creating intent with various terms_metadata values."""
        # Test with None (should work)
        intent1 = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug='test-slug-1',
            name='Test Enterprise 1',
            quantity=5,
            terms_metadata=None
        )
        self.assertIsNone(intent1.terms_metadata)

        # Test with empty dict (should work)
        intent2 = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user2),
            slug='test-slug-2',
            name='Test Enterprise 2',
            quantity=10,
            terms_metadata={}
        )
        self.assertEqual(intent2.terms_metadata, {})

        # Test with complex metadata
        complex_metadata = {
            'version': '2.1',
            'accepted_at': '2024-01-15T10:30:00Z',
            'user_agent': 'Mozilla/5.0...',
            'ip_address': '192.168.1.100',
            'accepted_sections': ['privacy', 'terms', 'cookies'],
            'preferences': {
                'marketing': True,
                'analytics': False
            }
        }
        user3 = UserFactory()
        intent3 = CheckoutIntent.create_intent(
            user=cast(AbstractUser, user3),
            slug='test-slug-3',
            name='Test Enterprise 3',
            quantity=15,
            terms_metadata=complex_metadata
        )
        self.assertEqual(intent3.terms_metadata, complex_metadata)

    def test_mark_as_paid_with_stripe_customer_id(self):
        """Test mark_as_paid with stripe_customer_id parameter."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Test marking as paid with both session_id and customer_id
        intent.mark_as_paid('cs_test_123', 'cus_test_456')
        self.assertEqual(intent.state, CheckoutIntentState.PAID)
        self.assertEqual(intent.stripe_checkout_session_id, 'cs_test_123')
        self.assertEqual(intent.stripe_customer_id, 'cus_test_456')

    def test_mark_as_paid_with_only_stripe_customer_id(self):
        """Test mark_as_paid with only stripe_customer_id parameter."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user2),
            slug='another-slug',
            name='Another Enterprise',
            quantity=7
        )

        # Test marking as paid with only customer_id
        intent.mark_as_paid(stripe_customer_id='cus_test_789')
        self.assertEqual(intent.state, CheckoutIntentState.PAID)
        self.assertIsNone(intent.stripe_checkout_session_id)
        self.assertEqual(intent.stripe_customer_id, 'cus_test_789')

    def test_mark_as_paid_idempotent_with_stripe_customer_id(self):
        """Test that mark_as_paid is idempotent when called with same stripe_customer_id."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Mark as paid first time
        intent.mark_as_paid('cs_test_123', 'cus_test_456')
        first_modified = intent.modified

        # Mark as paid again with same values - should be idempotent
        intent.mark_as_paid('cs_test_123', 'cus_test_456')
        self.assertEqual(intent.state, CheckoutIntentState.PAID)
        self.assertEqual(intent.stripe_checkout_session_id, 'cs_test_123')
        self.assertEqual(intent.stripe_customer_id, 'cus_test_456')
        # Modified time should have changed since we called save()
        self.assertGreater(intent.modified, first_modified)

    def test_mark_as_paid_different_stripe_customer_id_raises_error(self):
        """Test that mark_as_paid raises error when called with different stripe_customer_id."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Mark as paid first time
        intent.mark_as_paid(stripe_customer_id='cus_test_456')

        # Try to mark as paid with different customer_id - should raise ValueError
        with self.assertRaises(ValueError) as context:
            intent.mark_as_paid(stripe_customer_id='cus_test_different')

        self.assertIn('Cannot transition from PAID to PAID with a different stripe_customer_id', str(context.exception))

    def test_mark_as_paid_update_fields_includes_stripe_customer_id(self):
        """Test that save() includes stripe_customer_id in update_fields."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        with mock.patch.object(intent, 'save') as mock_save:
            intent.mark_as_paid(stripe_customer_id='cus_test_456')

            # Verify save was called with correct update_fields
            mock_save.assert_called_once_with(
                update_fields=['state', 'stripe_checkout_session_id', 'stripe_customer_id', 'modified']
            )

    def test_create_intent_patch_fields_do_not_unset(self):
        """
        Test that calling create_intent() without supplying slug, name, country, or terms_metadata
        does NOT unset those fields in the DB record (acts like PATCH).
        """
        user = UserFactory()
        initial_intent = CheckoutIntent.create_intent(
            user=user,
            quantity=11,
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            country='JP',
            terms_metadata={'foo': 'bar'}
        )

        # Call create_intent() again, omitting slug, name, country, and terms_metadata
        updated_intent = CheckoutIntent.create_intent(
            user=user,
            quantity=22  # Only update quantity
            # No slug, name, country, or terms_metadata
        )

        initial_intent.refresh_from_db()

        # Make sure we only created one new intent record, not two.
        assert initial_intent == updated_intent
        # Should have updated quantity field only.
        self.assertEqual(updated_intent.quantity, 22)  # Updated
        # Old values should remain the same as they were omitted from the 2nd call.
        assert updated_intent.enterprise_slug == self.basic_data['enterprise_slug']
        assert updated_intent.enterprise_name == self.basic_data['enterprise_name']
        assert updated_intent.country == 'JP'
        assert updated_intent.terms_metadata == {'foo': 'bar'}

    def test_create_intent_blank_slug_and_name_success(self):
        """
        Test that calling create_intent() with both slug and name blank does not throw.

        This test case supports the use case of creating an intent without reserving,
        needed for the Plan Details page.
        """
        user = UserFactory()
        intent = CheckoutIntent.create_intent(
            user=user,
            quantity=self.basic_data['quantity'],
            slug=None,
            name=None,
        )
        intent.refresh_from_db()
        assert intent.enterprise_slug is None
        assert intent.enterprise_name is None
        assert intent.quantity == self.basic_data['quantity']

    @ddt.data(
        {'slug': 'present-slug', 'name': None},
        {'slug': None, 'name': 'Present Name'}
    )
    @ddt.unpack
    def test_create_intent_blank_slug_or_name_raises(self, slug, name):
        """
        Test that calling create_intent() with a non-blank slug and blank name, or vice-versa,
        raises a ValueError.
        """
        user = UserFactory()
        with self.assertRaises(ValueError) as exc:
            CheckoutIntent.create_intent(
                user=user,
                quantity=self.basic_data['quantity'],
                slug=slug,
                name=name,
            )
        self.assertIn("slug and name must either both be given or neither be given", str(exc.exception))

    def test_uuid_field_not_null_and_has_default(self):
        """
        Test that the uuid field is not nullable and automatically generates a UUID.
        """
        user = UserFactory()

        # Create an intent without explicitly setting uuid
        intent = CheckoutIntent.create_intent(
            user=user,
            slug='test-uuid-slug',
            name='Test UUID Enterprise',
            quantity=5
        )

        # Verify uuid was automatically generated
        self.assertIsNotNone(intent.uuid)
        self.assertEqual(len(str(intent.uuid)), 36)  # Standard UUID string length

        # Create another intent and verify it gets a different UUID
        user2 = UserFactory()
        intent2 = CheckoutIntent.create_intent(
            user=user2,
            slug='test-uuid-slug-2',
            name='Test UUID Enterprise 2',
            quantity=10
        )

        self.assertIsNotNone(intent2.uuid)
        self.assertNotEqual(intent.uuid, intent2.uuid)

        # Verify we can still manually set a UUID if needed
        custom_uuid = uuid4()
        intent3 = CheckoutIntent.objects.create(
            user=UserFactory(),
            enterprise_name="Manual UUID Enterprise",
            enterprise_slug="manual-uuid-enterprise",
            state=CheckoutIntentState.CREATED,
            quantity=15,
            expires_at=timezone.now() + timedelta(minutes=30),
            uuid=custom_uuid
        )
        self.assertEqual(intent3.uuid, custom_uuid)


class TestStripeEventSummary(TestCase):
    """Test cases for StripeEventSummary model and populate_with_summary_data method."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory()
        cls.checkout_intent = CheckoutIntent.objects.create(
            uuid=uuid4(),
            user=cls.user,
            enterprise_name='Test Enterprisee',
            enterprise_slug='test-enterprisee',
            stripe_customer_id='cus_test_456',
            quantity=10,
            expires_at=timezone.now() + timedelta(minutes=30),
        )

    def test_populate_with_summary_data_invoice_event(self):
        """Test populating summary from invoice.paid event with full invoice data."""
        # Create mock invoice event data
        invoice_event_data = {
            'id': 'evt_test_invoice',
            'type': 'invoice.paid',
            'data': {
                'object': {
                    'object': 'invoice',
                    'id': 'in_test_123',
                    'subscription': 'sub_test_456',
                    'amount_paid': 2500,  # $25.00 in cents
                    'currency': 'usd',
                    'lines': {
                        'data': [
                            {
                                'quantity': 10,
                                'pricing': {
                                    'unit_amount_decimal': '250.0'  # $2.50 per unit
                                }
                            }
                        ]
                    },
                    'parent': {
                        'subscription_details': {
                            'subscription': 'sub_test_456'
                        }
                    }
                }
            }
        }

        # Create StripeEventData
        # The connected signal receiver will create the related summary record
        stripe_event_data = StripeEventData.objects.create(
            event_id='evt_test_invoice',
            event_type='invoice.paid',
            checkout_intent=self.checkout_intent,
            data=invoice_event_data
        )
        summary = stripe_event_data.summary
        summary.populate_with_summary_data()
        summary.save()

        # Verify invoice-specific fields are populated
        self.assertEqual(summary.event_id, 'evt_test_invoice')
        self.assertEqual(summary.event_type, 'invoice.paid')
        self.assertEqual(summary.checkout_intent, self.checkout_intent)
        self.assertEqual(summary.stripe_object_type, 'invoice')
        self.assertEqual(summary.stripe_invoice_id, 'in_test_123')
        self.assertEqual(summary.stripe_subscription_id, 'sub_test_456')
        self.assertEqual(summary.invoice_amount_paid, 2500)
        self.assertEqual(summary.invoice_currency, 'usd')
        self.assertEqual(summary.invoice_unit_amount, 250)
        self.assertEqual(summary.invoice_unit_amount_decimal, Decimal(250.0))
        self.assertEqual(summary.invoice_quantity, 10)

    def test_populate_with_summary_data_subscription_created_event(self):
        """Test populating summary from customer.subscription.created event."""
        # Create mock subscription event data
        subscription_event_data = {
            'id': 'evt_test_sub_created',
            'type': 'customer.subscription.created',
            'data': {
                'object': {
                    'object': 'subscription',
                    'id': 'sub_test_789',
                    'status': 'active',
                    'currency': 'usd',
                    'items': {
                        'data': [
                            {
                                'current_period_start': 1609459200,  # 2021-01-01 00:00:00 UTC
                                'current_period_end': 1640995200,    # 2022-01-01 00:00:00 UTC
                            }
                        ]
                    }
                }
            }
        }

        # Create StripeEventData
        stripe_event_data = StripeEventData.objects.create(
            event_id='evt_test_sub_created',
            event_type='customer.subscription.created',
            checkout_intent=self.checkout_intent,
            data=subscription_event_data,
        )

        # Create and populate summary
        summary = StripeEventSummary(stripe_event_data=stripe_event_data)
        summary.populate_with_summary_data()

        # Verify subscription-specific fields are populated
        self.assertEqual(summary.event_id, 'evt_test_sub_created')
        self.assertEqual(summary.event_type, 'customer.subscription.created')
        self.assertEqual(summary.checkout_intent, self.checkout_intent)
        self.assertEqual(summary.stripe_object_type, 'subscription')
        self.assertEqual(summary.stripe_subscription_id, 'sub_test_789')
        self.assertEqual(summary.subscription_status, 'active')

        # Verify datetime conversion
        self.assertIsNotNone(summary.subscription_period_start)
        self.assertIsNotNone(summary.subscription_period_end)

        # Check that the converted dates are reasonable (2021-2022)
        self.assertEqual(summary.subscription_period_start.year, 2021)
        self.assertEqual(summary.subscription_period_end.year, 2022)

    def test_populate_with_summary_data_extracts_cancel_at(self):
        """Test that populate_with_summary_data correctly extracts subscription cancel_at."""
        # Create timestamp for cancel_at (14 days from now)
        cancel_at_timestamp = int((timezone.now() + timedelta(days=14)).timestamp())

        # Create mock subscription event data with cancel_at
        subscription_event_data = {
            'id': 'evt_test_sub_cancel_at',
            'type': 'customer.subscription.updated',
            'data': {
                'object': {
                    'object': 'subscription',
                    'id': 'sub_test_cancel_123',
                    'status': 'trialing',
                    'currency': 'usd',
                    'cancel_at': cancel_at_timestamp,
                    'items': {
                        'data': [
                            {
                                'current_period_start': 1609459200,  # 2021-01-01 00:00:00 UTC
                                'current_period_end': 1640995200,    # 2022-01-01 00:00:00 UTC
                            }
                        ]
                    }
                }
            }
        }

        # Create StripeEventData
        stripe_event_data = StripeEventData.objects.create(
            event_id='evt_test_sub_cancel_at',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data=subscription_event_data,
        )

        # Create and populate summary
        summary = StripeEventSummary(stripe_event_data=stripe_event_data)
        summary.populate_with_summary_data()

        # Verify cancel_at is extracted and converted to datetime
        self.assertIsNotNone(summary.subscription_cancel_at)
        # Verify it's within a reasonable range (should be around 14 days from now)
        expected_cancel_at = timezone.now() + timedelta(days=14)
        time_diff = abs((summary.subscription_cancel_at - expected_cancel_at).total_seconds())
        self.assertLess(time_diff, 60, "cancel_at timestamp should be within 60 seconds of expected value")

    def test_populate_with_summary_data_with_subscription_plan_uuid(self):
        """Test that subscription_plan_uuid is extracted from related workflow."""
        # Create a workflow
        workflow = ProvisionNewCustomerWorkflowFactory()

        # Create checkout intent linked to the workflow
        checkout_intent = CheckoutIntent.create_intent(
            user=self.user,
            slug='test-enterprise-workflow',
            name='Test Enterprise Workflow',
            quantity=5
        )
        checkout_intent.workflow = workflow
        checkout_intent.save()

        # Create a subscription plan step with output containing subscription_plan_uuid
        trial_subscription_plan_uuid = uuid4()
        _ = GetCreateTrialSubscriptionPlanStep.objects.create(
            workflow_record_uuid=workflow.uuid,
            input_data={
                'title': 'Test Plan',
                'salesforce_opportunity_line_item': 'test-oli-123',
                'start_date': '2024-01-01T00:00:00Z',
                'expiration_date': '2025-01-01T00:00:00Z',
                'desired_num_licenses': 5,
                'product_id': 123,
            },
            output_data={
                'uuid': str(trial_subscription_plan_uuid),  # This is what we're testing
                'title': 'Test Plan',
                'salesforce_opportunity_line_item': 'test-oli-123',
                'created': '2024-01-01T00:00:00Z',
                'start_date': '2024-01-01T00:00:00Z',
                'expiration_date': '2025-01-01T00:00:00Z',
                'is_active': True,
                'is_current': True,
                'plan_type': 'Subscription',
                'enterprise_catalog_uuid': str(uuid4()),
                'product': 123,
                'desired_num_licenses': 5,
            }
        )

        first_paid_subscription_plan_uuid = uuid4()
        _ = GetCreateFirstPaidSubscriptionPlanStep.objects.create(
            workflow_record_uuid=workflow.uuid,
            input_data={
                'title': 'First Paid Plan',
                'salesforce_opportunity_line_item': None,
                'start_date': '2025-01-01T00:00:00Z',
                'expiration_date': '2026-01-01T00:00:00Z',
                'desired_num_licenses': 5,
                'product_id': 456,
            },
            output_data={
                'uuid': str(first_paid_subscription_plan_uuid),  # Should be IGNORED.
                'title': 'First Paid Plan',
                'salesforce_opportunity_line_item': None,
                'created': '2024-01-01T00:00:00Z',
                'start_date': '2025-01-01T00:00:00Z',
                'expiration_date': '2026-01-01T00:00:00Z',
                'is_active': True,
                'is_current': True,
                'plan_type': 'Subscription',
                'enterprise_catalog_uuid': str(uuid4()),
                'product': 456,
                'desired_num_licenses': 5,
            }
        )

        # Create mock subscription event data
        subscription_event_data = {
            'id': 'evt_test_with_plan_uuid',
            'type': 'customer.subscription.created',
            'data': {
                'object': {
                    'object': 'subscription',
                    'id': 'sub_test_with_uuid',
                    'currency': 'usd',
                    'status': 'active',
                    'items': {
                        'data': [
                            {
                                'current_period_start': 1609459200,
                                'current_period_end': 1640995200,
                            }
                        ]
                    }
                }
            }
        }

        # Create StripeEventData linked to the checkout intent with workflow
        stripe_event_data = StripeEventData.objects.create(
            event_id='evt_test_with_plan_uuid',
            event_type='customer.subscription.created',
            checkout_intent=checkout_intent,
            data=subscription_event_data
        )

        # Create and populate summary
        summary = StripeEventSummary(stripe_event_data=stripe_event_data)
        summary.populate_with_summary_data()

        # Verify that trial_subscription_plan_uuid was extracted from the workflow
        self.assertEqual(summary.subscription_plan_uuid, trial_subscription_plan_uuid)
        self.assertEqual(summary.event_id, 'evt_test_with_plan_uuid')
        self.assertEqual(summary.event_type, 'customer.subscription.created')
        self.assertEqual(summary.checkout_intent, checkout_intent)
        self.assertEqual(summary.stripe_subscription_id, 'sub_test_with_uuid')
        self.assertEqual(summary.subscription_status, 'active')

    def test_checkout_intent_previous_summary(self):
        """
        Test CheckoutIntent.previous_summary() method returns most recent
        summary before given event.
        """
        # Create multiple StripeEventData and StripeEventSummary records with different timestamps
        event_times = [
            1609459200,  # 2021-01-01 00:00:00 UTC - earliest
            1609462800,  # 2021-01-01 01:00:00 UTC - middle
            1609466400,  # 2021-01-01 02:00:00 UTC - latest
            1609470000,  # 2021-01-01 03:00:00 UTC - current event (not in summaries)
        ]

        summaries = []
        for i, event_time in enumerate(event_times[:3]):  # Create 3 summaries
            # Create StripeEventData
            event_data = {
                'id': f'evt_test_{i}',
                'type': 'invoice.paid',
                'created': event_time,
                'data': {
                    'object': {
                        'object': 'invoice',
                        'id': f'in_test_{i}',
                        'amount_paid': 1000 + i,
                        'currency': 'usd',
                        'lines': {'data': []},
                        'parent': {'subscription_details': {'subscription': 'sub_test'}}
                    }
                }
            }

            # The post_save signal receiver automatically populates
            # a related StripeEventSummary.
            stripe_event_data = StripeEventData.objects.create(
                event_id=f'evt_test_{i}',
                event_type='invoice.paid',
                checkout_intent=self.checkout_intent,
                data=event_data
            )
            summary = stripe_event_data.summary
            summary.populate_with_summary_data()
            summary.save()
            summaries.append(summary)

        # Verify summaries have correct timestamps
        self.assertEqual(summaries[0].stripe_event_created_at.timestamp(), event_times[0])
        self.assertEqual(summaries[1].stripe_event_created_at.timestamp(), event_times[1])
        self.assertEqual(summaries[2].stripe_event_created_at.timestamp(), event_times[2])

        # Create a mock Stripe event with timestamp after all summaries
        current_event = mock.Mock(spec=stripe.Event)
        current_event.created = event_times[3]  # 2021-01-01 03:00:00 UTC

        # Test: previous_summary should return the most recent summary (index 2)
        previous = self.checkout_intent.previous_summary(current_event)
        self.assertEqual(previous, summaries[2])
        self.assertEqual(previous.event_id, 'evt_test_2')

        # Test: event between first and second summary should return first summary
        middle_event = mock.Mock(spec=stripe.Event)
        middle_event.created = event_times[1] - 1  # Just before second event
        previous = self.checkout_intent.previous_summary(middle_event)
        self.assertEqual(previous, summaries[0])

        # Test: event before all summaries should return None
        early_event = mock.Mock(spec=stripe.Event)
        early_event.created = event_times[0] - 1  # Before any summaries
        previous = self.checkout_intent.previous_summary(early_event)
        self.assertIsNone(previous)

        # Test: with different checkout intent should return None
        other_user = UserFactory()
        other_checkout_intent = CheckoutIntent.create_intent(
            user=other_user,
            slug='other-enterprise',
            name='Other Enterprise',
            quantity=5
        )
        previous = other_checkout_intent.previous_summary(current_event)
        self.assertIsNone(previous)

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

    @mock.patch.object(stripe_api, 'stripe')
    def test_upcoming_invoice_amount_due(self, mock_stripe):
        """
        Test that the `upcoming_invoice_amount_due` value in the StripeEventSummary model is populated
        from the upcoming invoice
        """
        subscription_data_object = {
            'object': 'subscription',
            'id': 'sub_test_789',
            'status': 'active',
            'currency': 'usd',
            'customer': 'cus_test_456',
            'items': {
                'data': [
                    {
                        'object': 'subscription_item',
                        'current_period_start': 1609459200,  # 2021-01-01 00:00:00 UTC
                        'current_period_end': 1640995200,    # 2022-01-01 00:00:00 UTC
                    }
                ]
            },
            'metadata': {
                'checkout_intent_id': self.checkout_intent.id,
                'enterprise_customer_name': 'Test Enterprise',
                'enterprise_customer_slug': 'test-enterprise',
            }
        }
        subscription_event_data = {
            'id': 'evt_test_sub_created',
            'type': 'customer.subscription.created',
            'data': {
                'object': subscription_data_object
            }
        }

        # Create subscription created stripe event
        mock_event = self._create_mock_stripe_event(
            "customer.subscription.created", subscription_data_object
        )

        # Create StripeEventData
        stripe_event_data = StripeEventData.objects.create(
            event_id=mock_event.id,
            event_type='customer.subscription.created',
            checkout_intent=self.checkout_intent,
            data=subscription_event_data
        )

        # Mock out the 'preview invoice' stripe call
        fake_invoice = {'amount_due': '200'}
        mock_stripe.Invoice.create_preview.return_value = fake_invoice

        # Dispatch creation event, which populates the upcoming_invoice_amount_due value
        StripeEventHandler.dispatch(mock_event)

        summary = StripeEventSummary.objects.get(event_id=stripe_event_data.event_id)
        self.assertEqual(summary.upcoming_invoice_amount_due, 200)


class TestCheckoutIntentStalledFulfillment(TestCase):
    """
    Tests for CheckoutIntent stalled fulfillment detection and handling.
    """

    def setUp(self):
        self.user = UserFactory()

    def _create_intent(self, state, slug_suffix='', **kwargs):
        """Helper to create a CheckoutIntent with minimal boilerplate."""
        defaults = {
            'user': kwargs.pop('user', self.user),
            'state': state,
            'enterprise_name': f'Test Enterprise{slug_suffix}',
            'enterprise_slug': f'test-enterprise{slug_suffix}',
            'quantity': 10,
            'expires_at': timezone.now() + timedelta(hours=1),
        }
        defaults.update(kwargs)
        return CheckoutIntent.objects.create(**defaults)

    def test_mark_stalled_fulfillment_intents_finds_stalled_intents(self):
        """Test class method finds and marks stalled intents."""
        intent = self._create_intent(CheckoutIntentState.PAID)
        CheckoutIntent.objects.filter(pk=intent.pk).update(
            modified=timezone.now() - timedelta(minutes=5)
        )

        count, uuids = CheckoutIntent.mark_stalled_fulfillment_intents(stalled_threshold_seconds=180)

        self.assertEqual(count, 1)
        self.assertEqual(len(uuids), 1)
        self.assertIn(str(intent.pk), uuids)

        intent.refresh_from_db()
        self.assertEqual(intent.state, CheckoutIntentState.ERRORED_FULFILLMENT_STALLED)
        self.assertIsNotNone(intent.last_provisioning_error)

    def test_mark_stalled_fulfillment_intents_ignores_non_paid_states(self):
        """Test that only PAID intents are marked as stalled."""
        created_intent = self._create_intent(CheckoutIntentState.CREATED, '-created')
        fulfilled_intent = self._create_intent(CheckoutIntentState.FULFILLED, '-fulfilled', user=UserFactory())
        paid_intent = self._create_intent(CheckoutIntentState.PAID, '-paid', user=UserFactory())

        CheckoutIntent.objects.filter(
            pk__in=[created_intent.pk, fulfilled_intent.pk, paid_intent.pk]
        ).update(modified=timezone.now() - timedelta(minutes=5))

        count, uuids = CheckoutIntent.mark_stalled_fulfillment_intents(stalled_threshold_seconds=180)

        self.assertEqual(count, 1)
        self.assertIn(str(paid_intent.pk), uuids)

        for intent, expected_state in [
            (created_intent, CheckoutIntentState.CREATED),
            (fulfilled_intent, CheckoutIntentState.FULFILLED),
            (paid_intent, CheckoutIntentState.ERRORED_FULFILLMENT_STALLED)
        ]:
            intent.refresh_from_db()
            self.assertEqual(intent.state, expected_state)


class TestSelfServiceSubscriptionRenewal(TestCase):
    """
    Tests for the SelfServiceSubscriptionRenewal model.
    """

    def setUp(self):
        self.user = UserFactory()
        self.checkout_intent = CheckoutIntent.create_intent(
            user=self.user,
            slug='test-enterprise',
            name='Test Enterprise',
            quantity=10
        )

    def tearDown(self):
        """Clean up test data."""
        SelfServiceSubscriptionRenewal.objects.all().delete()
        CheckoutIntent.objects.all().delete()

    def test_self_service_subscription_renewal_mark_as_processed(self):
        """Test that mark_as_processed sets processed_at timestamp."""
        # Create StripeEventData first
        event_data = StripeEventData.objects.create(
            event_id='evt_test_123',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'test': 'data'}
        )

        # Create renewal record
        expected_renewal_id = 123
        renewal = SelfServiceSubscriptionRenewal.objects.create(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id,
            stripe_subscription_id='sub_test_123',
            stripe_event_data=event_data
        )

        # Verify initial state
        self.assertIsNone(renewal.processed_at)

        # Mark as processed
        before_processing = timezone.now()
        renewal.mark_as_processed()
        after_processing = timezone.now()

        # Verify processed_at was set
        self.assertIsNotNone(renewal.processed_at)
        self.assertGreaterEqual(renewal.processed_at, before_processing)
        self.assertLessEqual(renewal.processed_at, after_processing)

    def test_self_service_subscription_renewal_mark_as_processed_multiple_times(self):
        """Test that calling mark_as_processed multiple times updates the timestamp each time."""
        # Create StripeEventData first
        event_data = StripeEventData.objects.create(
            event_id='evt_test_456',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'test': 'data'}
        )

        expected_renewal_id = 456
        renewal = SelfServiceSubscriptionRenewal.objects.create(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id,
            stripe_subscription_id='sub_test_456',
            stripe_event_data=event_data
        )

        # Mark as processed first time
        renewal.mark_as_processed()
        first_processed_at = renewal.processed_at
        self.assertIsNotNone(first_processed_at)

        # Wait a small amount to ensure timestamp difference
        time.sleep(0.001)

        # Mark as processed second time
        renewal.mark_as_processed()
        second_processed_at = renewal.processed_at

        # Verify the timestamp was updated (current implementation behavior)
        self.assertIsNotNone(second_processed_at)
        self.assertGreaterEqual(second_processed_at, first_processed_at)

    def test_self_service_subscription_renewal_relationships(self):
        """Test that foreign key relationships work correctly."""
        # Create StripeEventData
        event_data = StripeEventDataFactory(
            checkout_intent=self.checkout_intent,
            event_type='customer.subscription.updated',
        )
        summary = event_data.summary

        # Create renewal record with relationships
        expected_renewal_id = 789
        renewal = SelfServiceSubscriptionRenewal.objects.create(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id,
            stripe_subscription_id=summary.stripe_subscription_id,
            stripe_event_data=event_data,
        )

        # Test forward relationships
        self.assertEqual(renewal.checkout_intent, self.checkout_intent)
        self.assertEqual(renewal.stripe_event_data, summary.stripe_event_data)

        # Test reverse relationship from checkout_intent
        related_renewals = self.checkout_intent.renewals.all()
        self.assertIn(renewal, related_renewals)

        # Test reverse relationship from event_data
        related_renewals_from_event = SelfServiceSubscriptionRenewal.objects.filter(
            stripe_event_data=event_data,
        )
        self.assertIn(renewal, related_renewals_from_event)

        # Test querying by related fields
        renewals_by_intent = SelfServiceSubscriptionRenewal.objects.filter(
            checkout_intent__enterprise_name='Test Enterprise'
        )
        self.assertIn(renewal, renewals_by_intent)

        renewals_by_event = SelfServiceSubscriptionRenewal.objects.filter(
            stripe_event_data__event_type='customer.subscription.updated'
        )
        self.assertIn(renewal, renewals_by_event)
