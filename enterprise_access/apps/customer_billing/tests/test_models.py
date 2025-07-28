"""
Tests for the ``enterprise_access.customer_billing.models`` module.
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.constants import CheckoutIntentState
from enterprise_access.apps.customer_billing.models import CheckoutIntent, EnterpriseSlugReservation
from enterprise_access.apps.provisioning.tests.factories import ProvisionNewCustomerWorkflowFactory

User = get_user_model()


class TestEnterpriseSlugReservationModel(TestCase):
    """
    Tests for the EnterpriseSlugReservation model methods.
    """

    def setUp(self):
        self.user1 = UserFactory()
        self.user2 = UserFactory()

    def tearDown(self):
        EnterpriseSlugReservation.objects.all().delete()

    def test_reserve_slug_success(self):
        """
        Test successful slug reservation.
        """
        reservation = EnterpriseSlugReservation.reserve_slug(self.user1, 'test-slug')

        self.assertEqual(reservation.user, self.user1)
        self.assertEqual(reservation.slug, 'test-slug')
        self.assertFalse(reservation.is_expired())
        self.assertIsNone(reservation.stripe_checkout_session_id)

    def test_reserve_slug_conflict(self):
        """
        Test that reserving an already reserved slug fails.
        """
        # User1 reserves a slug
        EnterpriseSlugReservation.reserve_slug(self.user1, 'conflicting-slug')

        # User2 tries to reserve the same slug
        with self.assertRaises(ValueError) as cm:
            EnterpriseSlugReservation.reserve_slug(self.user2, 'conflicting-slug')

        self.assertIn('already reserved', str(cm.exception))

    def test_reserve_slug_replaces_existing(self):
        """
        Test that user can replace their own reservation.
        """
        # User reserves first slug
        first_reservation = EnterpriseSlugReservation.reserve_slug(self.user1, 'first-slug')

        # Same user reserves different slug
        second_reservation = EnterpriseSlugReservation.reserve_slug(self.user1, 'second-slug')

        # Should be the same object but with updated slug
        self.assertEqual(first_reservation.id, second_reservation.id)
        self.assertEqual(second_reservation.slug, 'second-slug')

        # Should only have one reservation for this user
        self.assertEqual(EnterpriseSlugReservation.objects.filter(user=self.user1).count(), 1)

    def test_is_slug_available(self):
        """
        Test slug availability checking.
        """
        # Initially available
        self.assertTrue(EnterpriseSlugReservation.is_slug_available('test-slug'))

        # Reserve it
        EnterpriseSlugReservation.reserve_slug(self.user1, 'test-slug')

        # Not available for others
        self.assertFalse(EnterpriseSlugReservation.is_slug_available('test-slug'))

        # But available for the owner
        self.assertTrue(EnterpriseSlugReservation.is_slug_available('test-slug', exclude_user=self.user1))

    def test_cleanup_expired(self):
        """
        Test cleanup of expired reservations.
        """
        # Create active reservation
        # We have to reserve this first, because reserve_slug() actually
        # cleans up expired reservations before doing anything else.
        active_reservation = EnterpriseSlugReservation.reserve_slug(self.user2, 'active-slug')

        # Create expired reservation
        expired_time = timezone.now() - timedelta(minutes=5)
        expired_reservation = EnterpriseSlugReservation.objects.create(
            user=self.user1,
            slug='expired-slug',
            expires_at=expired_time
        )

        # Cleanup expired
        deleted_count = EnterpriseSlugReservation.cleanup_expired()

        self.assertEqual(deleted_count, 1)
        self.assertFalse(EnterpriseSlugReservation.objects.filter(id=expired_reservation.id).exists())
        self.assertTrue(EnterpriseSlugReservation.objects.filter(id=active_reservation.id).exists())

    def test_release_reservation_by_user(self):
        """
        Test releasing reservation by user.
        """
        reservation = EnterpriseSlugReservation.reserve_slug(self.user1, 'test-slug')

        # Release by user
        released = EnterpriseSlugReservation.release_reservation(user=self.user1)

        self.assertTrue(released)
        self.assertFalse(EnterpriseSlugReservation.objects.filter(id=reservation.id).exists())

    def test_release_reservation_by_slug(self):
        """
        Test releasing reservation by slug.
        """
        reservation = EnterpriseSlugReservation.reserve_slug(self.user1, 'test-slug')

        # Release by slug
        released = EnterpriseSlugReservation.release_reservation(slug='test-slug')

        self.assertTrue(released)
        self.assertFalse(EnterpriseSlugReservation.objects.filter(id=reservation.id).exists())

    def test_release_reservation_by_stripe_session(self):
        """
        Test releasing reservation by Stripe session ID.
        """
        reservation = EnterpriseSlugReservation.reserve_slug(self.user1, 'test-slug')
        reservation.update_stripe_session_id('cs_test_123')

        # Release by Stripe session
        released = EnterpriseSlugReservation.release_reservation(stripe_session_id='cs_test_123')

        self.assertTrue(released)
        self.assertFalse(EnterpriseSlugReservation.objects.filter(id=reservation.id).exists())

    def test_update_stripe_session_id(self):
        """
        Test updating Stripe session ID.
        """
        reservation = EnterpriseSlugReservation.reserve_slug(self.user1, 'test-slug')
        original_modified = reservation.modified

        # Update session ID
        reservation.update_stripe_session_id('cs_test_456')

        reservation.refresh_from_db()
        self.assertEqual(reservation.stripe_checkout_session_id, 'cs_test_456')
        self.assertGreater(reservation.modified, original_modified)

    @override_settings(SLUG_RESERVATION_DURATION_MINUTES=60)
    def test_custom_reservation_duration(self):
        """
        Test that custom reservation duration is respected.
        """
        reservation = EnterpriseSlugReservation.reserve_slug(self.user1, 'test-slug')

        # Should expire in 60 minutes based on settings
        expected_expiry = timezone.now() + timedelta(minutes=60)
        time_diff = abs((reservation.expires_at - expected_expiry).total_seconds())

        # Allow 5 second tolerance for test execution time
        self.assertLess(time_diff, 5)


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
            expires_at=timezone.now() - timedelta(minutes=5)
        )

        # Intent 2: Not expired time - should not be updated
        active_intent = CheckoutIntent.objects.create(
            user=UserFactory(),
            enterprise_name="Active Enterprise",
            enterprise_slug="active-enterprise",
            state=CheckoutIntentState.CREATED,
            quantity=15,
            expires_at=timezone.now() + timedelta(minutes=30)
        )

        # Intent 3: Expired time but already in PAID state - should not be updated
        paid_intent = CheckoutIntent.objects.create(
            user=UserFactory(),
            enterprise_name="Paid Enterprise",
            enterprise_slug="paid-enterprise",
            state=CheckoutIntentState.PAID,
            quantity=20,
            expires_at=timezone.now() - timedelta(minutes=10)
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
        intent = CheckoutIntent.create_intent(
            user=self.user1,
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        self.assertEqual(intent.user, self.user1)
        self.assertEqual(intent.enterprise_slug, self.basic_data['enterprise_slug'])
        self.assertEqual(intent.enterprise_name, self.basic_data['enterprise_name'])
        self.assertEqual(intent.quantity, self.basic_data['quantity'])
        self.assertEqual(intent.state, CheckoutIntentState.CREATED)
        self.assertIsNone(intent.workflow)
        self.assertFalse(intent.is_expired())

    def test_create_intent_name_slug_conflict(self):
        """Test that creating an intent with conflicting slug/name fails."""
        # First user reserves slug and name
        CheckoutIntent.create_intent(
            user=self.user1,
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Second user tries to use same slug
        with self.assertRaises(ValueError):
            CheckoutIntent.create_intent(
                user=self.user2,
                slug=self.basic_data['enterprise_slug'],
                name='Different Enterprise',
                quantity=self.basic_data['quantity']
            )

        # Second user tries to use same name
        with self.assertRaises(ValueError):
            CheckoutIntent.create_intent(
                user=self.user2,
                slug='different-slug',
                name=self.basic_data['enterprise_name'],
                quantity=self.basic_data['quantity']
            )

    def test_create_intent_updates_existing(self):
        """Test that creating an intent updates the user's existing one."""
        # Create first intent
        first_intent = CheckoutIntent.create_intent(
            user=self.user1,
            slug='first-slug',
            name='First Enterprise',
            quantity=5
        )

        # Create second intent for same user
        second_intent = CheckoutIntent.create_intent(
            user=self.user1,
            slug='second-slug',
            name='Second Enterprise',
            quantity=10
        )

        # Should be the same object but with updated fields
        self.assertEqual(first_intent.id, second_intent.id)
        self.assertEqual(second_intent.enterprise_slug, 'second-slug')
        self.assertEqual(second_intent.enterprise_name, 'Second Enterprise')
        self.assertEqual(second_intent.quantity, 10)

        # Should still only have one intent for this user
        self.assertEqual(CheckoutIntent.objects.filter(user=self.user1).count(), 1)

    def test_create_intent_with_existing_failed_intent(self):
        """
        Test that trying to update an intent that's in a failure state raises an exception.
        """
        # First create a normal intent
        intent = CheckoutIntent.create_intent(
            user=self.user1,
            slug='original-slug',
            name='Original Enterprise',
            quantity=5
        )

        # Now put it in a failure state
        intent.mark_checkout_error('Payment processing failed')
        self.assertIn(intent.state, CheckoutIntent.FAILURE_STATES)

        # Trying to create a new intent for the same user should fail
        with self.assertRaises(ValueError) as context:
            CheckoutIntent.create_intent(
                user=self.user1,
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
        self.assertEqual(intent.state, CheckoutIntentState.ERRORED_STRIPE_CHECKOUT)

    def test_state_transitions_happy_path(self):
        """Test the state transitions for the happy path."""
        intent = CheckoutIntent.create_intent(
            user=self.user1,
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # CREATED → PAID
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
        intent = CheckoutIntent.create_intent(
            user=self.user1,
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # CREATED → ERRORED_STRIPE_CHECKOUT
        intent.mark_checkout_error('Payment failed: card declined')
        self.assertEqual(intent.state, CheckoutIntentState.ERRORED_STRIPE_CHECKOUT)
        self.assertEqual(intent.last_checkout_error, 'Payment failed: card declined')

        # Reset for testing PAID → ERRORED_PROVISIONING
        intent = CheckoutIntent.create_intent(
            user=self.user2,
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
            user=self.user1,
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Mark as errored
        intent.mark_checkout_error('Payment failed')

        # Should not be able to transition from error to paid
        with self.assertRaises(ValueError):
            intent.mark_as_paid()

        # Create a new intent
        intent2 = CheckoutIntent.create_intent(
            user=self.user2,
            slug='another-slug',
            name='Another Enterprise',
            quantity=7
        )

        # Should not be able to go from CREATED to FULFILLED (skipping PAID)
        with self.assertRaises(ValueError):
            intent2.mark_as_fulfilled()

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
            expires_at=past_time
        )

        self.assertTrue(intent.is_expired())

        # Test the cleanup method
        with mock.patch.object(CheckoutIntent, 'cleanup_expired') as mock_cleanup:
            CheckoutIntent.cleanup_expired.return_value = 1
            CheckoutIntent.create_intent(
                user=self.user2,
                slug='new-slug',
                name='New Enterprise',
                quantity=10
            )
            mock_cleanup.assert_called_once()

    def test_admin_portal_url(self):
        """Test the admin_portal_url property."""
        intent = CheckoutIntent.create_intent(
            user=self.user1,
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
        with self.settings(ENTERPRISE_ADMIN_PORTAL_URL='https://admin.example.com/'):
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
            user=self.user1,
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
            expires_at=timezone.now() + timedelta(hours=2)  # Future date
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
            user=self.user1,
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
            user=self.user1,
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
            user=self.user1,
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
