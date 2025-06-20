"""
Tests for the ``enterprise_access.customer_billing.models`` module.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.models import EnterpriseSlugReservation

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
