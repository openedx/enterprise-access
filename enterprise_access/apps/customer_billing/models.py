"""
Models for customer billing app.
"""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.validators import validate_slug
from django.db import models, transaction
from django.utils import timezone
from django_extensions.db.models import TimeStampedModel
from simple_history.models import HistoricalRecords

from .constants import SLUG_RESERVATION_DURATION_MINUTES

User = get_user_model()


class EnterpriseSlugReservation(TimeStampedModel):
    """
    Temporary reservation of enterprise customer slugs to prevent race conditions
    during the checkout-to-provisioning flow.

    Only one active reservation per user is allowed at a time.

    .. no_pii: This model has no PII
    """
    class Meta:
        verbose_name = "Enterprise Slug Reservation"
        verbose_name_plural = "Enterprise Slug Reservations"

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='enterprise_slug_reservation'
    )
    slug = models.SlugField(
        max_length=255,
        unique=True,
        validators=[validate_slug],
        help_text="Reserved enterprise customer slug"
    )
    expires_at = models.DateTimeField(
        db_index=True,
        help_text="Reservation expiration timestamp"
    )
    stripe_checkout_session_id = models.CharField(
        db_index=True,
        max_length=255,
        blank=True,
        null=True,
        help_text="Associated Stripe checkout session ID"
    )
    history = HistoricalRecords()

    def __str__(self):
        return f"{self.user.email}, {self.slug} (expires {self.expires_at})"

    def is_expired(self):
        """Check if this reservation has expired."""
        if self.expires_at:
            return timezone.now() > self.expires_at
        return None

    @classmethod
    def get_reservation_duration(cls):
        """Get reservation duration from settings or default to our stored constant."""
        return getattr(settings, 'SLUG_RESERVATION_DURATION_MINUTES', SLUG_RESERVATION_DURATION_MINUTES)

    @classmethod
    @transaction.atomic
    def reserve_slug(cls, user, slug):
        """
        Reserve a slug for the given user.

        If the user already has a reservation, it will be replaced with
        the given ``slug`` argument (implicitly releasing the reservation on the existing slug).
        Cleans up *all* expired reservations before attempting to reserve.

        Args:
            user: Django User instance
            slug: Slug to reserve

        Returns:
            EnterpriseSlugReservation instance

        Raises:
            ValueError: If slug is already reserved by another user
        """
        # Clean up expired reservations first
        cls.cleanup_expired()

        # Check if slug is available (excluding user's own reservation)
        if not cls.is_slug_available(slug, exclude_user=user):
            raise ValueError(f"Slug '{slug}' is already reserved by another user")

        # Calculate expiration time
        expires_at = timezone.now() + timedelta(minutes=cls.get_reservation_duration())

        # Create or update user's reservation
        reservation, _ = cls.objects.update_or_create(
            user=user,
            defaults={
                'slug': slug,
                'expires_at': expires_at,
                'stripe_checkout_session_id': None,  # Reset session ID
            }
        )

        return reservation

    @classmethod
    def is_slug_available(cls, slug, exclude_user=None):
        """
        Check if a slug is available for reservation.

        Args:
            slug: Slug to check
            exclude_user: User to exclude from check (for their own reservation)

        Returns:
            bool: True if slug is available
        """
        query = cls.objects.filter(slug=slug, expires_at__gte=timezone.now())
        if exclude_user:
            query = query.exclude(user=exclude_user)

        return not query.exists()

    @classmethod
    def cleanup_expired(cls):
        """Remove all expired reservations."""
        expired_count = cls.objects.filter(expires_at__lte=timezone.now()).delete()[0]
        return expired_count

    @classmethod
    def release_reservation(cls, user=None, slug=None, stripe_session_id=None):
        """
        Release a reservation based on user, slug, or Stripe session ID.
        We'll eventually integrate this into the webhook handler that triggers a provisioning workflow.

        Args:
            user: User whose reservation to release
            slug: Slug reservation to release
            stripe_session_id: Stripe session ID to match

        Returns:
            bool: True if a reservation was released
        """
        query = cls.objects.all()

        if user:
            query = query.filter(user=user)
        if slug:
            query = query.filter(slug=slug)
        if stripe_session_id:
            query = query.filter(stripe_checkout_session_id=stripe_session_id)

        deleted_count = query.delete()[0]
        return deleted_count > 0

    def update_stripe_session_id(self, session_id):
        """Update the associated Stripe checkout session ID."""
        self.stripe_checkout_session_id = session_id
        self.save(update_fields=['stripe_checkout_session_id', 'modified'])
