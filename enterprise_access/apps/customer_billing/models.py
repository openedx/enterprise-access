"""
Models for customer billing app.
"""
import logging
from datetime import timedelta
from decimal import Decimal
from typing import Self
from uuid import uuid4

import stripe
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import validate_slug
from django.db import models, transaction
from django.utils import timezone
from django_countries.fields import CountryField
from django_extensions.db.models import TimeStampedModel
from simple_history.models import HistoricalRecords
from simple_history.utils import bulk_update_with_history

from enterprise_access.apps.customer_billing import stripe_api
from enterprise_access.apps.customer_billing.constants import ALLOWED_CHECKOUT_INTENT_STATE_TRANSITIONS

from .constants import INTENT_RESERVATION_DURATION_MINUTES, CheckoutIntentState
from .utils import datetime_from_timestamp

logger = logging.getLogger(__name__)
User = get_user_model()


class FailedCheckoutIntentConflict(Exception):
    pass


class SlugReservationConflict(Exception):
    pass


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

    class StateChoices(models.TextChoices):
        """
        Allowed choices for the state field
        """
        CREATED = (CheckoutIntentState.CREATED, 'Created')
        PAID = (CheckoutIntentState.PAID, 'Paid')
        FULFILLED = (CheckoutIntentState.FULFILLED, 'Fulfilled')
        ERRORED_BACKOFFICE = (
            CheckoutIntentState.ERRORED_BACKOFFICE,
            'Errored (Backoffice) - Salesforce integration failed during checkout intent update'
        )
        ERRORED_FULFILLMENT_STALLED = (
            CheckoutIntentState.ERRORED_FULFILLMENT_STALLED,
            'Errored (Fulfillment Stalled) - Checkout intent stuck in paid state, fulfillment workflow may have failed'
        )
        ERRORED_PROVISIONING = (
            CheckoutIntentState.ERRORED_PROVISIONING,
            'Errored (Provisioning) - Enterprise provisioning workflow failed after payment'
        )
        EXPIRED = (CheckoutIntentState.EXPIRED, 'Expired')

    SUCCESS_STATES = {CheckoutIntentState.PAID, CheckoutIntentState.FULFILLED}
    FAILURE_STATES = {
        CheckoutIntentState.ERRORED_BACKOFFICE,
        CheckoutIntentState.ERRORED_FULFILLMENT_STALLED,
        CheckoutIntentState.ERRORED_PROVISIONING,
    }
    NON_EXPIRED_STATES = {
        CheckoutIntentState.CREATED,
        CheckoutIntentState.PAID,
        CheckoutIntentState.FULFILLED,
        CheckoutIntentState.ERRORED_BACKOFFICE,
        CheckoutIntentState.ERRORED_FULFILLMENT_STALLED,
        CheckoutIntentState.ERRORED_PROVISIONING,
    }

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
    )
    uuid = models.UUIDField(
        unique=True,
        null=False,
        default=uuid4,
        help_text="Unique identifier for this record, can be used for cross-service references",
    )
    state = models.CharField(
        db_index=True,
        choices=StateChoices.choices,
        default=StateChoices.CREATED,
        max_length=255,
    )
    enterprise_name = models.CharField(
        db_index=True,
        null=True,
        blank=True,
        max_length=255,
        help_text="Checkout intent enterprise customer name",
    )
    enterprise_slug = models.SlugField(
        db_index=True,
        null=True,
        blank=True,
        max_length=255,
        validators=[validate_slug],
        help_text="Checkout intent enterprise customer slug"
    )
    enterprise_uuid = models.UUIDField(
        db_index=True,
        null=True,
        blank=True,
        help_text="The uuid of the EnterpriseCustomer, once successfully provisioned",
    )
    stripe_customer_id = models.CharField(
        null=True,
        blank=True,
        help_text="The Stripe Customer identifier associated with this record",
        db_index=True,
        max_length=255,
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
        'provisioning.ProvisionNewCustomerWorkflow',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    terms_metadata = models.JSONField(
        null=True,
        blank=True,
        help_text="Metadata relating to the terms and conditions accepted by the user.",
    )

    history = HistoricalRecords()

    def __str__(self):
        return (
            "<CheckoutIntent "
            f"id={self.id}, "
            f"uuid={self.uuid}, "
            f"email={self.user.email}, "
            f"enterprise_slug={self.enterprise_slug}, "
            f"enterprise_name={self.enterprise_name}, "
            f"state={self.state}, "
            f"expires_at={self.expires_at}>"
        )

    @classmethod
    def FULFILLABLE_STATES(cls) -> list[CheckoutIntentState]:
        return [
            from_state
            for from_state, to_states
            in ALLOWED_CHECKOUT_INTENT_STATE_TRANSITIONS.items()
            if CheckoutIntentState.FULFILLED in to_states
        ]

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

    def mark_as_paid(self, stripe_session_id=None, stripe_customer_id=None, **kwargs):
        """Mark the intent as paid after successful Stripe checkout."""
        if not self.is_valid_state_transition(CheckoutIntentState(self.state), CheckoutIntentState.PAID):
            raise ValueError(f"Cannot transition from {self.state} to {CheckoutIntentState.PAID}.")

        if stripe_session_id:
            if self.state == CheckoutIntentState.PAID and stripe_session_id != self.stripe_checkout_session_id:
                raise ValueError("Cannot transition from PAID to PAID with a different stripe_checkout_session_id")

        if stripe_customer_id:
            if self.state == CheckoutIntentState.PAID and stripe_customer_id != self.stripe_customer_id:
                raise ValueError("Cannot transition from PAID to PAID with a different stripe_customer_id")

        self.state = CheckoutIntentState.PAID
        if stripe_session_id:
            self.stripe_checkout_session_id = stripe_session_id
        if stripe_customer_id:
            self.stripe_customer_id = stripe_customer_id

        self.save(update_fields=['state', 'stripe_checkout_session_id', 'stripe_customer_id', 'modified'])
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

    def mark_backoffice_error(self, error_message):
        """Record a backoffice error (Salesforce integration failure)."""
        if not self.is_valid_state_transition(
            CheckoutIntentState(self.state),
            CheckoutIntentState.ERRORED_BACKOFFICE,
        ):
            raise ValueError(f"Cannot transition from {self.state} to {CheckoutIntentState.ERRORED_BACKOFFICE}.")

        self.state = CheckoutIntentState.ERRORED_BACKOFFICE
        self.last_provisioning_error = error_message
        self.save(update_fields=['state', 'last_provisioning_error', 'modified'])
        logger.info(f'CheckoutIntent {self} marked as {CheckoutIntentState.ERRORED_BACKOFFICE}.')
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
            return f"{settings.ENTERPRISE_ADMIN_PORTAL_URL}/{self.enterprise_slug}"
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

    @classmethod
    def find_stalled_fulfillment_intents(cls, stalled_threshold_seconds=180, do_logging=False):
        """
        Find all CheckoutIntent records stuck in 'paid' state.

        Args:
            stalled_threshold_seconds (int): Number of seconds after which a 'paid'
                CheckoutIntent is considered stalled. Default: 180 (3 minutes)
            do_logging (bool): If True, log information about each stalled intent found.
                Default: False

        Returns:
            tuple: (stalled_intents, list_of_uuids, current_time)
                stalled_intents: List of CheckoutIntent objects that are stalled
                list_of_uuids: List of string UUIDs for the stalled intents
                current_time: The timestamp used for calculations
        """
        threshold_time = timezone.now() - timedelta(seconds=stalled_threshold_seconds)
        current_time = timezone.now()

        # Query stalled intents
        stalled_intents = list(
            cls.objects.filter(
                state=CheckoutIntentState.PAID,
                modified__lte=threshold_time,
            ).order_by('modified')
        )

        if not stalled_intents:
            return [], [], current_time

        updated_uuids = [str(intent.pk) for intent in stalled_intents]

        if do_logging:
            for intent in stalled_intents:
                time_stalled = (current_time - intent.modified).total_seconds()
                logger.info(
                    '[DRY RUN] Would mark CheckoutIntent %s as stalled (stalled for %s seconds)',
                    intent.pk,
                    int(time_stalled),
                )

        return stalled_intents, updated_uuids, current_time

    @classmethod
    def mark_stalled_fulfillment_intents(cls, stalled_threshold_seconds=180):
        """
        Find all CheckoutIntent records stuck in 'paid' state and transition them
        to 'errored_fulfillment_stalled'.

        Args:
            stalled_threshold_seconds (int): Number of seconds after which a 'paid'
                CheckoutIntent is considered stalled. Default: 180 (3 minutes)

        Returns:
            tuple: (updated_count, list_of_updated_uuids)
        """
        stalled_intents, updated_uuids, current_time = cls.find_stalled_fulfillment_intents(
            stalled_threshold_seconds
        )

        if not stalled_intents:
            return 0, []

        # Update each intent and save individually (django-simple-history tracks via post-save signal)
        updated_count = 0
        for intent in stalled_intents:
            time_stalled = (current_time - intent.modified).total_seconds()
            intent.state = CheckoutIntentState.ERRORED_FULFILLMENT_STALLED
            intent.last_provisioning_error = (
                f'Fulfillment stalled for {int(time_stalled)} seconds '
                f'(threshold: {stalled_threshold_seconds}s). '
                f'Last modified: {intent.modified.isoformat()}. '
                f'Provisioning workflow may have failed without proper error handling.'
            )
            logger.warning(
                'Marking CheckoutIntent %s as ERRORED_FULFILLMENT_STALLED. '
                'Stalled for %s seconds.',
                intent.pk,
                int(time_stalled),
            )
            intent.save(update_fields=['state', 'last_provisioning_error', 'modified'])
            updated_count += 1

        return updated_count, updated_uuids

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
        quantity: int,
        slug: str | None = None,
        name: str | None = None,
        country: str | None = None,
        terms_metadata: dict | None = None
    ) -> Self:
        """
        Create or update a checkout intent for a user with the given enterprise details.

        This method handles the reservation of enterprise slugs and names, ensuring they're
        not already in use by another checkout flow. If the user already has an intent:
        - If it's in a success state (PAID, FULFILLED), the existing intent is returned unchanged
        - If it's in a failure state (ERRORED_*), a ValueError is raised
        - If it's in CREATED state, it's updated with the new details (more like PATCH, not PUT).

        The method also cleans up any expired intents first to free up reserved slugs/names.

        Args:
            user (User): The Django User who will own this intent
            quantity (int): Number of licenses to create
            slug (str, Optional): The enterprise slug to reserve
            name (str, Optional): The enterprise name to reserve

        Returns:
            CheckoutIntent: The created or updated intent object

        Raises:
            ValueError: If only one of [slug, name] were given, but not the other.
            SlugReservationConflict: If the slug or name is already reserved by another user
            FailedCheckoutIntentConflict: If the user already has an intent that failed

        Note:
            This operation is atomic - either the entire reservation succeeds or fails.
        """
        # Wrap entire function body inside an atomic transaction. The decorator version of
        # this feature (@transaction.atomic) is normally preferable, but breaks type hints
        # because it hasn't been updated to use functools.wraps yet.
        with transaction.atomic():
            cls.cleanup_expired()

            if bool(slug) != bool(name):
                raise ValueError("slug and name must either both be given or neither be given.")

            existing_intent = cls.objects.filter(user=user).first()

            # If an existing intent has already reached a terminal state, exit fast.
            if existing_intent:
                if existing_intent.state in cls.SUCCESS_STATES:
                    return existing_intent

                if existing_intent.state in cls.FAILURE_STATES:
                    raise FailedCheckoutIntentConflict("Failed checkout record already exists")

            # Establish whether or not a new slug needs to be reserved. This logic is really only an
            # optimization to avoid unnecessary DB lookups to search for reservation conflicts (via
            # can_reserve()) in cases where we know the reservation is not changing.
            #
            #       |    Slug    |  Intent  |      A Slug       | Requested Slug |    Requested Slug
            #  Case | Requested? | Existed? | Already Reserved? | Is Different?  | Needs To Be Reserved
            # ------+------------+----------+-------------------+----------------+---------------------
            #   1   |     no     |    no    |        N/A        |      N/A       |        no
            #   2   |     no     |    yes   |        no         |      N/A       |        no
            #   3   |     no     |    yes   |        yes        |      N/A       |        no
            #   4   |     yes    |    no    |        N/A        |      N/A       |        yes
            #   5   |     yes    |    yes   |        no         |      N/A       |        yes
            #   6   |     yes    |    yes   |        yes        |      no        |        no
            #   7   |     yes    |    yes   |        yes        |      yes       |        yes
            if slug:
                if existing_intent:
                    slug_changing = slug != existing_intent.enterprise_slug
                    name_changing = name != existing_intent.enterprise_name
                    should_reserve_new_slug = slug_changing or name_changing  # Cases 5, 6, 7
                else:
                    should_reserve_new_slug = True  # Case 4
            else:
                should_reserve_new_slug = False  # Cases 1, 2, 3

            # If we are reserving a new slug, then gate this whole view on it not already being reserved.
            if should_reserve_new_slug:
                if not cls.can_reserve(slug, name, exclude_user=user):
                    raise SlugReservationConflict(f"Slug '{slug}' or name '{name}' is already reserved")

            expires_at = timezone.now() + timedelta(minutes=cls.get_reservation_duration())

            # The remaining code essentially performs an update_or_create(), but we're not
            # using update_or_create() because we already have the existing intent and
            # don't need to spend a DB query looking it up again. Also, wouldn't be able
            # to do the terms_metadata merging logic which could come in handy in case
            # this ever gets called multiple times and we have terms on multiple pages.

            if existing_intent:
                # Found an existing CREATED or EXPIRED intent, so update it.

                # Force update certain fields.
                existing_intent.state = CheckoutIntentState.CREATED
                existing_intent.quantity = quantity
                existing_intent.expires_at = expires_at

                # Any of the following could be None since they're optional, so lets only update them if supplied.
                existing_intent.enterprise_slug = slug or existing_intent.enterprise_slug
                existing_intent.enterprise_name = name or existing_intent.enterprise_name
                existing_intent.country = country or existing_intent.country
                existing_intent.terms_metadata = (existing_intent.terms_metadata or {}) | (terms_metadata or {})

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
                terms_metadata=terms_metadata,
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
        """
        Deprecated in favor of update_stripe_identifiers below.
        Update the associated Stripe checkout session ID.
        """
        self.stripe_checkout_session_id = session_id
        self.save(update_fields=['stripe_checkout_session_id', 'modified'])

    def previous_summary(self, stripe_event: stripe.Event, **filter_kwargs) -> 'StripeEventSummary':
        """
        Return the most recent StripeEventSummary for this CheckoutIntent prior to the given event.

        Args:
            stripe_event: The Stripe event to use as the cutoff point

        Returns:
            The most recent StripeEventSummary before the given event, or None if none exists
        """
        # Convert Stripe event timestamp to datetime
        event_timestamp = datetime_from_timestamp(stripe_event.created)

        # Find the most recent summary before this event
        return StripeEventSummary.objects.filter(
            checkout_intent=self,
            stripe_event_created_at__lt=event_timestamp,
            **filter_kwargs,
        ).order_by('-stripe_event_created_at').first()

    def update_stripe_identifiers(self, session_id=None, customer_id=None):
        """
        Updates stripe identifiers related to this checkout intent record.
        """
        if session_id:
            self.stripe_checkout_session_id = session_id
        if customer_id:
            self.stripe_customer_id = customer_id
        self.save(update_fields=['stripe_checkout_session_id', 'stripe_customer_id', 'modified'])


class SelfServiceSubscriptionRenewal(TimeStampedModel):
    """
    Tracks renewal processing for self-service subscriptions, linking a CheckoutIntent
    to the SubscriptionPlanRenewals that compose it.

    This model records when subscription renewals are processed, particularly
    the transition from trial to paid and subsequent annual renewals.

    .. no_pii: This model has no PII
    """
    class Meta:
        verbose_name = "Self Service Subscription Renewal"
        verbose_name_plural = "Self Service Subscription Renewal"
        ordering = ['-created']

    checkout_intent = models.ForeignKey(
        CheckoutIntent,
        on_delete=models.CASCADE,
        related_name='renewals',
        help_text="The CheckoutIntent this renewal is associated with",
    )
    subscription_plan_renewal_id = models.IntegerField(
        help_text="UUID of the SubscriptionPlanRenewal in License Manager",
    )
    prior_subscription_plan_uuid = models.UUIDField(
        null=True,
        blank=True,
        help_text='The prior (or current) subscription plan uuid on this renewal',
    )
    renewed_subscription_plan_uuid = models.UUIDField(
        null=True,
        blank=True,
        help_text='The renewed (or future) subscription plan uuid on this renewal',
    )
    stripe_event_data = models.OneToOneField(
        'StripeEventData',
        on_delete=models.CASCADE,
        related_name='renewal_processing',
        help_text="The Stripe event that triggered this renewal",
    )
    stripe_subscription_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text="The Stripe subscription ID for this renewal",
    )
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the renewal was successfully processed in License Manager",
    )

    history = HistoricalRecords()

    def __str__(self):
        return f"Renewal for {self.checkout_intent.enterprise_slug} - {self.stripe_subscription_id}"

    def mark_as_processed(self):
        """Mark this renewal as successfully processed."""
        self.processed_at = timezone.now()
        self.save(update_fields=['processed_at', 'modified'])


class StripeEventData(TimeStampedModel):
    """
    Persists stripe event payload data.

    .. pii: The data field stores PII,
       which is to be scrubbed after 90 days via management command under certain conditions
    .. pii_types: email_address
    .. pii_retirement: local_api
    """
    event_id = models.CharField(
        max_length=255,
        primary_key=True,
        help_text='The unique event identifier',
    )
    event_type = models.CharField(
        max_length=255,
        null=False,
        db_index=True,
        help_text='The stripe event type.',
    )
    checkout_intent = models.ForeignKey(
        CheckoutIntent,
        null=True,
        on_delete=models.SET_NULL,
        help_text='The related CheckoutIntent, which is infered from the stripe customer id.',
    )
    data = models.JSONField(
        null=False,
        default=dict,
        help_text='The event payload data',
        encoder=DjangoJSONEncoder,
    )
    handled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp when this Stripe event was successfully handled.'
    )

    def __str__(self):
        return f"id={self.event_id}, event_type={self.event_type}"

    def mark_as_handled(self):
        """Mark this event as handled by setting handled_at to now."""
        self.handled_at = timezone.now()
        self.save()

    @property
    def object_data(self):
        """Shortcut to the nested stripe object data for this event."""
        return self.data['data']['object']


class StripeEventSummary(TimeStampedModel):
    """
    Normalized view of StripeEventData with extracted fields for easier querying.
    Populated when StripeEventData records are created/updated.

    .. no_pii: This model has no PII
    """
    # Base StripeEventData fields
    stripe_event_data = models.OneToOneField(
        StripeEventData,
        on_delete=models.CASCADE,
        related_name='summary',
        help_text='Reference to the original StripeEventData record'
    )
    event_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text='The unique Stripe event identifier'
    )
    event_type = models.CharField(
        max_length=255,
        db_index=True,
        help_text='The Stripe event type'
    )
    stripe_event_created_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text='Timestamp when the Stripe event was created'
    )
    checkout_intent = models.ForeignKey(
        CheckoutIntent,
        null=True,
        on_delete=models.CASCADE,
        help_text='The related CheckoutIntent'
    )

    # Normalized fields from provisioning workflow
    subscription_plan_uuid = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text='UUID of the Trial SubscriptionPlan from License Manager'
    )
    currency = models.CharField(
        max_length=3,
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            'Three-letter ISO currency code associated with the subscription.'
        ),
    )

    # Stripe object identification
    stripe_object_type = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        db_index=True,
        help_text='Type of the main Stripe object (invoice, subscription, etc.)'
    )
    stripe_subscription_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
        help_text='Stripe subscription ID extracted from event data'
    )
    stripe_invoice_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
        help_text='Stripe invoice ID extracted from event data'
    )

    # Subscription-related fields
    subscription_status = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text='Status of the Stripe subscription (active, canceled, etc.)'
    )
    subscription_period_start = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Start date of the subscription period'
    )
    subscription_period_end = models.DateTimeField(
        null=True,
        blank=True,
        help_text='End date of the subscription period'
    )
    subscription_cancel_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp when the subscription is scheduled to be canceled'
    )

    # Invoice-related fields
    invoice_amount_paid = models.IntegerField(
        null=True,
        blank=True,
        help_text='Amount paid on the invoice in cents'
    )
    # Invoice unit amount in an integer number of cents
    invoice_unit_amount = models.IntegerField(
        null=True,
        blank=True,
        help_text='Unit amount from the primary invoice line item as integer cents'
    )
    # Stripe provides an invoice unit_amount_decimal field
    # representing the unit price in cents as a decimal string
    # with up to 12 decimal places of precision.
    invoice_unit_amount_decimal = models.DecimalField(
        max_digits=20,
        decimal_places=12,
        null=True,
        blank=True,
        help_text='Unit amount from the primary invoice line item in decimal cents'
    )
    invoice_quantity = models.IntegerField(
        null=True,
        blank=True,
        help_text='Quantity from the primary invoice line item'
    )
    invoice_currency = models.CharField(
        max_length=3,
        null=True,
        blank=True,
        help_text='Currency of the invoice'
    )
    upcoming_invoice_amount_due = models.IntegerField(
        null=True,
        blank=True,
        help_text='Upcoming invoice amount due related to this event/subscription'
    )

    class Meta:
        db_table = 'customer_billing_stripe_event_summary'
        verbose_name = 'Stripe Event Summary'
        verbose_name_plural = 'Stripe Event Summaries'
        indexes = [
            models.Index(fields=['event_type', 'checkout_intent']),
        ]

    def __str__(self):
        return f"Summary of {self.event_type} - {self.event_id}"

    def populate_with_summary_data(self):  # pylint: disable=too-many-statements
        """
        Extract and populate normalized fields from the related StripeEventData.
        """
        stripe_event_data = self.stripe_event_data

        # Copy base fields
        self.event_id = stripe_event_data.event_id
        self.event_type = stripe_event_data.event_type
        checkout_intent = stripe_event_data.checkout_intent
        self.checkout_intent = checkout_intent

        # Extract Stripe event timestamp
        event_data = stripe_event_data.data
        if 'created' in event_data:
            self.stripe_event_created_at = self._timestamp_to_datetime(event_data['created'])
        else:
            logger.warning(f"No 'created' timestamp found in event {stripe_event_data.event_id}")

        # Get subscription plan UUID from related workflow
        if checkout_intent and checkout_intent.workflow:
            # Fetch model from the Django app registry to avoid
            # a circular import.
            subscription_step_model = apps.get_model(
                'provisioning', 'GetCreateTrialSubscriptionPlanStep',
            )
            subscription_step = subscription_step_model.objects.filter(
                workflow_record_uuid=checkout_intent.workflow.uuid,
            ).first()

            try:
                if subscription_step and subscription_step.output_object:
                    self.subscription_plan_uuid = subscription_step.output_object.uuid
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    'Could not get trial subscription output data for %s, CheckoutIntent %s. %s',
                    self.event_id, checkout_intent.uuid, exc,
                )

        # Extract data from the Stripe event payload
        event_data = stripe_event_data.data
        stripe_object_data = event_data.get('data', {}).get('object', {})
        self.stripe_object_type = stripe_object_data['object']
        # pylint: disable=protected-access
        stripe_object = stripe._util.convert_to_stripe_object(event_data['data']['object'])

        # Extract subscription-specific fields
        if self.stripe_object_type == 'subscription' or self.event_type.startswith('customer.subscription'):
            subscription_obj = stripe_object

            self.stripe_subscription_id = subscription_obj.id
            self.subscription_status = subscription_obj.get('status')
            self.currency = subscription_obj.get('currency')
            self.subscription_cancel_at = self._timestamp_to_datetime(
                subscription_obj.get('cancel_at')
            )

            if 'items' not in subscription_obj or not subscription_obj['items'].get('data', []):
                return
            first_item = subscription_obj['items']['data'][0]
            self.subscription_period_start = self._timestamp_to_datetime(
                first_item.get('current_period_start')
            )
            self.subscription_period_end = self._timestamp_to_datetime(
                first_item.get('current_period_end')
            )

        # Extract invoice-specific fields
        elif self.stripe_object_type == 'invoice' or self.event_type.startswith('invoice'):
            invoice_obj = stripe_object
            self.stripe_invoice_id = invoice_obj.id
            try:
                self.stripe_subscription_id = invoice_obj.parent.subscription_details.subscription
            except AttributeError:
                pass
            self.invoice_amount_paid = invoice_obj.get('amount_paid')
            self.invoice_currency = invoice_obj.get('currency')

            # Extract unit amount and quantity from line items
            lines = invoice_obj.get('lines', {}).get('data', [])
            if lines:
                primary_line = lines[0]
                if 'pricing' in primary_line:
                    self.invoice_unit_amount = getattr(primary_line.pricing, 'unit_amount', None)
                    self.invoice_unit_amount_decimal = Decimal(primary_line.pricing.unit_amount_decimal)
                    if 'quantity' in primary_line:
                        self.invoice_quantity = primary_line.quantity
                    if self.invoice_unit_amount is None:
                        self.invoice_unit_amount = int(self.invoice_unit_amount_decimal)

    def update_upcoming_invoice_amount_due(self):
        """
        Updates the `amount_due` value of this record from the first upcoming invoice
        associated with this customer's subscription.
        """
        if not self.checkout_intent:
            logger.warning(
                'Cannot update with upcoming invoice for event %s, no checkout intent exists',
                self.event_id,
            )
            return
        stripe_subscription_id = self.stripe_subscription_id
        stripe_customer_id = self.checkout_intent.stripe_customer_id
        if not (stripe_subscription_id and stripe_customer_id):
            logger.warning('Cannot update with upcoming invoice for event %s', self.event_id)
            return

        # Now call the stripe invoice upcoming API
        upcoming_invoice = stripe_api.get_upcoming_invoice(stripe_customer_id, stripe_subscription_id)
        if not upcoming_invoice:
            logger.warning('No upcoming invoice exists for event %s', self.event_id)
            return

        self.upcoming_invoice_amount_due = upcoming_invoice['amount_due']
        self.save(update_fields=['upcoming_invoice_amount_due'])

    @staticmethod
    def _timestamp_to_datetime(timestamp):
        """Convert Unix timestamp to Django datetime."""
        if timestamp:
            return datetime_from_timestamp(timestamp)
        return None

    @classmethod
    def get_latest_for_checkout_intent(cls, checkout_intent, **filter_kwargs):
        """
        Helper to get latest summary for the given CheckoutIntent.
        """
        result = StripeEventSummary.objects.filter(
            checkout_intent=checkout_intent,
            **filter_kwargs,
        ).order_by('-stripe_event_created_at').first()

        if result:
            logger.info('Found Stripe event summary %s for checkout intent %s', result, checkout_intent.uuid)
        else:
            logger.warning('No Stripe event summary for checkout intent %s', checkout_intent.uuid)
        return result

    @classmethod
    def get_latest_invoice_paid(cls, invoice_id):
        """
        Retrieve the most recent invoice.paid event summary for a given invoice ID.

        Args:
            invoice_id (str): The Stripe invoice ID to look up

        Returns:
            StripeEventSummary: The most recent invoice.paid event summary, or None if not found
        """
        return cls.objects.filter(
            stripe_invoice_id=invoice_id,
            event_type='invoice.paid',
        ).order_by('-stripe_event_created_at').first()

    @classmethod
    def get_latest_subscription_created(cls, subscription_id):
        """
        Retrieve the most recent customer.subscription.created event summary for a given subscription ID.

        Args:
            subscription_id (str): The Stripe subscription ID to look up

        Returns:
            StripeEventSummary: The most recent customer.subscription.created event summary, or None if not found
        """
        return cls.objects.filter(
            stripe_subscription_id=subscription_id,
            event_type='customer.subscription.created',
        ).order_by('-stripe_event_created_at').first()
