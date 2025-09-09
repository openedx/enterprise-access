"""
Models for customer billing app.
"""
import logging
from datetime import timedelta
from typing import Self

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import validate_slug
from django.db import models, transaction
from django.utils import timezone
from django_countries.fields import CountryField
from django_extensions.db.models import TimeStampedModel
from simple_history.models import HistoricalRecords
from simple_history.utils import bulk_update_with_history

from enterprise_access.apps.customer_billing.constants import ALLOWED_CHECKOUT_INTENT_STATE_TRANSITIONS
from enterprise_access.apps.provisioning.models import ProvisionNewCustomerWorkflow

from .constants import INTENT_RESERVATION_DURATION_MINUTES, CheckoutIntentState

logger = logging.getLogger(__name__)
User = get_user_model()


class CheckoutIntent(TimeStampedModel):
    """
    Tracks the complete lifecycle of a self-service checkout process:

    1. Reserves enterprise slugs/names during checkout
    2. Stores minimal purchase data needed for UI rendering
    3. Tracks the checkout and provisioning process state
    4. Records errors that occur during the flow

    The model follows a state machine pattern with these key transitions:
    - CREATED → PAID → FULFILLED (happy path)
    - CREATED → ERRORED_STRIPE_CHECKOUT (payment failures)
    - PAID → ERRORED_PROVISIONING (provisioning failures)
    - CREATED → EXPIRED (timeout)

    Example usage:
    1. Create initial intent
    intent = CheckoutIntent.create_intent(user, slug, name, quantity)

    2. Update with Stripe session
    intent.update_stripe_session_id(session_id)

    3. Mark as paid after checkout completion
    intent.mark_as_paid(stripe_session_id)

    4. Mark as fulfilled after provisioning
    intent.mark_as_fulfilled(workflow)

    .. no_pii: This model has no PII
    """
    class Meta:
        verbose_name = "Enterprise Checkout Intent"
        verbose_name_plural = "Enterprise Checkout Intents"
        indexes = [
            models.Index(fields=['state']),
            models.Index(fields=['enterprise_slug']),
            models.Index(fields=['enterprise_name']),
            models.Index(fields=['expires_at']),
            models.Index(fields=['stripe_checkout_session_id']),
        ]

    class StateChoices(models.TextChoices):
        """
        Allowed choices for the state field
        """
        CREATED = (CheckoutIntentState.CREATED, 'Created')
        PAID = (CheckoutIntentState.PAID, 'Paid')
        FULFILLED = (CheckoutIntentState.FULFILLED, 'Fulfilled')
        ERRORED_STRIPE_CHECKOUT = (CheckoutIntentState.ERRORED_STRIPE_CHECKOUT, 'Errored (Stripe Checkout)')
        ERRORED_PROVISIONING = (CheckoutIntentState.ERRORED_PROVISIONING, 'Errored (Provisioning)')
        EXPIRED = (CheckoutIntentState.EXPIRED, 'Expired')

    SUCCESS_STATES = {CheckoutIntentState.PAID, CheckoutIntentState.FULFILLED}
    FAILURE_STATES = {CheckoutIntentState.ERRORED_STRIPE_CHECKOUT, CheckoutIntentState.ERRORED_PROVISIONING}
    NON_EXPIRED_STATES = {
        CheckoutIntentState.CREATED,
        CheckoutIntentState.PAID,
        CheckoutIntentState.FULFILLED,
        CheckoutIntentState.ERRORED_STRIPE_CHECKOUT,
        CheckoutIntentState.ERRORED_PROVISIONING,
    }

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
    )
    state = models.CharField(
        choices=StateChoices.choices,
        default=StateChoices.CREATED,
        max_length=255,
    )
    enterprise_name = models.CharField(
        max_length=255,
        help_text="Checkout intent enterprise customer name",
    )
    enterprise_slug = models.SlugField(
        max_length=255,
        validators=[validate_slug],
        help_text="Checkout intent enterprise customer slug"
    )
    expires_at = models.DateTimeField(
        db_index=True,
        help_text="Checkout intent expiration timestamp"
    )
    stripe_checkout_session_id = models.CharField(
        db_index=True,
        max_length=255,
        blank=True,
        null=True,
        help_text="Associated Stripe checkout session ID"
    )
    quantity = models.PositiveIntegerField(
        help_text="How many licenses to create.",
    )
    country = CountryField(
        null=True,
        help_text="The customer's country",
        blank_label="(select country)",
    )
    last_checkout_error = models.TextField(blank=True, null=True)
    last_provisioning_error = models.TextField(blank=True, null=True)
    workflow = models.OneToOneField(
        ProvisionNewCustomerWorkflow,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )

    history = HistoricalRecords()

    def __str__(self):
        return (
            "<CheckoutIntent "
            f"id={self.id}, "
            f"email={self.user.email}, "
            f"enterprise_slug={self.enterprise_slug}, "
            f"enterprise_name={self.enterprise_name}, "
            f"state={self.state}, "
            f"expires_at={self.expires_at}>"
        )

    @classmethod
    def is_valid_state_transition(
        cls,
        current_state: CheckoutIntentState,
        new_state: CheckoutIntentState,
    ) -> bool:
        """
        Validate if the state transition is allowed.

        Args:
            current_state: Current state of the CheckoutIntent
            new_state: Proposed new state

        Returns:
            bool: True if transition is allowed, False otherwise
        """
        if current_state == new_state:
            return True
        allowed_transitions = ALLOWED_CHECKOUT_INTENT_STATE_TRANSITIONS.get(current_state, [])
        return new_state in allowed_transitions

    def mark_as_paid(self, stripe_session_id=None):
        """Mark the intent as paid after successful Stripe checkout."""
        if not self.is_valid_state_transition(CheckoutIntentState(self.state), CheckoutIntentState.PAID):
            raise ValueError(f"Cannot transition from {self.state} to {CheckoutIntentState.PAID}.")

        if stripe_session_id:
            if self.state == CheckoutIntentState.PAID and stripe_session_id != self.stripe_checkout_session_id:
                raise ValueError("Cannot transition from PAID to PAID with a different stripe_checkout_session_id")

        self.state = CheckoutIntentState.PAID
        if stripe_session_id:
            self.stripe_checkout_session_id = stripe_session_id
        self.save(update_fields=['state', 'stripe_checkout_session_id', 'modified'])
        logger.info(f'CheckoutIntent {self} marked as {CheckoutIntentState.PAID}.')
        return self

    def mark_as_fulfilled(self, workflow=None):
        """Mark the intent as fulfilled after successful provisioning."""
        if not self.is_valid_state_transition(CheckoutIntentState(self.state), CheckoutIntentState.FULFILLED):
            raise ValueError(f"Cannot transition from {self.state} to {CheckoutIntentState.FULFILLED}.")

        self.state = CheckoutIntentState.FULFILLED
        if workflow:
            self.workflow = workflow
        self.save(update_fields=['state', 'workflow', 'modified'])
        logger.info(f'CheckoutIntent {self} marked as {CheckoutIntentState.FULFILLED}.')
        return self

    def mark_checkout_error(self, error_message):
        """Record a checkout error."""
        if not self.is_valid_state_transition(
            CheckoutIntentState(self.state),
            CheckoutIntentState.ERRORED_STRIPE_CHECKOUT,
        ):
            raise ValueError(f"Cannot transition from {self.state} to {CheckoutIntentState.ERRORED_STRIPE_CHECKOUT}.")

        self.state = CheckoutIntentState.ERRORED_STRIPE_CHECKOUT
        self.last_checkout_error = error_message
        self.save(update_fields=['state', 'last_checkout_error', 'modified'])
        logger.info(f'CheckoutIntent {self} marked as {CheckoutIntentState.ERRORED_STRIPE_CHECKOUT}.')
        return self

    def mark_provisioning_error(self, error_message, workflow=None):
        """Record a provisioning error."""
        if not self.is_valid_state_transition(
            CheckoutIntentState(self.state),
            CheckoutIntentState.ERRORED_PROVISIONING,
        ):
            raise ValueError(f"Cannot transition from {self.state} to {CheckoutIntentState.ERRORED_PROVISIONING}.")

        self.state = CheckoutIntentState.ERRORED_PROVISIONING
        self.last_provisioning_error = error_message
        if workflow:
            self.workflow = workflow
        self.save(update_fields=['state', 'last_provisioning_error', 'workflow', 'modified'])
        logger.info(f'CheckoutIntent {self} marked as {CheckoutIntentState.ERRORED_PROVISIONING}.')
        return self

    @property
    def admin_portal_url(self):
        if self.state == CheckoutIntentState.FULFILLED:
            return f"{settings.ENTERPRISE_ADMIN_PORTAL_URL}{self.enterprise_slug}"
        return None

    @classmethod
    def cleanup_expired(cls):
        """Update expired intents."""
        expired_intent_records = list(cls.objects.filter(
            state=CheckoutIntentState.CREATED,
            expires_at__lte=timezone.now(),
        ))
        for expired_record in expired_intent_records:
            expired_record.state = CheckoutIntentState.EXPIRED

        return bulk_update_with_history(
            expired_intent_records, cls, ['state', 'modified'], batch_size=100,
        )

    def is_expired(self):
        """Check if this checkout intent has expired."""
        if self.expires_at:
            return (self.state == CheckoutIntentState.CREATED) and (timezone.now() > self.expires_at)
        return None

    @staticmethod
    def get_reservation_duration():
        """
        Determine how long any intent is reserved for, based on settings.
        """
        return getattr(settings, 'INTENT_RESERVATION_DURATION_MINUTES', INTENT_RESERVATION_DURATION_MINUTES)

    @classmethod
    def filter_by_name_and_slug(cls, slug=None, name=None):
        """
        Finds ``CheckoutIntent`` instances with the given name or the given slug.
        At least one argument must be provided
        """
        if not (slug or name):
            raise ValueError("One of slug or name must be provided")

        query = models.Q()
        if slug:
            query |= models.Q(enterprise_slug=slug)
        if name:
            query |= models.Q(enterprise_name=name)

        return cls.objects.filter(query)

    def clean(self):
        """
        Validate the CheckoutIntent to prevent conflicts with existing non-expired intents.

        This method enforces several uniqueness constraints:
        1. A user can only have one active checkout intent at a time
        2. Enterprise name must be unique across all non-expired intents
        3. Enterprise slug must be unique across all non-expired intents

        The validation considers an intent "active" if it's in any of the NON_EXPIRED_STATES.
        For existing instances being updated, we exclude the current instance from conflict checks.

        Raises:
          ValidationError: If any uniqueness constraint is violated. The error dictionary
            will contain field-specific error messages.
        """
        super().clean()

        # Check if this is a new instance
        if not self.pk:
            # Check if user already has a non-expired intent
            existing_intent = CheckoutIntent.objects.filter(
                user=self.user,
                state__in=self.NON_EXPIRED_STATES
            ).first()

            if existing_intent:
                raise ValidationError({
                    'user': f"User {self.user.email} already has an active checkout intent ({existing_intent})."
                })

        conflicts = CheckoutIntent.filter_by_name_and_slug(
            name=self.enterprise_name,
            slug=self.enterprise_slug,
        ).filter(
            state__in=self.NON_EXPIRED_STATES,
        ).exclude(
            id=self.pk if self.pk else None,  # Exclude current record for updates
        )

        # Handle name conflicts
        name_conflicts = [c for c in conflicts if c.enterprise_name == self.enterprise_name]
        if name_conflicts:
            conflict = name_conflicts[0]
            raise ValidationError({
                'enterprise_name': (
                    f"This enterprise name is already reserved by {conflict.user.email} (intent is {conflict})"
                )
            })

        # Handle slug conflicts
        slug_conflicts = [c for c in conflicts if c.enterprise_slug == self.enterprise_slug]
        if slug_conflicts:
            conflict = slug_conflicts[0]
            raise ValidationError({
                'enterprise_slug': f"This slug is already reserved by {conflict.user.email} (intent is {conflict})"
            })

    @classmethod
    def can_reserve(cls, slug=None, name=None, exclude_user=None):
        """
        Check if an enterprise slug and name is available for reservation.

        Args:
            slug: Enterprise slug to check
            name: Enterprise name to check
            exclude_user: User to exclude from check (for their own reservation)

        Returns:
            bool: True if slug and name are available
        """
        queryset = cls.filter_by_name_and_slug(slug=slug, name=name)
        queryset = queryset.filter(state__in=cls.NON_EXPIRED_STATES)

        if exclude_user:
            queryset = queryset.exclude(user=exclude_user)

        return not queryset.exists()

    @classmethod
    def create_intent(
        cls,
        user: AbstractUser,
        slug: str,
        name: str,
        quantity: int,
        country: str | None = None
    ) -> Self:
        """
        Create or update a checkout intent for a user with the given enterprise details.

        This method handles the reservation of enterprise slugs and names, ensuring they're
        not already in use by another checkout flow. If the user already has an intent:
        - If it's in a success state (PAID, FULFILLED), the existing intent is returned unchanged
        - If it's in a failure state (ERRORED_*), a ValueError is raised
        - If it's in CREATED state, it's updated with the new details

        The method also cleans up any expired intents first to free up reserved slugs/names.

        Args:
            user (User): The Django User who will own this intent
            slug (str): The enterprise slug to reserve
            name (str): The enterprise name to reserve
            quantity (int): Number of licenses to create

        Returns:
            CheckoutIntent: The created or updated intent object

        Raises:
            ValueError: If the slug or name is already reserved by another user
            ValueError: If the user already has an intent that failed

        Note:
            This operation is atomic - either the entire reservation succeeds or fails.
        """
        # Wrap entire function body inside an atomic transaction. The decorator version of
        # this feature (@transaction.atomic) is normally preferable, but breaks type hints
        # because it hasn't been updated to use functools.wraps yet.
        with transaction.atomic():
            cls.cleanup_expired()

            if not cls.can_reserve(slug, name, exclude_user=user):
                raise ValueError(f"Slug '{slug}' or name '{name}' is already reserved")

            existing_intent = cls.objects.filter(user=user).first()

            expires_at = timezone.now() + timedelta(minutes=cls.get_reservation_duration())

            if existing_intent:
                if existing_intent.state in cls.SUCCESS_STATES:
                    return existing_intent

                if existing_intent.state in cls.FAILURE_STATES:
                    raise ValueError("Failed checkout record already exists")

                # Update the existing CREATED or EXPIRED intent
                existing_intent.state = CheckoutIntentState.CREATED
                existing_intent.enterprise_slug = slug
                existing_intent.enterprise_name = name
                existing_intent.quantity = quantity
                existing_intent.expires_at = expires_at
                existing_intent.country = country
                existing_intent.save()
                return existing_intent

            return cls.objects.create(
                user=user,
                state=CheckoutIntentState.CREATED,
                enterprise_slug=slug,
                enterprise_name=name,
                quantity=quantity,
                expires_at=expires_at,
                country=country,
            )

    @classmethod
    def for_user(cls, user):
        """
        Fetch the CheckoutIntent for a user.

        Returns:
          CheckoutIntent: The user's checkout intent, or None if not found
        """
        if not user or not user.is_authenticated:
            return None
        return cls.objects.filter(user=user).first()

    def update_stripe_session_id(self, session_id):
        """Update the associated Stripe checkout session ID."""
        self.stripe_checkout_session_id = session_id
        self.save(update_fields=['stripe_checkout_session_id', 'modified'])
