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
from enterprise_access.apps.customer_billing.academy_api import get_cached_academy_data, get_cached_course_count
from enterprise_access.apps.customer_billing.constants import ALLOWED_CHECKOUT_INTENT_STATE_TRANSITIONS

from .constants import (
    ALLOWED_LEARNER_CREDIT_TOP_UP_STATE_TRANSITIONS,
    INTENT_RESERVATION_DURATION_MINUTES,
    LEARNER_CREDIT_TOP_UP_MAX_AMOUNT_CENTS,
    LEARNER_CREDIT_TOP_UP_MIN_AMOUNT_CENTS,
    LEARNER_CREDIT_TOP_UP_QUOTE_MINUTES,
    CheckoutIntentState,
    LearnerCreditTopUpState
)
from .utils import datetime_from_timestamp

logger = logging.getLogger(__name__)
User = get_user_model()


def get_default_ssp_product_slug():
    """Return the default SSP product slug from Django settings."""
    return settings.SSP_DEFAULT_PRODUCT_SLUG


class FailedCheckoutIntentConflict(Exception):
    pass


class SlugReservationConflict(Exception):
    pass


class SspProduct(TimeStampedModel):
    """
    Universal product record for all SSP subscription offerings (Teams and each Essentials Academy).

    This is the cross-service interoperability key. ``slug`` is stored in:
      - Stripe Price metadata (as ``ssp_product_slug``, set via Terraform)
      - Salesforce provisioning API calls (passed through from Stripe invoice)

    Academy display metadata (title, description, etc.) is NOT stored here —
    use the ``academy_*`` properties which fetch and cache from enterprise-catalog.

    .. no_pii:
    """
    slug = models.SlugField(
        primary_key=True,
        max_length=255,
        help_text='Universal cross-service identifier, e.g. "teams-yearly", "ai-academy-yearly".',
    )
    stripe_price_lookup_key = models.CharField(
        max_length=255,
        unique=True,
        help_text='Stripe Price lookup_key for this product.',
    )
    academy_uuid = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text='enterprise-catalog Academy UUID. Null for non-Academy products (e.g. Teams).',
    )
    catalog_query_id = models.IntegerField(
        null=False,
        blank=False,
        default=0,
        db_index=True,
        help_text='The enterprise-catalog CatalogQuery.id value, used during provisioning.',
    )
    catalog_query_uuid = models.UUIDField(
        help_text='enterprise-catalog CatalogQuery UUID used for provisioning.',
    )
    license_manager_product_id_trial = models.IntegerField(
        null=True,
        blank=True,
        help_text='license-manager product ID for the trial subscription period.',
    )
    license_manager_product_id_paid = models.IntegerField(
        null=True,
        blank=True,
        help_text='license-manager product ID for the paid subscription period.',
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
    )
    marketing_url = models.URLField(
        max_length=255,
        null=True,
        blank=True,
        help_text='Marketing URL for this product. Overrides academy_marketing_url when present.',
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = 'SSP Product'
        verbose_name_plural = 'SSP Products'

    def __str__(self):
        return f'<SspProduct slug={self.slug}>'

    @property
    def _academy_data(self):
        """Fetch and instance-cache the academy payload. None for non-Academy products."""
        if not getattr(self, '_academy_data_cache', None):
            try:
                # pylint: disable=attribute-defined-outside-init
                self._academy_data_cache = get_cached_academy_data(self.academy_uuid)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning('Failed to fetch academy data for %s', self.academy_uuid)
                return None
        return self._academy_data_cache

    @property
    def academy_title(self):
        """Academy display name. None for non-Academy products."""
        return self._academy_data.get('title') if self._academy_data else None

    @property
    def academy_description(self):
        """Academy description. Prefers long_description, then short_description, then description."""
        if not self._academy_data:
            return None
        for key in ('long_description', 'short_description', 'description'):
            value = self._academy_data.get(key)
            if value:
                return value
        return None

    @property
    def academy_long_name(self):
        """Academy long public name. Uses long_name when present, else falls back to title."""
        if not self._academy_data:
            return None
        return self._academy_data.get('long_name') or self.academy_title

    @property
    def academy_marketing_url(self):
        """Academy marketing URL. None for non-Academy products."""
        return self._academy_data.get('marketing_url') if self._academy_data else None

    @property
    def academy_thumbnail_url(self):
        """Academy thumbnail image URL. Prefers image field, falls back to thumbnail_url."""
        if not self._academy_data:
            return None
        return self._academy_data.get('image') or self._academy_data.get('thumbnail_url')

    @property
    def academy_tags(self):
        """Academy competency tags. None for non-Academy products."""
        return self._academy_data.get('tags') if self._academy_data else None

    @property
    def course_count(self):
        """Number of courses in this product's enterprise-catalog CatalogQuery. None if it can't be fetched."""
        return get_cached_course_count(self.catalog_query_uuid)

    @property
    def enterprise_catalog_metadata(self):
        """
        Builds the enterprise_catalog dict for Stripe subscription metadata.
        """
        if not self.catalog_query_id:
            return None

        title = self.academy_title or "Open Courses"
        return {
            "title": title,
            "catalog_query_id": self.catalog_query_id,
        }


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
        constraints = [
            models.UniqueConstraint(fields=['user', 'ssp_product'], name='unique_user_ssp_product'),
        ]

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

    user = models.ForeignKey(
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
    ssp_product = models.ForeignKey(
        'SspProduct',
        on_delete=models.PROTECT,
        null=False,
        blank=False,
        default=get_default_ssp_product_slug,
        help_text='The SSP product associated with this checkout intent.',
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
        is_valid = new_state in allowed_transitions
        if not is_valid:
            logger.warning(
                'Rejected CheckoutIntent state transition from %s to %s (allowed: %s)',
                current_state, new_state, allowed_transitions,
            )
        return is_valid

    def mark_as_paid(self, stripe_session_id=None, stripe_customer_id=None, **kwargs):
        """Mark the intent as paid after successful Stripe checkout."""
        if not self.is_valid_state_transition(CheckoutIntentState(self.state), CheckoutIntentState.PAID):
            raise ValueError(f"Cannot transition from {self.state} to {CheckoutIntentState.PAID}.")

        if stripe_session_id:
            if self.state == CheckoutIntentState.PAID and stripe_session_id != self.stripe_checkout_session_id:
                logger.error(
                    'CheckoutIntent %s: refusing to overwrite stripe_checkout_session_id %s with %s while PAID',
                    self.id, self.stripe_checkout_session_id, stripe_session_id,
                )
                raise ValueError("Cannot transition from PAID to PAID with a different stripe_checkout_session_id")

        if stripe_customer_id:
            if self.state == CheckoutIntentState.PAID and stripe_customer_id != self.stripe_customer_id:
                logger.error(
                    'CheckoutIntent %s: refusing to overwrite stripe_customer_id %s with %s while PAID',
                    self.id, self.stripe_customer_id, stripe_customer_id,
                )
                raise ValueError("Cannot transition from PAID to PAID with a different stripe_customer_id")

        logger.info(
            'Marking CheckoutIntent %s as PAID (stripe_session_id=%s, stripe_customer_id=%s)',
            self.id, stripe_session_id, stripe_customer_id,
        )
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
        logger.info(
            'Marking CheckoutIntent %s as FULFILLED (workflow_uuid=%s)',
            self.id, workflow.uuid if workflow else None,
        )
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
        logger.error(
            'Marking CheckoutIntent %s as ERRORED_BACKOFFICE: %s',
            self.id, error_message,
        )
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
        logger.error(
            'Marking CheckoutIntent %s as ERRORED_PROVISIONING (workflow_uuid=%s): %s',
            self.id, workflow.uuid if workflow else None, error_message,
        )
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

    @classmethod
    def get_latest_for_customer(
        cls,
        customer_uuid=None,
        customer_slug=None,
        require_stripe_customer=False,
    ):
        """
        Return the most recently created CheckoutIntent for a customer.

        Args:
            customer_uuid: Enterprise customer UUID to filter by.
            customer_slug: Enterprise customer slug to filter by.
            require_stripe_customer: If True, only return intents that have
                a non-empty stripe_customer_id.

        Returns:
            CheckoutIntent | None: Most recent matching intent, or None.
        """
        if not (customer_uuid or customer_slug):
            raise ValueError("One of customer_uuid or customer_slug must be provided")

        queryset = cls.objects.select_related('ssp_product')
        if customer_uuid:
            queryset = queryset.filter(enterprise_uuid=customer_uuid)
        if customer_slug:
            queryset = queryset.filter(enterprise_slug=customer_slug)

        if require_stripe_customer:
            queryset = queryset.exclude(stripe_customer_id__isnull=True).exclude(stripe_customer_id='')

        return queryset.order_by('-created').first()

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
                logger.warning(
                    'Rejecting new CheckoutIntent for user %s: already has active intent %s',
                    self.user.email, existing_intent,
                )
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
            logger.warning(
                'Rejecting CheckoutIntent for enterprise_name=%s: already reserved by %s (intent %s)',
                self.enterprise_name, conflict.user.email, conflict,
            )
            raise ValidationError({
                'enterprise_name': (
                    f"This enterprise name is already reserved by {conflict.user.email} (intent is {conflict})"
                )
            })

        # Handle slug conflicts
        slug_conflicts = [c for c in conflicts if c.enterprise_slug == self.enterprise_slug]
        if slug_conflicts:
            conflict = slug_conflicts[0]
            logger.warning(
                'Rejecting CheckoutIntent for enterprise_slug=%s: already reserved by %s (intent %s)',
                self.enterprise_slug, conflict.user.email, conflict,
            )
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
        terms_metadata: dict | None = None,
        ssp_product: 'SspProduct | None' = None,
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
                    logger.info(
                        'create_intent for user %s: existing intent %s already in success state %s, returning as-is',
                        user.email, existing_intent.id, existing_intent.state,
                    )
                    return existing_intent

                if existing_intent.state in cls.FAILURE_STATES:
                    logger.warning(
                        'create_intent for user %s: existing intent %s is in failure state %s',
                        user.email, existing_intent.id, existing_intent.state,
                    )
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
                    logger.warning(
                        'create_intent for user %s: slug %s / name %s is already reserved',
                        user.email, slug, name,
                    )
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
                if ssp_product is not None:
                    existing_intent.ssp_product = ssp_product
                existing_intent.save()
                logger.info(
                    'create_intent for user %s: updated existing intent %s with slug=%s, name=%s, quantity=%s',
                    user.email, existing_intent.id, slug, name, quantity,
                )
                return existing_intent

            create_kwargs = {
                'user': user,
                'state': CheckoutIntentState.CREATED,
                'enterprise_slug': slug,
                'enterprise_name': name,
                'quantity': quantity,
                'expires_at': expires_at,
                'country': country,
                'terms_metadata': terms_metadata,
            }
            if ssp_product is not None:
                create_kwargs['ssp_product'] = ssp_product
            new_intent = cls.objects.create(**create_kwargs)
            logger.info(
                'create_intent for user %s: created new intent %s with slug=%s, name=%s, quantity=%s',
                user.email, new_intent.id, slug, name, quantity,
            )
            return new_intent

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
        logger.info('Updating CheckoutIntent %s stripe_checkout_session_id to %s', self.id, session_id)
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
        logger.info(
            'Updating CheckoutIntent %s stripe identifiers: session_id=%s, customer_id=%s',
            self.id, session_id, customer_id,
        )
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
    is_canceled = models.BooleanField(
        default=False,
        db_index=True,
        help_text='True if the subscription has been canceled. Can be set back to False if subscription is '
                  'un-canceled.',
    )
    subscription_cancel_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            'Timestamp when the subscription is scheduled to be canceled. '
            'Set from Stripe cancel_at on subscription_updated events; '
            'cleared on subscription deletion or when cancellation is reversed.'
        ),
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
    stripe_invoice_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
        help_text="The Stripe invoice ID that this renewal corresponds to.",
    )
    effective_date = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "The datetime at which this renewal is expected to take effect, "
            "derived from the period end of the previous subscription cycle. "
            "This is a local cache of the like-named column on the "
            "license-manager SubscriptionPlanRenewal model."
        ),
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

    def save(self, *args, **kwargs):
        """
        Infer checkout_intent from related event data if not present on self.
        """
        if not self.checkout_intent:
            if self.stripe_event_data.checkout_intent:
                self.checkout_intent = self.stripe_event_data.checkout_intent
        super().save(*args, **kwargs)

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
            logger.warning(
                "No 'created' timestamp found in event %s, falling back to StripeEventData.created",
                stripe_event_data.event_id,
            )
            self.stripe_event_created_at = stripe_event_data.created

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
        stripe_object = stripe._util.convert_to_stripe_object(event_data['data']['object']).to_dict()

        # Extract subscription-specific fields
        if self.stripe_object_type == 'subscription' or self.event_type.startswith('customer.subscription'):
            subscription_obj = stripe_object

            self.stripe_subscription_id = subscription_obj['id']
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
            self.stripe_invoice_id = invoice_obj['id']
            try:
                self.stripe_subscription_id = invoice_obj['parent']['subscription_details']['subscription']
            except (KeyError, TypeError):
                pass
            self.invoice_amount_paid = invoice_obj.get('amount_paid')
            self.invoice_currency = invoice_obj.get('currency')

            # Extract unit amount and quantity from line items
            lines = invoice_obj.get('lines', {}).get('data', [])
            if lines:
                primary_line = lines[0]
                if 'pricing' in primary_line:
                    pricing = primary_line.get('pricing', {})
                    self.invoice_unit_amount = pricing.get('unit_amount')
                    unit_amount_decimal = pricing.get('unit_amount_decimal')
                    self.invoice_unit_amount_decimal = Decimal(unit_amount_decimal) if unit_amount_decimal else None
                    if 'quantity' in primary_line:
                        self.invoice_quantity = primary_line.get('quantity')
                    if self.invoice_unit_amount is None and self.invoice_unit_amount_decimal is not None:
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


class InvalidLearnerCreditTopUpTransition(Exception):
    """
    Raised when a LearnerCreditTopUp is asked to move to a state that
    ALLOWED_LEARNER_CREDIT_TOP_UP_STATE_TRANSITIONS doesn't allow from its current one.
    """


def get_default_learner_credit_top_up_expires_at():
    """
    Return when a top-up quote created now expires. Module-level so that migrations can serialize it.
    """
    return timezone.now() + timedelta(minutes=LEARNER_CREDIT_TOP_UP_QUOTE_MINUTES)


def _is_whole_cents(value):
    """
    Return True if ``value`` is None or an int. A Decimal or float is never accepted, even with no fractional part.
    """
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


class LearnerCreditTopUp(TimeStampedModel):
    """
    One self-service top-up of a Learner Credit budget, from quote to fulfillment.

    This is the audit record of every self-service deposit: who started it, how much, when,
    which payment and what discount. Rows must never be deleted.

    A top-up is created in ``created``, and ``state`` changes only through ``transition_to()``, which
    allows only the changes listed in ``ALLOWED_LEARNER_CREDIT_TOP_UP_STATE_TRANSITIONS``. The quote
    fields are checked when the top-up is created and can't change afterwards.

    .. pii: initiated_by_lms_user_id stores the LMS user id of the admin who started the top-up.
       It is kept after the user is retired, because the row is a payment record kept for audit.
    .. pii_types: id
    .. pii_retirement: retained
    """
    class Meta:
        verbose_name = "Learner Credit Top-Up"
        verbose_name_plural = "Learner Credit Top-Ups"
        indexes = [
            models.Index(fields=['subsidy_access_policy', 'state']),
        ]

    class StateChoices(models.TextChoices):
        """
        Allowed choices for the state field
        """
        CREATED = (LearnerCreditTopUpState.CREATED, 'Created')
        REQUIRES_ACTION = (LearnerCreditTopUpState.REQUIRES_ACTION, 'Requires action')
        PAID = (LearnerCreditTopUpState.PAID, 'Paid')
        DEPOSITED = (LearnerCreditTopUpState.DEPOSITED, 'Deposited')
        FULFILLED = (LearnerCreditTopUpState.FULFILLED, 'Fulfilled')
        EXPIRED = (LearnerCreditTopUpState.EXPIRED, 'Expired')
        ERRORED_FULFILLMENT = (LearnerCreditTopUpState.ERRORED_FULFILLMENT, 'Errored (Fulfillment)')
        REFUNDED = (LearnerCreditTopUpState.REFUNDED, 'Refunded')

    # The timestamp that transition_to() sets on the change into each state.
    STATE_TIMESTAMP_FIELDS = {
        LearnerCreditTopUpState.PAID: 'paid_at',
        LearnerCreditTopUpState.DEPOSITED: 'deposited_at',
        LearnerCreditTopUpState.FULFILLED: 'fulfilled_at',
    }

    # Fixed when the quote is created: what the admin accepted, and what the charge and the deposit are made from.
    QUOTE_FIELDS = frozenset({
        'enterprise_customer_uuid',
        'subsidy_access_policy',
        'subsidy_uuid',
        'initiated_by',
        'initiated_by_lms_user_id',
        'amount_cents',
        'discount_rate',
        'discount_cents',
        'charge_amount_cents',
        'currency',
        'sales_quote_number',
        'stripe_customer_id',
    })

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        help_text='Source of the Stripe and deposit idempotency keys, and the transaction ID shown to the admin.',
    )
    enterprise_customer_uuid = models.UUIDField(
        db_index=True,
        help_text="The policy's enterprise. Copied from the policy when not given.",
    )
    subsidy_access_policy = models.ForeignKey(
        'subsidy_access_policy.SubsidyAccessPolicy',
        on_delete=models.PROTECT,
        related_name='learner_credit_top_ups',
        help_text='The budget being topped up.',
    )
    subsidy_uuid = models.UUIDField(
        db_index=True,
        help_text="The subsidy the deposit goes to. Copied from the policy's subsidy_uuid when not given.",
    )
    initiated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='learner_credit_top_ups',
        help_text='The admin who started the top-up. Null once that user is deleted.',
    )
    initiated_by_lms_user_id = models.IntegerField(
        null=True,
        help_text="Copied from initiated_by.lms_user_id, and kept after the user is retired.",
    )
    state = models.CharField(
        max_length=255,
        choices=StateChoices.choices,
        default=StateChoices.CREATED,
        db_index=True,
        help_text='Changed only through transition_to().',
    )
    amount_cents = models.BigIntegerField(
        help_text=(
            'Credit added to the budget, in USD cents: the full selected amount, not the discounted one. '
            'Also the deposit quantity and the spend limit increase.'
        ),
    )
    discount_rate = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        null=True,
        help_text='The discount rate copied at quote time. Null means no discount is on record; 0 is a real 0% rate.',
    )
    discount_cents = models.BigIntegerField(
        null=True,
        help_text='The discount, in USD cents. Null exactly when discount_rate is null.',
    )
    charge_amount_cents = models.BigIntegerField(
        help_text='What the card is charged, in USD cents: amount_cents minus discount_cents.',
    )
    currency = models.CharField(
        max_length=3,
        choices=[('usd', 'USD')],
        default='usd',
    )
    sales_quote_number = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text='Copied at quote time for the legal agreement.',
    )
    agreement_version = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text='The agreement version the admin accepted when paying.',
    )
    agreement_accepted_at = models.DateTimeField(
        null=True,
    )
    stripe_customer_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text='The Stripe Customer the top-up is charged to.',
    )
    stripe_payment_intent_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        help_text='The one Stripe PaymentIntent of this top-up. Null until the admin pays.',
    )
    payment_method_type = models.CharField(
        max_length=32,
        choices=[('card', 'Card')],
        null=True,
    )
    stripe_payment_method_id = models.CharField(
        max_length=255,
        null=True,
        help_text='The Stripe PaymentMethod used to pay.',
    )
    deposit_uuid = models.UUIDField(
        null=True,
        help_text='The deposit returned by enterprise-subsidy.',
    )
    ledger_transaction_uuid = models.UUIDField(
        null=True,
        help_text="The deposit's ledger transaction, returned by enterprise-subsidy.",
    )
    spend_limit_before = models.IntegerField(
        null=True,
        help_text="The policy's spend_limit before the increase. Null when the policy has no spend limit.",
    )
    spend_limit_after = models.IntegerField(
        null=True,
        help_text="The policy's spend_limit after the increase. Null when the policy has no spend limit.",
    )
    expires_at = models.DateTimeField(
        default=get_default_learner_credit_top_up_expires_at,
        help_text='An unpaid quote past this time cannot be paid.',
    )
    paid_at = models.DateTimeField(
        null=True,
        help_text='Set by transition_to() on the change into paid.',
    )
    deposited_at = models.DateTimeField(
        null=True,
        help_text='Set by transition_to() on the change into deposited.',
    )
    fulfilled_at = models.DateTimeField(
        null=True,
        help_text='Set by transition_to() on the change into fulfilled.',
    )
    last_error = models.TextField(
        null=True,
        blank=True,
        help_text='The latest payment or fulfillment error.',
    )
    salesforce_opportunity_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )
    salesforce_synced_at = models.DateTimeField(
        null=True,
    )

    history = HistoricalRecords()

    def __str__(self):
        return (
            "<LearnerCreditTopUp "
            f"uuid={self.uuid}, "
            f"subsidy_access_policy={self.subsidy_access_policy_id}, "
            f"state={self.state}, "
            f"amount_cents={self.amount_cents}>"
        )

    @classmethod
    def is_valid_state_transition(
        cls,
        current_state: LearnerCreditTopUpState,
        new_state: LearnerCreditTopUpState,
    ) -> bool:
        """
        Return True if the transition table allows moving from current_state to new_state.
        Staying in the same state is always allowed.
        """
        if current_state == new_state:
            return True
        return new_state in ALLOWED_LEARNER_CREDIT_TOP_UP_STATE_TRANSITIONS.get(current_state, [])

    def _protected_fields_in(self, names) -> list[str]:
        """
        Return which of ``names`` (field names or attnames) only transition_to() may write, or nothing may.
        """
        protected = {'state', *self.STATE_TIMESTAMP_FIELDS.values(), *self.QUOTE_FIELDS}
        return sorted(protected.intersection(self._meta.get_field(name).name for name in names))

    def _reload_for_update(self):
        """
        Reload this top-up from the database under a row lock. Call inside transaction.atomic().
        """
        self.refresh_from_db(from_queryset=LearnerCreditTopUp.objects.select_for_update())

    def _write(self, changes, **save_kwargs):
        """
        Apply ``changes`` and write them, with ``modified``, in one UPDATE that skips this class's save() checks.

        Call inside transaction.atomic(), after _reload_for_update(). If the write fails, the atomic block
        undoes it, so the instance is put back to the values it had before ``changes`` were applied.
        """
        previous_values = {name: getattr(self, name) for name in [*changes, 'modified']}
        for name, value in changes.items():
            setattr(self, name, value)
        try:
            super().save(**{**save_kwargs, 'update_fields': [*changes, 'modified']})
        except Exception:
            for name, value in previous_values.items():
                setattr(self, name, value)
            raise

    def transition_to(self, new_state, **fields) -> bool:
        """
        Move this top-up to ``new_state``, saving ``fields`` in the same write.

        This is the only way ``state`` changes. The top-up is first reloaded under a row lock, so the
        change is checked against the stored state rather than a copy that another request or task
        has since moved on from, and concurrent calls for one top-up run one at a time. Unsaved
        changes to the instance are discarded: pass them as ``fields``.

        The change into ``paid``, ``deposited`` or ``fulfilled`` also sets ``paid_at``,
        ``deposited_at`` or ``fulfilled_at``, unless it is already set: an ops re-run keeps the
        first timestamp.

        Returns:
            True if this call changed the state. False if the top-up was already in ``new_state``:
            then nothing is written, ``fields`` are not saved, and no history record is added.

        Raises:
            InvalidLearnerCreditTopUpTransition: the transition table doesn't allow the change.
            ValueError: ``new_state`` is not a state, or ``fields`` names ``state``, a state
                timestamp or a quote field.

        Whenever it raises, including when the save itself fails, nothing is written and the
        instance holds the stored values.
        """
        new_state = LearnerCreditTopUpState(new_state)
        if protected := self._protected_fields_in(fields):
            raise ValueError(f'transition_to() cannot set {protected}.')

        with transaction.atomic():
            self._reload_for_update()
            current_state = LearnerCreditTopUpState(self.state)

            if current_state == new_state:
                if ignored := sorted(name for name, value in fields.items() if getattr(self, name) != value):
                    logger.warning(
                        'LearnerCreditTopUp %s is already %s; not saving %s', self.uuid, new_state, ignored,
                    )
                return False

            if not self.is_valid_state_transition(current_state, new_state):
                logger.warning(
                    'Rejected LearnerCreditTopUp %s state transition from %s to %s (allowed: %s)',
                    self.uuid, current_state, new_state,
                    ALLOWED_LEARNER_CREDIT_TOP_UP_STATE_TRANSITIONS.get(current_state, []),
                )
                raise InvalidLearnerCreditTopUpTransition(
                    f'Cannot move LearnerCreditTopUp {self.uuid} from {current_state} to {new_state}.'
                )

            changes = {'state': new_state, **fields}
            timestamp_field = self.STATE_TIMESTAMP_FIELDS.get(new_state)
            if timestamp_field and getattr(self, timestamp_field) is None:
                changes[timestamp_field] = timezone.now()
            self._write(changes)

        logger.info('LearnerCreditTopUp %s moved from %s to %s.', self.uuid, current_state, new_state)
        return True

    def clean(self):
        """
        Check a new top-up: require it to start as an unpaid quote, fill in the copies of the
        policy's and the initiating admin's fields, and check the amounts.

        Only new top-ups are checked. The quote fields can't change afterwards (see save()), and
        re-checking them later could leave a paid top-up unable to move, for example after the
        allowed range changes.
        """
        if not self._state.adding:
            return

        errors = {}
        # A top-up starts as an unpaid quote. Every later state, and its timestamp, is reached through
        # transition_to(), so a row can't be inserted already deposited or fulfilled, skipping the money steps.
        if self.state != LearnerCreditTopUpState.CREATED:
            errors['state'] = f'A new top-up must start in {LearnerCreditTopUpState.CREATED}.'
        for field_name in self.STATE_TIMESTAMP_FIELDS.values():
            if getattr(self, field_name) is not None:
                errors[field_name] = 'Set by transition_to(), not when the top-up is created.'
        if not self.subsidy_access_policy_id:
            errors['subsidy_access_policy'] = 'This field is required.'
        else:
            policy = self.subsidy_access_policy
            for field_name in ('enterprise_customer_uuid', 'subsidy_uuid'):
                value = getattr(self, field_name)
                policy_value = getattr(policy, field_name)
                if value is None:
                    setattr(self, field_name, policy_value)
                elif self._meta.get_field(field_name).to_python(value) != policy_value:
                    errors[field_name] = f'Must match the policy ({policy_value}).'

        if self.initiated_by_lms_user_id is None and self.initiated_by_id:
            self.initiated_by_lms_user_id = self.initiated_by.lms_user_id

        if self.discount_rate is not None and not 0 <= self.discount_rate < 1:
            errors['discount_rate'] = 'Must be at least 0 and less than 1.'

        # Amounts are whole cents, given as int. Django would truncate a fractional value only when saving it,
        # after the checks below had passed on the untruncated one, so reject it rather than check its arithmetic.
        amount_fields = ('amount_cents', 'discount_cents', 'charge_amount_cents')
        if fractional := [name for name in amount_fields if not _is_whole_cents(getattr(self, name))]:
            errors.update({name: 'Must be a whole number of cents.' for name in fractional})
        else:
            amount = self.amount_cents
            min_amount, max_amount = LEARNER_CREDIT_TOP_UP_MIN_AMOUNT_CENTS, LEARNER_CREDIT_TOP_UP_MAX_AMOUNT_CENTS
            if amount is None or not min_amount <= amount <= max_amount:
                errors['amount_cents'] = f'Must be between {min_amount} and {max_amount} cents.'
            if (self.discount_rate is None) != (self.discount_cents is None):
                errors['discount_cents'] = 'Must be null exactly when discount_rate is null.'
            elif self.discount_cents is not None and not 0 <= self.discount_cents < (amount or 0):
                # The card is charged something, and never more than the credit.
                errors['discount_cents'] = 'Must be at least 0 and less than amount_cents.'
            if amount is not None and self.charge_amount_cents != amount - (self.discount_cents or 0):
                errors['charge_amount_cents'] = 'Must equal amount_cents minus discount_cents.'

        if errors:
            raise ValidationError(errors)

    def save(self, **kwargs):
        """
        Check a new top-up with clean(); on an existing one, write only the fields in ``update_fields``.

        An existing top-up must be saved with ``update_fields``, and they may not include ``state``,
        a state timestamp or a quote field. A full save of an instance loaded before another request
        moved the top-up would write its old state back. Use transition_to() to change the state.

        An existing top-up is first reloaded under a row lock, and only the ``update_fields`` values
        are applied to it, so the history record this save adds is the stored row plus this change,
        not a stale or locally edited copy. Unsaved changes to other fields are discarded, and if the
        save fails, the instance holds the stored values.

        This calls clean() rather than full_clean(), which would report a reused
        stripe_payment_intent_id as a ValidationError rather than leaving it to the unique index,
        and would reject the nullable columns that aren't blank=True.
        """
        if self._state.adding:
            self.clean()
            super().save(**kwargs)
            return

        update_fields = kwargs.get('update_fields')
        if update_fields is None:
            raise ValueError('Save an existing LearnerCreditTopUp with update_fields, or use transition_to().')
        if protected := self._protected_fields_in(update_fields):
            raise ValueError(f'{protected} can only be changed by transition_to(), or not at all.')
        if not update_fields:
            # As in Django, an empty update_fields saves nothing.
            return

        updates = {
            attname: getattr(self, attname)
            for attname in (self._meta.get_field(name).attname for name in update_fields)
        }
        updates.pop('modified', None)
        with transaction.atomic():
            self._reload_for_update()
            self._write(updates, **{name: value for name, value in kwargs.items() if name != 'update_fields'})


class EnterpriseStripeCustomer(TimeStampedModel):
    """
    The one Stripe Customer of an enterprise, including enterprises with no CheckoutIntent (provisioned through
    Salesforce). Read it with get_stripe_customer_id_for_enterprise() in ``customer_billing/api.py``.

    ``stripe_customer_id`` is not unique: checkout reuses the Stripe customer that has the admin's email, so two
    enterprises with the same admin can share one.

    .. no_pii: This model has no PII
    """
    class Meta:
        verbose_name = 'Enterprise Stripe Customer'
        verbose_name_plural = 'Enterprise Stripe Customers'

    class SourceChoices(models.TextChoices):
        """
        How the mapping was first recorded.
        """
        CHECKOUT_INTENT_BACKFILL = ('checkout_intent_backfill', 'CheckoutIntent backfill')
        CREATED_FOR_TOP_UP = ('created_for_top_up', 'Created for top-up')

    enterprise_customer_uuid = models.UUIDField(
        unique=True,
        help_text='The EnterpriseCustomer this Stripe customer belongs to.',
    )
    stripe_customer_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text='The Stripe Customer identifier of the enterprise.',
    )
    source = models.CharField(
        max_length=64,
        choices=SourceChoices.choices,
        help_text='How this mapping was first recorded.',
    )

    history = HistoricalRecords()

    def __str__(self):
        return (
            f'<EnterpriseStripeCustomer enterprise_customer_uuid={self.enterprise_customer_uuid}, '
            f'stripe_customer_id={self.stripe_customer_id}>'
        )
