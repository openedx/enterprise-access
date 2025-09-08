"""
Models for subsidy_access_policy
"""
# AED 2025-05-01: pylint runner is crashing in github actions
# when this file is not disabled.
# pylint: skip-file

import logging
import sys
from contextlib import contextmanager
from uuid import UUID, uuid4

import requests
from django.conf import settings
from django.core.cache import cache as django_cache
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django_extensions.db.models import TimeStampedModel
from edx_django_utils.cache.utils import get_cache_key
from simple_history.models import HistoricalRecords

from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.content_assignments import api as assignments_api
from enterprise_access.apps.content_assignments.constants import LearnerContentAssignmentStateChoices
from enterprise_access.apps.subsidy_request.constants import (
    LearnerCreditRequestActionErrorReasons,
    LearnerCreditRequestUserMessages,
    SubsidyRequestStates
)
from enterprise_access.apps.subsidy_request.models import (
    LearnerCreditRequestActions,
    SubsidyRequestCustomerConfiguration
)
from enterprise_access.apps.subsidy_request.utils import (
    get_action_choice,
    get_error_reason_choice,
    get_user_message_choice
)
from enterprise_access.cache_utils import request_cache, versioned_cache_key
from enterprise_access.utils import format_traceback, is_none, is_not_none, localized_utcnow

from ..content_assignments.models import AssignmentConfiguration
from .constants import (
    ASSIGNED_CREDIT_POLICY_TYPE_PRIORITY,
    CREDIT_POLICY_TYPE_PRIORITY,
    FORCE_ENROLLMENT_KEYWORD,
    REASON_BEYOND_ENROLLMENT_DEADLINE,
    REASON_BNR_NOT_ENABLED,
    REASON_CONTENT_NOT_IN_CATALOG,
    REASON_LEARNER_ASSIGNMENT_CANCELLED,
    REASON_LEARNER_ASSIGNMENT_EXPIRED,
    REASON_LEARNER_ASSIGNMENT_FAILED,
    REASON_LEARNER_ASSIGNMENT_REVERSED,
    REASON_LEARNER_MAX_ENROLLMENTS_REACHED,
    REASON_LEARNER_MAX_SPEND_REACHED,
    REASON_LEARNER_NOT_ASSIGNED_CONTENT,
    REASON_LEARNER_NOT_IN_ENTERPRISE,
    REASON_LEARNER_NOT_IN_ENTERPRISE_GROUP,
    REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY,
    REASON_POLICY_EXPIRED,
    REASON_POLICY_SPEND_LIMIT_REACHED,
    REASON_SUBSIDY_EXPIRED,
    VALIDATION_ERROR_SPEND_LIMIT_EXCEEDS_STARTING_BALANCE,
    AccessMethods,
    TransactionStateChoices
)
from .content_metadata_api import (
    enroll_by_datetime,
    get_and_cache_catalog_contains_content,
    get_and_cache_content_metadata,
    get_list_price_for_content,
    make_list_price_dict
)
from .customer_api import get_and_cache_enterprise_learner_record
from .exceptions import (
    ContentPriceNullException,
    MissingAssignment,
    PriceValidationError,
    SubsidyAccessPolicyLockAttemptFailed,
    SubsidyAPIHTTPError
)
from .subsidy_api import (
    CACHE_MISS,
    get_and_cache_transactions_for_learner,
    get_tiered_cache_subsidy_record,
    set_tiered_cache_subsidy_record
)
from .utils import (
    ProxyAwareHistoricalRecords,
    cents_to_usd_string,
    create_idempotency_key_for_transaction,
    get_versioned_subsidy_client,
    validate_budget_deactivation_with_spend
)

# Magic key that is used transaction metadata hint to the subsidy service and all downstream services that the
# enrollment should be allowed even if the enrollment deadline has passed.
ALLOW_LATE_ENROLLMENT_KEY = 'allow_late_enrollment'

REQUEST_CACHE_NAMESPACE = 'subsidy_access_policy'
POLICY_LOCK_RESOURCE_NAME = 'subsidy_access_policy'
logger = logging.getLogger(__name__)


class PolicyManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(policy_type=self.model.__name__)


class SubsidyAccessPolicy(TimeStampedModel):
    """
    Tie together information used to control access to a subsidy.
    This model joins group, catalog, and access method.

    .. no_pii: This model has no PII
    """

    class Meta:
        unique_together = [
            ('active', 'assignment_configuration'),
        ]

    # Helps support model-level validation, along with serializer-level validation,
    # in a way that makes it possible to validate data *before changing
    # the state of a model instance in-memory* in a serializer.
    # Keyed by field name, and valued by a constraint function and error message:
    # {
    #   field name: (
    #    constraint function that returns false if constraint is broken,
    #    error message on broken constraint
    #   )
    # }
    # Used in conjunction with clean() below.
    FIELD_CONSTRAINTS = {}

    POLICY_FIELD_NAME = 'policy_type'

    policy_type = models.CharField(
        max_length=64,
        editable=False,
        help_text='The type of this policy (e.g. the name of an access policy proxy model).'
    )

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
        help_text='The uuid that uniquely identifies this policy record.',
    )
    enterprise_customer_uuid = models.UUIDField(
        db_index=True,
        null=False,
        blank=False,
        # This field should, in practice, never be null.
        # However, we are retroactively requiring it and need a default
        # for the sake of the historical table.
        default=UUID('0' * 32),
        help_text=(
            "The owning Enterprise Customer's UUID.  Cannot be blank or null."
        ),
    )
    display_name = models.CharField(
        max_length=512,
        null=True,
        blank=True,
        default=None,
        verbose_name='Display name',
        help_text='User-facing display name for this policy record',
    )
    description = models.TextField(
        blank=True,
        null=False,
        default='',
        help_text="Brief description about a specific policy.",
    )
    active = models.BooleanField(
        default=False,
        help_text=(
            'Set to FALSE to deactivate and hide this policy. Use this when you want to disable redemption and make '
            'it disappear from all frontends, effectively soft-deleting it. Default is False (deactivated).'
        ),
    )
    learner_credit_request_config = models.OneToOneField(
        'subsidy_request.LearnerCreditRequestConfiguration',
        related_name="learner_credit_config",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    retired = models.BooleanField(
        default=False,
        help_text=(
            "True means redeemability of content using this policy has been enabled. "
            "Set this to False to deactivate the policy but keep it visible from an admin's perspective "
            "(useful when you want to just expire a policy without expiring the whole plan)."
        ),
    )
    retired_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "The date and time when this policy is considered retired."
        )
    )
    catalog_uuid = models.UUIDField(
        db_index=True,
        help_text='The primary identifier of the catalog associated with this policy.',
    )
    subsidy_uuid = models.UUIDField(
        db_index=True,
        help_text='The primary identifier of the subsidy associated with this policy.',
    )
    access_method = models.CharField(
        max_length=32,
        choices=AccessMethods.CHOICES,
        default=AccessMethods.DIRECT,
        help_text='The mechanism by which learners access content in this policy, defaults to "direct".',
    )
    spend_limit = models.IntegerField(
        null=True,
        blank=True,
        default=None,
        verbose_name='Policy-wide spend limit (USD cents)',
        help_text=(
            'The maximum number of allowed dollars to be spent, in aggregate, by all learners '
            'under this policy. Denoted in USD cents. '
            'Defaults to null, which means that no such maximum exists.'
        ),
    )
    assignment_configuration = models.OneToOneField(
        AssignmentConfiguration,
        related_name='subsidy_access_policy',
        on_delete=models.SET_NULL,
        db_index=True,
        null=True,
        blank=True,
    )
    late_redemption_allowed_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Before this date, "late redemptions" will be allowed. If empty, late redemptions are disallowed.',
    )
    per_learner_enrollment_limit = models.IntegerField(
        null=True,
        blank=True,
        default=None,
        verbose_name='Per-learner enrollment limit',
        help_text=(
            'The maximum number of enrollments allowed for a single learner under this policy. '
            'Null value means no limit is set, which disables this feature. '
            'Required if policy_type = "PerLearnerEnrollmentCreditAccessPolicy".'
        ),
    )
    per_learner_spend_limit = models.IntegerField(
        null=True,
        blank=True,
        default=None,
        verbose_name='Per-learner spend limit (USD cents)',
        help_text=(
            'The maximum amount of allowed money spent for a single learner under this policy. '
            'Denoted in USD cents. '
            'Null value means no limit is set, which disables this feature. '
            'Required if policy_type = "PerLearnerSpendCreditAccessPolicy".'
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Store the initial value of retired
        self._original_retired = self.retired

    # Customized version of HistoricalRecords to enable history tracking on child proxy models.  See
    # ProxyAwareHistoricalRecords docstring for more info.
    history = ProxyAwareHistoricalRecords(inherit=True)

    @classmethod
    def policies_with_redemption_enabled(cls):
        """
        Return all policies which have redemption enabled.
        """
        return cls.objects.filter(
            active=True,
            retired=False,
        )

    @property
    def is_redemption_enabled(self):
        """
        Return True if this policy has redemption enabled.
        """
        return self.active and not self.retired

    @property
    def is_late_redemption_allowed(self):
        """
        Return True if late redemption is currently allowed.
        """
        if not self.late_redemption_allowed_until:
            return False
        return localized_utcnow() < self.late_redemption_allowed_until

    @property
    def subsidy_active_datetime(self):
        """
        The datetime when this policy's associated subsidy is considered active.
        If null, this subsidy is considered active.
        """
        return self.subsidy_record().get('active_datetime')

    @property
    def subsidy_expiration_datetime(self):
        """
        The datetime when this policy's associated subsidy is considered expired.
        If null, this subsidy is considered active.
        """
        return self.subsidy_record().get('expiration_datetime')

    @property
    def is_subsidy_active(self):
        """
        Returns true if the related subsidy record is still active.
        """
        return self.subsidy_record().get('is_active')

    @property
    def subsidy_client(self):
        """
        An EnterpriseSubsidyAPIClient instance.
        """
        return get_versioned_subsidy_client()

    @property
    def lms_api_client(self):
        """
        An LmsApiClient instance.
        """
        return LmsApiClient()

    @classmethod
    def get_policy_class_by_type(cls, policy_type):
        """
        Given a policy_type str, return the appropriate subclass of SubsidyAccessPolicy.
        """
        for policy_class in SubsidyAccessPolicy.__subclasses__():
            if policy_type == policy_class.__name__:
                return policy_class
        return None

    @property
    def total_spend_limits_for_subsidy(self):
        """
        Sum of spend_limit for all policies associated with this policy's subsidy.

        Calculation is based on what the sum would be if this instance was saved to the DB.
        """
        sibling_policies_sum = SubsidyAccessPolicy.objects.filter(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            subsidy_uuid=self.subsidy_uuid,
            active=True,
        ).exclude(
            # Exclude self from the DB query because that value might be outdated.
            uuid=self.uuid,
        ).aggregate(
            models.Sum("spend_limit", default=0),
        )["spend_limit__sum"]

        # Re-add self.spend_limit which likely is more up-to-date compared to the DB value.
        self_spend_limit = self.spend_limit if self.active and self.spend_limit else 0
        return self_spend_limit + sibling_policies_sum

    @property
    def is_spend_limit_updated(self):
        """
        Checks if SubsidyAccessPolicy object exists in the database, and determines if the
        database value of spend_limit differs from the current instance of spend_limit
        """
        if self._state.adding:
            return False
        record_from_db = SubsidyAccessPolicy.objects.get(uuid=self.uuid)
        return record_from_db.spend_limit != self.spend_limit

    @property
    def is_active_updated(self):
        """
        Checks if SubsidyAccessPolicy object exists in the database, and determines if the
        database value of active flag differs from the current instance of active
        """
        if self._state.adding:
            return False
        record_from_db = SubsidyAccessPolicy.objects.get(uuid=self.uuid)
        return record_from_db.active != self.active

    @property
    def is_assignable(self):
        """
        Convenience property to determine if this policy is assignable.
        """
        return self.access_method == AccessMethods.ASSIGNED

    @property
    def bnr_enabled(self):
        """
        Returns True if learner_credit_request_config exists and is active, otherwise False.
        """
        return bool(self.learner_credit_request_config and self.learner_credit_request_config.active)

    @classmethod
    def has_bnr_enabled_policy_for_enterprise(cls, enterprise_customer_uuid):
        """
        Check if any active SubsidyAccessPolicy for the given enterprise_customer_uuid has bnr_enabled.

        Args:
            enterprise_customer_uuid (UUID): The UUID of the enterprise customer.

        Returns:
            bool: True if bnr_enabled is True for any active policy, otherwise False.
        """
        return cls.objects.filter(
            enterprise_customer_uuid=enterprise_customer_uuid,
            active=True,
            learner_credit_request_config__isnull=False,
            learner_credit_request_config__active=True
        ).exists()

    def clean_spend_limit(self):
        if self.active and (self.is_active_updated or self.is_spend_limit_updated):
            if self.total_spend_limits_for_subsidy > self.total_deposits_for_subsidy:
                sum_of_spend_limits_str = cents_to_usd_string(
                    self.total_spend_limits_for_subsidy
                )
                sum_of_deposits_str = cents_to_usd_string(self.total_deposits_for_subsidy)
                raise ValidationError(
                    f'{self} {VALIDATION_ERROR_SPEND_LIMIT_EXCEEDS_STARTING_BALANCE} '
                    f'Error: {sum_of_spend_limits_str} is greater than {sum_of_deposits_str}'
                )

    def clean(self):
        """
        Used to help validate field values before saving this model instance.
        """
        validation_errors = {}

        # Validate the spend_limit field.
        try:
            self.clean_spend_limit()
        except ValidationError as exc:
            validation_errors['spend_limit'] = str(exc)

        # Validate that budgets with spend cannot be deactivated
        if not self._state.adding:
            validate_budget_deactivation_with_spend(self)

        # Perform basic field constraint checks.
        for field_name, (constraint_function, error_message) in self.FIELD_CONSTRAINTS.items():
            field = getattr(self, field_name)
            if not constraint_function(field):
                validation_errors[field_name] = f'{self} {error_message}'

        if validation_errors:
            raise ValidationError(validation_errors)

    def save(self, *args, **kwargs):
        """
        Override to persist policy type.
        """
        if type(self).__name__ == SubsidyAccessPolicy.__name__:
            # it doesn't make sense to create an object of SubsidyAccessPolicy
            # because it is not a concrete policy
            raise TypeError("Can not create object of class SubsidyAccessPolicy")

        # Update retired_at based on changes to retired
        if self.retired != self._original_retired:
            self.retired_at = timezone.now() if self.retired else None
            self._original_retired = self.retired

        self.policy_type = type(self).__name__
        self.full_clean()
        super().save(*args, **kwargs)

    def __new__(cls, *args, **kwargs):
        """
        Override to create object of correct policy type.
        """
        # Implementation is taken from https://stackoverflow.com/a/60894618
        proxy_class = cls
        try:
            # get proxy name, either from kwargs or from args
            policy_type = kwargs.get(cls.POLICY_FIELD_NAME)
            if policy_type is None:
                policy_name_field_index = cls._meta.fields.index(
                    cls._meta.get_field(cls.POLICY_FIELD_NAME)
                )
                policy_type = args[policy_name_field_index]
            # get proxy class, by name, from current module
            proxy_class = getattr(sys.modules[__name__], policy_type)

        except Exception:  # pylint: disable=broad-except
            pass

        return super().__new__(proxy_class)

    def subsidy_record(self):
        """
        Retrieve this policy's corresponding subsidy record
        """
        cache_key = versioned_cache_key(
            'get_subsidy_record',
            self.enterprise_customer_uuid,
            self.subsidy_uuid,
        )
        cached_response = request_cache(namespace=REQUEST_CACHE_NAMESPACE).get_cached_response(cache_key)
        if cached_response.is_found:
            return cached_response.value

        try:
            result = self.subsidy_client.retrieve_subsidy(subsidy_uuid=self.subsidy_uuid)
        except requests.exceptions.HTTPError as exc:
            logger.warning('SubsidyAccessPolicy.subsidy_record() raised HTTPError: %s', exc)
            result = {}

        request_cache(namespace=REQUEST_CACHE_NAMESPACE).set(cache_key, result)

        return result

    def subsidy_record_from_tiered_cache(self, *cache_key_args):
        """
        Retrieve this policy's corresponding subsidy record from TieredCache.
        Should only be used in contexts that are ok with reading slow-moving,
        possibly stale fields.
        """
        cached_value = get_tiered_cache_subsidy_record(self.subsidy_uuid, *cache_key_args)
        if cached_value is not CACHE_MISS:
            return cached_value
        record = self.subsidy_record()
        set_tiered_cache_subsidy_record(record, *cache_key_args)
        return record

    def enterprise_user_record(self, lms_user_id):
        """
        Returns the enterprise_user_record.
        Retrieves it from TieredCache if available, otherwise, it will retrieve and initialize the cache.
        """
        enterprise_user_record = get_and_cache_enterprise_learner_record(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            learner_id=lms_user_id,
        )
        return enterprise_user_record

    def subsidy_balance(self):
        """
        Returns total remaining balance for the associated subsidy ledger.
        """
        current_balance = self.subsidy_record().get('current_balance') or 0
        return int(current_balance)

    @property
    def total_deposits_for_subsidy(self):
        """
        Returns total amount deposited into the associated subsidy ledger.
        """
        total_deposits = self.subsidy_record().get('total_deposits') or 0
        return int(total_deposits)

    @property
    def spend_available(self):
        """
        Policy-wide spend available.  This takes only policy-wide limits into account (no per-learner or other
        custom parameters) and is used in the enterprise admin dashboard to help summarize the high-level status of a
        policy.

        Returns:
            int: quantity >= 0 of USD Cents representing the policy-wide spend available.

        Raises:
            requests.exceptions.HTTPError if the request to Subsidy API (to fetch aggregates) fails.
        """
        # This is how much available spend the policy limit would allow, ignoring the subsidy balance.
        if self.spend_limit is not None:
            # total_redeemed is negative
            policy_limit_balance = max(0, self.spend_limit + self.total_redeemed)
            # Finally, take both the policy-wide limit and the subsidy balance into account:
            return min(policy_limit_balance, self.subsidy_balance())
        else:
            # Take ONLY the subsidy balance into account:
            return self.subsidy_balance()

    @property
    def total_redeemed(self):
        """
        Total amount already transacted via this policy.

        Returns:
            int: quantity <= 0 of USD Cents.

        Raises:
            requests.exceptions.HTTPError if the request to Subsidy API (to fetch aggregates) fails.
        """
        return self.aggregates_for_policy().get('total_quantity') or 0

    @property
    def total_allocated(self):
        """
        Total amount of assignments currently allocated via this policy.

        Override this in sub-classes that use assignments.  Empty definition needed here in order to make serializer
        happy.

        Returns:
            int: negative USD cents representing the total amount of currently allocated assignments.
        """
        return 0

    def catalog_contains_content_key(self, content_key):
        """
        Returns a boolean indicating if the given content_key
        is part of this policy's catalog.
        """
        return get_and_cache_catalog_contains_content(
            self.catalog_uuid,
            content_key,
        )

    def get_content_metadata(self, content_key):
        """
        Returns a dict of content metadata for the given key.
        """
        return get_and_cache_content_metadata(self.enterprise_customer_uuid, content_key)

    def get_content_price(self, content_key, content_metadata=None):
        """
        Returns the price for some content key, as told by the enterprise-subsidy service.

        Returns: The price (in USD cents) for the given content key.
        Raises: UnredeemableContentException if the price is null.
        """
        if not content_metadata:
            content_metadata = self.get_content_metadata(content_key)
        content_price = content_metadata['content_price']
        if content_price is None:
            raise ContentPriceNullException(f'The price for {content_key} is null')
        return content_price

    def get_list_price(self, lms_user_id, content_key):  # pylint: disable=unused-argument
        """
        Determine the price for content for display purposes only.
        We likely have content metadata prefetched on this policy record instance at the time
        of invocation, so we do that prefetch here via ``self.get_content_metadata()``.
        """
        return get_list_price_for_content(
            self.enterprise_customer_uuid,
            content_key,
            self.get_content_metadata(content_key),
        )

    def includes_learner(self, lms_user_id):
        """
        Determine whether the lms user is associated properly with both the enterprise
        and the policy's group(s).
        """
        learner_record = self.enterprise_user_record(lms_user_id)
        if not learner_record:
            return False, REASON_LEARNER_NOT_IN_ENTERPRISE

        associated_group_uuids = set(learner_record.get('enterprise_group', []))
        # if there are no policy groups, return early
        if not PolicyGroupAssociation.objects.filter(subsidy_access_policy=self).exists():
            return True, None

        # if no association for this learner's group(s), return false
        if not PolicyGroupAssociation.objects.filter(
            subsidy_access_policy=self,
            enterprise_group_uuid__in=associated_group_uuids,
        ).exists():
            return False, REASON_LEARNER_NOT_IN_ENTERPRISE_GROUP

        # otherwise, return true
        return True, None

    def aggregates_for_policy(self):
        """
        Returns aggregate transaction data for this policy. The result is cached via ``RequestCache``
        for other use in the scope of a single request.

        Raises:
            requests.exceptions.HTTPError if the request to Subsidy API fails.
        """
        _cache = request_cache(namespace=REQUEST_CACHE_NAMESPACE)
        cache_key = versioned_cache_key('aggregates_for_policy', self.subsidy_uuid, self.uuid)

        cached_response = _cache.get_cached_response(cache_key)
        if cached_response.is_found:
            logger.info(
                f'aggregates_for_policy cache hit: subsidy {self.subsidy_uuid}, policy {self.uuid}'
            )
            return cached_response.value

        response_payload = self.subsidy_client.list_subsidy_transactions(
            subsidy_uuid=self.subsidy_uuid,
            subsidy_access_policy_uuid=self.uuid,
        )
        result = response_payload['aggregates']
        logger.info(
            f'aggregates_for_policy cache miss: subsidy {self.subsidy_uuid}, policy {self.uuid}'
        )
        _cache.set(cache_key, result)
        return result

    def transactions_for_learner(self, lms_user_id):
        """
        Returns a request-cached version of all transactions and aggregate quantities
        for this learner and this policy.
        """
        subsidy_transactions = get_and_cache_transactions_for_learner(
            self.subsidy_uuid, lms_user_id
        )['transactions']
        policy_transactions = [
            transaction for transaction in subsidy_transactions
            if str(transaction['subsidy_access_policy_uuid']) == str(self.uuid)
        ]
        policy_aggregates = {
            'total_quantity': sum(tx['quantity'] for tx in policy_transactions),
        }
        return {
            'transactions': policy_transactions,
            'aggregates': policy_aggregates,
        }

    def transactions_for_learner_and_content(self, lms_user_id, content_key):
        """
        Return a dictionary that contains a list of transactions
        and aggregates describing those transactions
        for the given ``lms_user_id`` and ``content_key``.
        """
        response_payload = self.subsidy_client.list_subsidy_transactions(
            subsidy_uuid=self.subsidy_uuid,
            lms_user_id=lms_user_id,
            content_key=content_key,
            subsidy_access_policy_uuid=self.uuid,
        )
        return {
            'transactions': response_payload['results'],
            'aggregates': response_payload['aggregates'],
        }

    @staticmethod
    def content_would_exceed_limit(spent_amount, limit_to_check, content_price):
        """
        Returns true if redeeming for this content price would exceed
        the given ``limit_to_check`` after taking into account the amount already
        spent.  ``spent_amount`` is assumed to be an integer <= 0 and ``content_price``
        is assumed to be an integer >= 0.
        """
        if spent_amount > 0:
            raise Exception('Expected a sum of transaction quantities <= 0')

        positive_spent_amount = spent_amount * -1
        return (positive_spent_amount + content_price) > limit_to_check

    def will_exceed_spend_limit(self, content_key, content_metadata=None):
        """
        Returns true if redeeming this course would exceed
        the ``spend_limit`` set by this policy.
        """
        if self.spend_limit is None:
            return False

        content_price = self.get_content_price(content_key, content_metadata=content_metadata)

        return self.content_would_exceed_limit(self.total_redeemed, self.spend_limit, content_price)

    def _log_redeemability(self, is_redeemable, reason, lms_user_id, content_key, extra=None):
        """
        Helper to log decision points in the can_redeem() function.
        """
        message = (
            '[POLICY REDEEMABILITY]: policy: %s, is_redeemable: %s, reason: %s '
            'lms_user_id: %s, content_key: %s, extra=%s'
        )
        logger.info(message, self.uuid, is_redeemable, reason, lms_user_id, content_key, extra)

    def can_approve(self, content_key, content_price_cents):
        """
        Determines if a request with the given content_key and content_price_cents
        can be approved under this policy.

        Returns a tuple of (bool, str):
        - bool: True if the request can be approved, False otherwise.
        - str: A reason code if the request cannot be approved, or an empty string if it can.
        """
        # Since we are treating assignments as approved requests, can_allocate would give us the same result.
        if not self.bnr_enabled:
            return False, REASON_BNR_NOT_ENABLED
        return self.assignment_request_can_allocate(content_key, content_price_cents)

    def approve(self, learner_email, content_key, content_price_cents, lms_user_id):
        """
        Approves a learner credit request for the given learner_email and content_key.
        This method allocates an assignment for the learner to be linked with the request.
        If the allocation fails, it logs an error and returns None.
        If the allocation is successful, it returns the created assignment.

        Params:
          learner_email: Email of the learner for whom the request is being approved.
          content_key: Course key of the requested content.
          content_price_cents: A **non-negative** integer reflecting the current price of the content in USD cents.
          lms_user_id: The LMS user ID of the learner.
        """
        # To approve a learner credit request, we need to allocate an assignment and link it to the request.
        assignment = assignments_api.allocate_assignment_for_request(
            self.assignment_configuration,
            learner_email,
            content_key,
            content_price_cents,
            lms_user_id,
        )

        if not assignment:
            error_msg = (
                f"Failed to create for learner {learner_email} "
                f"and content {content_key}."
            )
            logger.error(f"[LC REQUEST APPROVAL] {error_msg}")
            return None
        return assignment

    def can_redeem(
        self, lms_user_id, content_key,
        skip_customer_user_check=False, skip_enrollment_deadline_check=False,
        **kwargs,
    ):
        """
        Check that a given learner can redeem the given content.
        The ordering of each conditional is intentional based on an expected
        error message to be shown based on the learners state at the time of
        accessing the CourseAbout page in FE-app-learner-portal focusing on the
        user state, catalog state based on content key, then subsidy/policy state
        based on whether they are active and have spend available for the requested
        content.

        Returns:
            3-tuple of (bool, str, list of dict):
                * first element is true if the learner can redeem the content,
                * second element contains a reason code if the content is not redeemable,
                * third a list of any transactions representing existing redemptions (any state).
        """
        logger.info(
            f'[POLICY REDEEMABILITY] Checking for policy: {self.uuid}, '
            f'lms_user_id: {lms_user_id}, content_key: {content_key}'
        )
        # inactive policy
        if not self.is_redemption_enabled:
            self._log_redeemability(False, REASON_POLICY_EXPIRED, lms_user_id, content_key)
            return (False, REASON_POLICY_EXPIRED, [])

        # learner not associated to enterprise
        if not skip_customer_user_check:
            included_in_policy, reason = self.includes_learner(lms_user_id)
            if not included_in_policy:
                self._log_redeemability(False, reason, lms_user_id, content_key)
                return (False, reason, [])

        # no content key in catalog
        if not self.catalog_contains_content_key(content_key):
            self._log_redeemability(False, REASON_CONTENT_NOT_IN_CATALOG, lms_user_id, content_key)
            return (False, REASON_CONTENT_NOT_IN_CATALOG, [])

        # Wait to fetch content metadata with a call to the enterprise-subsidy
        # service until we *know* that we'll need it.
        content_metadata = self.get_content_metadata(content_key)

        # no content key in content metadata
        if not content_metadata:
            self._log_redeemability(False, REASON_CONTENT_NOT_IN_CATALOG, lms_user_id, content_key)
            return (False, REASON_CONTENT_NOT_IN_CATALOG, [])

        # Check if the current time is beyond the enrollment deadline for the content,
        # but only if late redemption is *not* currently allowed.
        if not skip_enrollment_deadline_check and not self.is_late_redemption_allowed:
            enrollment_deadline = enroll_by_datetime(content_metadata)
            if enrollment_deadline and (timezone.now() > enrollment_deadline):
                self._log_redeemability(False, REASON_BEYOND_ENROLLMENT_DEADLINE, lms_user_id, content_key)
                return (False, REASON_BEYOND_ENROLLMENT_DEADLINE, [])

        # We want to wait to do these checks that might require a call
        # to the enterprise-subsidy service until we *know* we'll need the data.
        subsidy_can_redeem_payload = self.subsidy_client.can_redeem(
            self.subsidy_uuid,
            lms_user_id,
            content_key,
        )

        # Refers to a computed property of an EnterpriseSubsidy record
        # that takes into account the start/expiration dates of the subsidy record.
        active_subsidy = subsidy_can_redeem_payload.get('active', False)
        existing_transactions = subsidy_can_redeem_payload.get('all_transactions', [])

        # inactive subsidy?
        if not active_subsidy:
            self._log_redeemability(False, REASON_SUBSIDY_EXPIRED, lms_user_id, content_key)
            return (False, REASON_SUBSIDY_EXPIRED, [])

        # can_redeem false from subsidy
        if not subsidy_can_redeem_payload.get('can_redeem', False):
            self._log_redeemability(
                False, REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY, lms_user_id, content_key, extra=existing_transactions,
            )
            return (False, REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY, existing_transactions)

        # not enough funds on policy
        if self.will_exceed_spend_limit(content_key, content_metadata=content_metadata):
            self._log_redeemability(
                False, REASON_POLICY_SPEND_LIMIT_REACHED, lms_user_id, content_key, extra=existing_transactions
            )
            return (False, REASON_POLICY_SPEND_LIMIT_REACHED, existing_transactions)

        self._log_redeemability(True, None, lms_user_id, content_key)
        return (True, None, existing_transactions)

    def has_credit_available_with_spend_limit(self):
        """
        Determines whether a subsidy access policy has yet exceeded its configured
        `spend_limit` based on the total value of transactions redeemed against the policy.
        """
        # No policy-wide spend_limit set, so credit is available.
        if self.spend_limit is None:
            return True

        # Verify that spend against the policy has not exceeded the spend limit.
        spent_amount = self.aggregates_for_policy().get('total_quantity') or 0
        if spent_amount > 0:
            raise Exception('[SubsidyAccessPolicy.credit_available] Expected a sum of transaction quantities <= 0')
        positive_spent_amount = spent_amount * -1
        if positive_spent_amount >= self.spend_limit:
            return False

        return True

    def credit_available(
            self,
            lms_user_id,
            skip_customer_user_check=False,
            skip_inactive_subsidy_check=False,
    ):
        """
        Perform generic checks to determine if a learner has credit available for a given
        subsidy access policy. The generic checks performed include:
            * Whether the policy is active.
            * Whether the learner is associated to the enterprise.
            * Whether the learner is associated to the policy's group.
            * Whether the subsidy is active (non-expired).
            * Whether the subsidy has remaining balance.
            * Whether the transactions associated with policy have exceeded the policy-wide spend limit.
        """
        # inactive policy
        if not self.is_redemption_enabled:
            logger.info(f'[credit_available] policy {self.uuid} inactive', self.uuid)
            return False

        # learner not linked to enterprise
        if not skip_customer_user_check:
            included_in_policy, reason = self.includes_learner(lms_user_id)
            if not included_in_policy:
                logger.info(f'[credit_available] learner {lms_user_id} encountered error {reason}')
                return False

        # verify associated subsidy is current (non-expired)
        try:
            if not skip_inactive_subsidy_check and not self.is_subsidy_active:
                logger.info('[credit_available] SubsidyAccessPolicy.subsidy_record() returned inactive subsidy')
                return False
        except requests.exceptions.HTTPError as exc:
            # when associated subsidy is soft-deleted, the subsidy retrieve API raises an exception.
            logger.info(f'[credit_available] SubsidyAccessPolicy.subsidy_record() raised HTTPError: {exc}')
            return False

        # verify associated subsidy has remaining balance
        if self.subsidy_balance() <= 0:
            logger.info('[credit_available] SubsidyAccessPolicy.subsidy_record() returned empty balance')
            return False

        # verify spend against policy and configured spend limit
        if not self.has_credit_available_with_spend_limit():
            logger.info(f'[credit_available] policy {self.uuid} has exceeded spend limit')
            return False

        return True

    def _redemptions_for_idempotency_key(self, all_transactions):
        """
        Select the historical redemptions (transactions) that may contribute to the idempotency key.

        Currently, only failed or reversed transactions qualify, allowing us to re-attempt a previously failed or
        reversed transaction for that same (subsidy, policy, learner, content) combination.  In all other cases, the
        same idempotency_key as the last redeem attempt will be generated, which results in redeem() returning an
        existing transaction instead of a new one.

        Reasons for qualifying scenarios:

        * Failed transaction:
          * This is a terminal state, so there's nothing more acting on this particular redemption request.  This does
            not imply redemption cannot be retried, so it qualifies as a versioning event for the idempotency_key.
        * Reversed transaction:
          * A prior attempt to redeem has been reversed, inactivating it.  Similarly to the failed transaction case,
            this is a terminal state which should allow for a new idempotency_key to be generated and redemption to be
            retried.

        Reasons for non-qualifying scenarios:

        * Committed transaction without reversal:
          * There should be at most one of these, and it represents an active redemption which we just want subsequent
            redeem() calls to return as-is without creating a new one.
        * Created/pending transaction:
          * If any of these exist, prior call(s) to redeem must have returned asynchronously.  There's obviously
            something already brewing in the background, so lets not add fuel to the fire by allowing the creation of
            yet another redemption attempt.

        Returns:
            list of str: Transaction UUIDs which should cause the idempotency_key to change.
        """
        return [
            transaction['uuid'] for transaction in all_transactions
            if transaction['state'] == TransactionStateChoices.FAILED or (
                isinstance(transaction['reversal'], dict) and
                transaction['reversal'].get('state') == TransactionStateChoices.COMMITTED
            )
        ]

    def redeem(self, lms_user_id, content_key, all_transactions, metadata=None, **kwargs):
        """
        Redeem a subsidy for the given learner and content.

        Returns:
            A ledger transaction.

        Raises:
            SubsidyAPIHTTPError if the Subsidy API request failed.
            ValueError if the access method of this policy is invalid.
        """
        if self.access_method in (AccessMethods.DIRECT, AccessMethods.ASSIGNED):
            idempotency_key = create_idempotency_key_for_transaction(
                subsidy_uuid=str(self.subsidy_uuid),
                lms_user_id=lms_user_id,
                content_key=content_key,
                subsidy_access_policy_uuid=str(self.uuid),
                historical_redemptions_uuids=self._redemptions_for_idempotency_key(all_transactions),
            )
            # If this policy has late redemptions currently enabled, tell that to the subsidy service.
            metadata_for_tx = metadata
            if self.is_late_redemption_allowed:
                metadata_for_tx = metadata.copy() if metadata else {}
                metadata_for_tx[ALLOW_LATE_ENROLLMENT_KEY] = True
            try:
                creation_payload = {
                    'subsidy_uuid': str(self.subsidy_uuid),
                    'lms_user_id': lms_user_id,
                    'content_key': content_key,
                    'subsidy_access_policy_uuid': str(self.uuid),
                    'metadata': metadata_for_tx,
                    'idempotency_key': idempotency_key,
                }
                requested_price_cents = kwargs.get('requested_price_cents')
                if requested_price_cents is not None:
                    creation_payload['requested_price_cents'] = requested_price_cents
                return self.subsidy_client.create_subsidy_transaction(**creation_payload)
            except requests.exceptions.HTTPError as exc:
                raise SubsidyAPIHTTPError('HTTPError occurred in Subsidy API request.') from exc
        else:
            raise ValueError(f"unknown access method {self.access_method}")

    def has_redeemed(self, lms_user_id, content_key):
        """
        Check if any existing transactions are present in the subsidy
        for the given lms_user_id and content_key.
        """
        if self.access_method == AccessMethods.DIRECT:
            return bool(self.transactions_for_learner_and_content(lms_user_id, content_key)['transactions'])
        else:
            raise ValueError(f"unknown access method {self.access_method}")

    def redemptions(self, lms_user_id, content_key):
        """
        Returns any existing transactions the policy's subsidy
        that are associated with the given lms_user_id and content_key.
        """
        if self.access_method == AccessMethods.DIRECT:
            return self.transactions_for_learner_and_content(lms_user_id, content_key)['transactions']
        else:
            raise ValueError(f"unknown access method {self.access_method}")

    def lock_resource_key(self, lms_user_id=None, content_key=None) -> str:
        """
        Get a string that can be used as a cache key representing the resource being locked.

        Returns:
            str: deterministic hash digest based on policy ID and other optional keys.
        """
        cache_key_inputs = {
            "resource": POLICY_LOCK_RESOURCE_NAME,
            "uuid": self.uuid,
        }
        cache_key_inputs.update({"lms_user_id": lms_user_id} if lms_user_id else {})
        cache_key_inputs.update({"content_key": content_key} if content_key else {})
        return get_cache_key(**cache_key_inputs)

    def acquire_lock(self, lms_user_id=None, content_key=None) -> str:
        """
        Acquire an exclusive lock on this SubsidyAccessPolicy instance.

        Memcached devs recommend using add() for locking instead of get()+set(), which rules out TieredCache which only
        exposes get()+set() from django cache.  See: https://github.com/memcached/memcached/issues/163

        Returns:
            str: lock ID if a lock was successfully acquired, None otherwise.
        """
        lock_id = str(uuid4())
        if django_cache.add(self.lock_resource_key(lms_user_id, content_key), lock_id):
            return lock_id
        else:
            return None

    def release_lock(self, lms_user_id=None, content_key=None) -> None:
        """
        Release an exclusive lock on this SubsidyAccessPolicy instance.
        """
        django_cache.delete(self.lock_resource_key(lms_user_id, content_key))

    @contextmanager
    def lock(self, lms_user_id=None, content_key=None):
        """
        Context manager for locking this SubsidyAccessPolicy instance.

        Raises:
            SubsidyAccessPolicyLockAttemptFailed:
                Raises this if there's another distributed process locking this SubsidyAccessPolicy.
        """
        lock_id = self.acquire_lock(lms_user_id, content_key)
        if not lock_id:
            raise SubsidyAccessPolicyLockAttemptFailed(
                f"Failed to acquire lock on SubsidyAccessPolicy {self} with lms_user_id={lms_user_id}, "
                f"content_key={content_key}."
            )
        try:
            yield lock_id
        finally:
            self.release_lock(lms_user_id, content_key)

    def create_deposit(
        self,
        desired_deposit_quantity,
        sales_contract_reference_id,
        sales_contract_reference_provider,
        metadata=None,
    ):
        """
        Create a Deposit for the associated Subsidy and update this Policy's spend_limit.

        Alternatively, this is referred to as a "Top-Up".

        Raises:
            SubsidyAPIHTTPError if the Subsidy API request failed.
        """
        deposit_kwargs = {
            "subsidy_uuid": self.subsidy_uuid,
            "desired_deposit_quantity": desired_deposit_quantity,
            "sales_contract_reference_id": sales_contract_reference_id,
            "sales_contract_reference_provider": sales_contract_reference_provider,
            "metadata": metadata,
        }
        logger.info("Attempting deposit creation with arguments %s", deposit_kwargs)
        try:
            self.subsidy_client.create_subsidy_deposit(**deposit_kwargs)
        except requests.exceptions.HTTPError as exc:
            logger.exception("Deposit creation request failed, skipping updating policy spend_limit.")
            raise SubsidyAPIHTTPError() from exc
        self.spend_limit += desired_deposit_quantity
        self.save()

    def delete(self, *args, **kwargs):
        """
        Perform a soft-delete, overriding the standard delete() method to prevent hard-deletes.

        If this instance was already soft-deleted, invoking delete() is a no-op.
        """
        if self.active:
            if 'reason' in kwargs and kwargs['reason']:
                self._change_reason = kwargs['reason']  # pylint: disable=attribute-defined-outside-init
            self.active = False
            self.save()

    def __str__(self):
        return f'<{self.__class__.__name__} uuid={self.uuid}>'


class CreditPolicyMixin:
    """
    Mixin class for credit type policies.
    """

    @property
    def priority(self):
        return CREDIT_POLICY_TYPE_PRIORITY


class AssignedCreditPolicyMixin:
    """
    Mixin class for assigned credit type policies.
    """

    @property
    def priority(self):
        return ASSIGNED_CREDIT_POLICY_TYPE_PRIORITY


class PerLearnerEnrollmentCreditAccessPolicy(CreditPolicyMixin, SubsidyAccessPolicy):
    """
    Policy that limits the number of enrollments transactions for a learner in a subsidy.

    .. no_pii: This model has no PII
    """
    objects = PolicyManager()

    # Policies of this type *must not* define a per-learner spend limit or an assignment configuration
    FIELD_CONSTRAINTS = {
        'per_learner_spend_limit': (is_none, 'must not define a per-learner spend limit.'),
        'assignment_configuration': (is_none, 'must not relate to an AssignmentConfiguration.'),
    }

    class Meta:
        """
        Metaclass for PerLearnerEnrollmentCreditAccessPolicy.
        """
        proxy = True

    def can_redeem(
        self, lms_user_id, content_key,
        skip_customer_user_check=False, skip_enrollment_deadline_check=False,
        **kwargs,
    ):
        """
        Checks if the given lms_user_id has a number of existing subsidy transactions
        LTE to the learner enrollment cap declared by this policy.
        """
        # perform generic access checks
        should_attempt_redemption, reason, existing_redemptions = \
            super().can_redeem(
                lms_user_id, content_key,
                skip_customer_user_check, skip_enrollment_deadline_check,
                **kwargs,
            )
        if not should_attempt_redemption:
            return (False, reason, existing_redemptions)

        has_per_learner_enrollment_limit = self.per_learner_enrollment_limit is not None
        if has_per_learner_enrollment_limit:
            # only retrieve transactions if there is a per-learner enrollment limit
            learner_transactions_count = len(self.transactions_for_learner(lms_user_id)['transactions'])
            # check whether learner exceeded the per-learner enrollment limit
            if learner_transactions_count >= self.per_learner_enrollment_limit:
                return (False, REASON_LEARNER_MAX_ENROLLMENTS_REACHED, existing_redemptions)

        # learner can redeem the subsidy access policy
        return (True, None, existing_redemptions)

    def credit_available(self, lms_user_id, *args, **kwargs):
        """
        Determine whether a learner has credit available for the subsidy access policy.
        """
        is_credit_available = super().credit_available(lms_user_id)
        if not is_credit_available:
            return False

        if self.per_learner_enrollment_limit is None:
            return True

        # Validate whether learner has enough remaining balance (enrollments) for this policy.
        remaining_balance_per_user = self.remaining_balance_per_user(lms_user_id)
        return (remaining_balance_per_user is not None) and remaining_balance_per_user > 0

    def remaining_balance_per_user(self, lms_user_id):
        """
        Returns the remaining redeemable credit for the user.
        Returns None if `per_learner_enrollment_limit` is not set.
        """
        if self.per_learner_enrollment_limit is None:
            return None
        if self.per_learner_enrollment_limit <= 0:
            return 0
        existing_transaction_count = len(self.transactions_for_learner(lms_user_id)['transactions'])
        return self.per_learner_enrollment_limit - existing_transaction_count


class SubsidyAccessPolicyRequestAssignmentMixin:

    def copy_context_to(self, policy_class):
        """
        Creates a new instance of the specified policy class and copies
        essential context from this policy to the new instance.

        Args:
            policy_class: The policy class to instantiate

        Returns:
            An instance of policy_class with context copied from self
        """
        # Create new instance
        new_policy = policy_class()
        new_policy.policy_type = 'AssignedLearnerCreditAccessPolicy'

        # Copy essential context attributes
        for attr in ['uuid', 'enterprise_customer_uuid', 'catalog_uuid',
                     'subsidy_uuid', 'active', 'retired', 'spend_limit',
                     'assignment_configuration', 'late_redemption_allowed_until']:
            if hasattr(self, attr):
                setattr(new_policy, attr, getattr(self, attr))
            new_policy.retired_at = getattr(self, 'retired_at', None)

        return new_policy

    def assignment_request_can_redeem(self, lms_user_id, content_key, skip_customer_user_check=False,
                                      skip_enrollment_deadline_check=False, **kwargs
                                      ):
        policy_instance = self.copy_context_to(AssignedLearnerCreditAccessPolicy)
        return policy_instance.can_redeem(lms_user_id, content_key, skip_customer_user_check,
                                          skip_enrollment_deadline_check, **kwargs
                                          )

    def assignment_request_redeem(self, lms_user_id, content_key, all_transactions, metadata=None, **kwargs):

        learner_credit_request = kwargs.get('learner_credit_request')
        logger.info(
            "LearnerCreditRequestActions redeem record creation requested."
            "lms_user_id=%s, content_key=%s",
            lms_user_id,
            content_key
        )
        if self.bnr_enabled:
            try:
                policy_instance = self.copy_context_to(AssignedLearnerCreditAccessPolicy)
                found_assignment = policy_instance.get_assignment(lms_user_id, content_key)
                logger.info(
                    "Assignment lookup for redemption attempt: found=%s, lms_user_id=%s, content_key=%s",
                    bool(found_assignment),
                    lms_user_id,
                    content_key
                )
                learner_credit_request = found_assignment.credit_request if found_assignment else None
                logger.info(
                    "Creating LearnerCreditRequestActions record for redemption attempt. "
                    "learner_credit_request_uuid=%s, lms_user_id=%s, content_key=%s",
                    learner_credit_request.uuid,
                    lms_user_id,
                    content_key
                )
                action = LearnerCreditRequestActions.create_action(
                    learner_credit_request=learner_credit_request,
                    recent_action=get_action_choice(SubsidyRequestStates.ACCEPTED),
                    status=get_user_message_choice(SubsidyRequestStates.ACCEPTED),
                )
                result = policy_instance.redeem(lms_user_id, content_key, all_transactions, metadata=metadata, **kwargs)
                learner_credit_request.state = SubsidyRequestStates.ACCEPTED
                learner_credit_request.save()
                logger.info(
                    "Successfully redeemed content through assignment_request_redeem. "
                    "lms_user_id=%s, content_key=%s, transaction_uuid=%s",
                    lms_user_id,
                    content_key,
                    result.get('uuid', 'unknown')
                )
                return result
            except Exception as exc:
                logger.exception(
                    "Error redeeming content through assignment_request_redeem. "
                    "learner_credit_request_uuid=%s, lms_user_id=%s, content_key=%s",
                    learner_credit_request.uuid if learner_credit_request else None,
                    lms_user_id,
                    content_key
                )
                action.status = get_action_choice(SubsidyRequestStates.APPROVED)
                action.error_reason = get_error_reason_choice(LearnerCreditRequestActionErrorReasons.FAILED_REDEMPTION)
                action.traceback = format_traceback(exc)
                action.save()
                raise

    def assignment_request_can_allocate(self, content_key, content_price_cents):
        """
        Wrapper method to make requests that fall under a PerLearnerSpendCreditAccessPolicy work with assignments.
        """
        policy_instance = self.copy_context_to(AssignedLearnerCreditAccessPolicy)
        return policy_instance.can_allocate(1, content_key, content_price_cents)


class PerLearnerSpendCreditAccessPolicy(CreditPolicyMixin, SubsidyAccessPolicy,
                                        SubsidyAccessPolicyRequestAssignmentMixin
                                        ):
    """
    Policy that limits the amount of learner spend for enrollment transactions.

    .. no_pii: This model has no PII
    """
    objects = PolicyManager()

    # Policies of this type *must not* define a per-learner enrollment limit or an assignment configuration
    FIELD_CONSTRAINTS = {
        'per_learner_enrollment_limit': (is_none, 'must not define a per-learner enrollment limit.'),
    }

    class Meta:
        """
        Metaclass for PerLearnerSpendCreditAccessPolicy.
        """
        proxy = True

    def clean(self):
        """
        Validate that only one subsidy type can have B&R enabled at a time.
        """
        super().clean()

        # Only check for conflicts if this policy has an active learner credit request config
        if self.learner_credit_request_config and self.learner_credit_request_config.active:
            try:
                customer_config = (
                    SubsidyRequestCustomerConfiguration.objects.get(
                        enterprise_customer_uuid=self.enterprise_customer_uuid
                    )
                )
                if customer_config.subsidy_requests_enabled:
                    raise ValidationError(
                        f"Browse & Request is already enabled for {customer_config.subsidy_type} "
                        f"subsidy type for this enterprise. "
                        "Only one subsidy type can have Browse & Request enabled at a time."
                    )
            except SubsidyRequestCustomerConfiguration.DoesNotExist:
                pass

    def save(self, *args, **kwargs):
        """
        If Browse and Request is enabled and no ``assignment_configuration``
        is present, create one and associate it with this record.
        This step is necessary to make a PerLearnerSpendCreditAccessPolicy work with the
        assignment-based workflow.

        Lastly, ensure that the associated assignment_configuration has the
        same ``enterprise_customer_uuid`` as this policy record.
        """
        if self.bnr_enabled and not self.assignment_configuration:
            self.assignment_configuration = assignments_api.create_assignment_configuration(
                self.enterprise_customer_uuid
            )
        elif (self.assignment_configuration and
                self.enterprise_customer_uuid != self.assignment_configuration.enterprise_customer_uuid):
            self.assignment_configuration.enterprise_customer_uuid = self.enterprise_customer_uuid
            self.assignment_configuration.save()

        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def total_allocated(self):
        """
        Total amount of assignments currently allocated via this policy. The assignments have direct
        1-1 mapping to the requests, so in this case total_allocated represents total approved requests.

        Returns:
            int: Negative USD cents representing the total amount of currently
            allocated assignments / approved requests.
        """
        if not self.bnr_enabled:
            return super().total_allocated

        return assignments_api.get_allocated_quantity_for_configuration(
            self.assignment_configuration,
        )

    @property
    def spend_available(self):
        """
        Policy-wide spend available.  This sub-class definition additionally takes approved requests into account.

        Returns:
            int: quantity >= 0 of USD Cents representing the policy-wide spend available.
        """
        if not self.bnr_enabled:
            return super().spend_available

        # super().spend_available is POSITIVE USD Cents representing available spend ignoring assignments.
        # self.total_allocated is NEGATIVE USD Cents representing currently allocated assignments / approved requests.
        return max(0, super().spend_available + self.total_allocated)

    def can_redeem(
        self, lms_user_id, content_key, skip_customer_user_check=False, skip_enrollment_deadline_check=False,
            **kwargs
    ):
        """
        Determines whether learner can redeem a subsidy access policy given the
        limits specified on the policy.
        """
        if self.bnr_enabled:
            return self.assignment_request_can_redeem(
                lms_user_id, content_key, skip_customer_user_check, skip_enrollment_deadline_check, **kwargs
            )
        # perform generic access checks
        should_attempt_redemption, reason, existing_redemptions = super().can_redeem(
            lms_user_id,
            content_key,
            skip_customer_user_check,
            skip_enrollment_deadline_check,
            **kwargs,
        )
        if not should_attempt_redemption:
            self._log_redeemability(False, reason, lms_user_id, content_key, extra=existing_redemptions)
            return (False, reason, existing_redemptions)

        has_per_learner_spend_limit = self.per_learner_spend_limit is not None
        if has_per_learner_spend_limit:
            # only retrieve transactions if there is a per-learner spend limit
            existing_learner_transaction_aggregates = self.transactions_for_learner(lms_user_id)['aggregates']
            spent_amount = existing_learner_transaction_aggregates.get('total_quantity') or 0
            content_price = self.get_content_price(content_key)
            if self.content_would_exceed_limit(spent_amount, self.per_learner_spend_limit, content_price):
                self._log_redeemability(
                    False, REASON_LEARNER_MAX_SPEND_REACHED, lms_user_id, content_key,
                    extra=existing_redemptions,
                )
                return (False, REASON_LEARNER_MAX_SPEND_REACHED, existing_redemptions)

        # learner can redeem the subsidy access policy
        self._log_redeemability(True, None, lms_user_id, content_key)
        return (True, None, existing_redemptions)

    def redeem(self, lms_user_id, content_key, all_transactions, metadata=None, **kwargs):
        """
        Redeem a subsidy for the given learner and content.

        If bnr_enabled is True, calls assignment_request_redeem to use the assignment-based
        workflow, otherwise calls the parent redeem method.

        Returns:
            A ledger transaction.

        Raises:
            SubsidyAPIHTTPError if the Subsidy API request failed.
            ValueError if the access method of this policy is invalid.
        """
        if self.bnr_enabled:
            return self.assignment_request_redeem(lms_user_id, content_key, all_transactions, metadata=metadata,
                                                  **kwargs
                                                  )
        return super().redeem(lms_user_id, content_key, all_transactions, metadata=metadata, **kwargs)

    def credit_available(self, lms_user_id, *args, **kwargs):
        """
        Determine whether a learner has credit available for the subsidy access policy.
        """
        is_credit_available = super().credit_available(lms_user_id)
        if not is_credit_available:
            return False

        if self.per_learner_spend_limit is None:
            return True

        # Validate whether learner has enough remaining balance (spend) for this policy.
        remaining_balance_per_user = self.remaining_balance_per_user(lms_user_id)

        if self.bnr_enabled and remaining_balance_per_user == 0:
            return True

        return (remaining_balance_per_user is not None) and remaining_balance_per_user > 0

    def remaining_balance_per_user(self, lms_user_id=None):
        """
        Returns the remaining redeemable credit for the user.
        Returns None if `per_learner_spend_limit` is not set.
        """
        if self.per_learner_spend_limit is None:
            return None
        if self.per_learner_spend_limit <= 0:
            return 0
        spent_amount = self.transactions_for_learner(lms_user_id)['aggregates'].get('total_quantity') or 0
        if spent_amount > 0:
            raise Exception('[remaining_balance_per_user] Expected a sum of transaction quantities <= 0')
        positive_spent_amount = spent_amount * -1
        return self.per_learner_spend_limit - positive_spent_amount


class AssignedLearnerCreditAccessPolicy(AssignedCreditPolicyMixin, SubsidyAccessPolicy):
    """
    Policy based on LearnerContentAssignments, backed by a learner credit type of subsidy.

    .. no_pii: This model has no PII
    """
    objects = PolicyManager()

    # Policies of this type *must* define a spend_limit.
    # Policies of this type *must not* define either of the per-learner limits.
    # Note that the save() method of this model enforces the existence of an assignment_configuration.
    FIELD_CONSTRAINTS = {
        'spend_limit': (is_not_none, 'must define a spend_limit.'),
        'per_learner_spend_limit': (is_none, 'must not define a per-learner spend limit.'),
        'per_learner_enrollment_limit': (is_none, 'must not define a per-learner enrollment limit.'),
    }

    class Meta:
        """ Meta class for this policy type. """
        proxy = True

    def save(self, *args, **kwargs):
        """
        This type of policy must always have an access_method of "assigned".
        Additionally, if no ``assignment_configuration``
        is present, create one and associate it with this record.
        Lastly, ensure that the associated assignment_configuration has the
        same ``enterprise_customer_uuid`` as this policy record.
        """
        self.access_method = AccessMethods.ASSIGNED
        if not self.assignment_configuration:
            self.assignment_configuration = assignments_api.create_assignment_configuration(
                self.enterprise_customer_uuid
            )
        elif self.enterprise_customer_uuid != self.assignment_configuration.enterprise_customer_uuid:
            self.assignment_configuration.enterprise_customer_uuid = self.enterprise_customer_uuid
            self.assignment_configuration.save()
        super().save(*args, **kwargs)

    @property
    def total_allocated(self):
        """
        Total amount of assignments currently allocated via this policy.

        Returns:
            int: Negative USD cents representing the total amount of currently allocated assignments.
        """
        return assignments_api.get_allocated_quantity_for_configuration(
            self.assignment_configuration,
        )

    @property
    def spend_available(self):
        """
        Policy-wide spend available.  This sub-class definition additionally takes assignments into account.

        Returns:
            int: quantity >= 0 of USD Cents representing the policy-wide spend available.
        """
        # super().spend_available is POSITIVE USD Cents representing available spend ignoring assignments.
        # self.total_allocated is NEGATIVE USD Cents representing currently allocated assignments.
        return max(0, super().spend_available + self.total_allocated)

    def get_assignment(self, lms_user_id, content_key):
        """
        Helper to get a ``LearnerContentAssignment`` for the given learner/content identifier pair
        in this policy's assignment configuration.  Returns None if no such pair is assigned.
        """
        cache_key = versioned_cache_key('get_assignment', self.uuid, lms_user_id, content_key)
        cached_response = request_cache(namespace=REQUEST_CACHE_NAMESPACE).get_cached_response(cache_key)
        if cached_response.is_found:
            logger.info(
                f'get_assignment cache hit: policy {self.uuid} lms_user_id {lms_user_id} content_key {content_key}',
            )
            return cached_response.value

        assignment = assignments_api.get_assignment_for_learner(
            self.assignment_configuration,
            lms_user_id,
            content_key,
        )
        request_cache(namespace=REQUEST_CACHE_NAMESPACE).set(cache_key, assignment)
        logger.info(
            f'get_assignment cache hit: policy {self.uuid} lms_user_id {lms_user_id} content_key {content_key}',
        )

        return assignment

    def get_list_price(self, lms_user_id, content_key):
        """
        Uses the relevant assignment for this policy instance to determine the appropriate
        list price of the requested ``content_key``.

        Returns:
        A dictionary of the form
        ```
        {
            "usd": 149.50, # the list price in US Dollars as a float
            "usd_cents": 14950 # the list price in USD Cents as an int
        }
        Both ``usd`` and ``usd_cents`` will be non-negative.
        """
        found_assignment = self.get_assignment(lms_user_id, content_key)
        if not found_assignment:
            return super().get_list_price(lms_user_id, content_key)
        # an assignment's content_quantity is always <= 0 to express the fact
        # that value has been consumed from a subsidy (though not necessarily fulfilled)
        return make_list_price_dict(integer_cents=found_assignment.content_quantity * -1)

    def can_redeem(
        self, lms_user_id, content_key,
        skip_customer_user_check=False, skip_enrollment_deadline_check=False,
        **kwargs,
    ):
        """
        Checks if the given lms_user_id has an existing assignment on the given content_key, ready to be accepted.
        """
        # perform generic access checks
        should_attempt_redemption, reason, existing_redemptions = super().can_redeem(
            lms_user_id,
            content_key,
            skip_customer_user_check,
            skip_enrollment_deadline_check,
            **kwargs,
        )
        if not should_attempt_redemption:
            self._log_redeemability(False, reason, lms_user_id, content_key, extra=existing_redemptions)
            return (False, reason, existing_redemptions)
        # Now that the default checks are complete, proceed with the custom checks for this assignment policy type.
        found_assignment = self.get_assignment(lms_user_id, content_key)
        failure_reason_for_state = {
            None: REASON_LEARNER_NOT_ASSIGNED_CONTENT,
            LearnerContentAssignmentStateChoices.CANCELLED: REASON_LEARNER_ASSIGNMENT_CANCELLED,
            LearnerContentAssignmentStateChoices.ERRORED: REASON_LEARNER_ASSIGNMENT_FAILED,
            LearnerContentAssignmentStateChoices.EXPIRED: REASON_LEARNER_ASSIGNMENT_EXPIRED,
            LearnerContentAssignmentStateChoices.REVERSED: REASON_LEARNER_ASSIGNMENT_REVERSED,
            # This should never happen.  Even if the frontend had a bug that called the redemption endpoint for already
            # redeemed content, we already check for existing redemptions at the beginning of this function and fail
            # fast.  Reaching this block would be extremely weird.
            LearnerContentAssignmentStateChoices.ACCEPTED: REASON_LEARNER_NOT_ASSIGNED_CONTENT,
        }
        if not found_assignment or found_assignment.state != LearnerContentAssignmentStateChoices.ALLOCATED:
            found_assignment_state = getattr(found_assignment, "state", None)
            failure_reason = failure_reason_for_state.get(found_assignment_state, REASON_LEARNER_NOT_ASSIGNED_CONTENT)
            self._log_redeemability(False, failure_reason, lms_user_id, content_key, extra=existing_redemptions)
            return (False, failure_reason, existing_redemptions)

        # Learner can redeem the subsidy access policy
        self._log_redeemability(True, None, lms_user_id, content_key)
        return (True, None, existing_redemptions)

    def credit_available(self, lms_user_id, *args, **kwargs):
        """
        Determine whether a learner has credit available for the subsidy access policy, determined
        based on the presence of unacknowledged assignments.
        """
        # Perform generic checks for credit availability; skip the check for inactive subsidies in order
        # to continue returning expired/cancelled assignments for the purposes of displaying them in the UI.
        is_credit_available = super().credit_available(lms_user_id, skip_inactive_subsidy_check=True)
        if not is_credit_available:
            return False

        # Validate whether learner has assignments available for this policy.
        assignments = assignments_api.get_assignments_for_configuration(
            self.assignment_configuration,
            lms_user_id=lms_user_id,
        )
        unacknowledged_assignments_uuids = [
            assignment.uuid
            for assignment in assignments
            if not assignment.learner_acknowledged
        ]
        return len(unacknowledged_assignments_uuids) > 0

    def redeem(self, lms_user_id, content_key, all_transactions, metadata=None, **kwargs):
        """
        Redeem content, but only if there's a matching assignment.  On successful redemption, the assignment state will
        be set to 'accepted', otherwise 'errored'.

        Returns:
            A ledger transaction.

        Raises:
            SubsidyAPIHTTPError if the Subsidy API request failed.
            ValueError if the access method of this policy is invalid.
        """
        found_assignment = self.get_assignment(lms_user_id, content_key)
        # The following checks for non-allocated assignments only exist to be defensive against race-conditions, but
        # in practice should never happen if the caller locks the policy and runs can_redeem() before redeem().
        if not found_assignment:
            raise MissingAssignment(
                f'No assignment was found for lms_user_id={lms_user_id} and content_key=<{content_key}>.'
            )
        if found_assignment.state != LearnerContentAssignmentStateChoices.ALLOCATED:
            raise MissingAssignment(
                f"Only an assignment with state='{found_assignment.state}' was found for lms_user_id={lms_user_id} "
                f"and content_key=<{content_key}>."
            )
        try:
            requested_price_cents = -1 * found_assignment.content_quantity
            ledger_transaction = super().redeem(
                lms_user_id,
                content_key,
                all_transactions,
                metadata=metadata,
                requested_price_cents=requested_price_cents,
            )
        except SubsidyAPIHTTPError as exc:
            # Migrate assignment to errored if the subsidy API call errored.
            found_assignment.state = LearnerContentAssignmentStateChoices.ERRORED
            found_assignment.errored_at = localized_utcnow()
            found_assignment.save()
            found_assignment.add_errored_redeemed_action(exc)
            raise
        # Migrate assignment to accepted.
        found_assignment.state = LearnerContentAssignmentStateChoices.ACCEPTED
        # For course-based assignments we need to update the preferred_course_run_key to point to the actual learner
        # selection instead of the one we guessed the admin wanted. That way, the nudge emails correspond to the actual
        # enrolled course run, not just the one that the admin might have preferred.
        found_assignment.preferred_course_run_key = content_key
        found_assignment.accepted_at = localized_utcnow()
        found_assignment.errored_at = None
        found_assignment.cancelled_at = None
        found_assignment.expired_at = None
        found_assignment.transaction_uuid = ledger_transaction.get('uuid')  # uuid should always be in the API response.
        found_assignment.save()
        found_assignment.add_successful_redeemed_action()
        return ledger_transaction

    def validate_requested_allocation_price(self, content_key, requested_price_cents):
        """
        Validates that the requested allocation price (in USD cents)
        is within some acceptable error bound interval.
        """
        if requested_price_cents < 0:
            raise PriceValidationError('Can only allocate non-negative content_price_cents')

        canonical_price_cents = self.get_content_price(content_key)
        lower_bound = settings.ALLOCATION_PRICE_VALIDATION_LOWER_BOUND_RATIO * canonical_price_cents
        upper_bound = settings.ALLOCATION_PRICE_VALIDATION_UPPER_BOUND_RATIO * canonical_price_cents
        if not (lower_bound <= requested_price_cents <= upper_bound):
            raise PriceValidationError(
                f'Requested price {requested_price_cents} for {content_key} '
                f'outside of acceptable interval on canonical course price of {canonical_price_cents}.'
            )

    def can_allocate(self, number_of_learners, content_key, content_price_cents):
        """
        Takes allocated LearnerContentAssignment records related to this policy
        into account to determine if ``number_of_learners`` new assignment
        records can be allocated in this policy for the given ``content_key``
        and it's current ``content_price_cents``.

        Params:
          number_of_learners: Non-negative integer indicating the number of learners to allocate this content to.
          content_key: Typically a course key (although theoretically could be *any* content identifier).
          content_price_cents: A **non-negative** integer reflecting the current price of the content in USD cents.
        """
        self.validate_requested_allocation_price(content_key, content_price_cents)

        # inactive policy
        if not self.is_redemption_enabled:
            return (False, REASON_POLICY_EXPIRED)

        # no content key in catalog
        if not self.catalog_contains_content_key(content_key):
            return (False, REASON_CONTENT_NOT_IN_CATALOG)

        if not self.is_subsidy_active:
            return (False, REASON_SUBSIDY_EXPIRED)

        # Determine total cost, in cents, of content to potentially allocated
        positive_total_price_cents = number_of_learners * content_price_cents

        # Determine total amount, in cents, already transacted via this policy.
        # This is a number <= 0
        spent_amount_cents = self.aggregates_for_policy().get('total_quantity') or 0

        # Determine total amount, in cents, of assignments already
        # allocated via this policy. This is a number <= 0
        total_allocated_assignments_cents = assignments_api.get_allocated_quantity_for_configuration(
            self.assignment_configuration,
        )
        total_allocated_and_spent_cents = spent_amount_cents + total_allocated_assignments_cents

        # Use all of these pieces to ensure that the assignments to potentially
        # allocate won't exceed the remaining balance of the related subsidy.
        subsidy_balance = self.subsidy_balance()
        if self.content_would_exceed_limit(
            total_allocated_assignments_cents,
            subsidy_balance,
            positive_total_price_cents,
        ):
            logger.info(
                f'content_would_exceed_limit function: '
                f'subsidy_uuid={self.subsidy_uuid}, '
                f'policy_uuid={self.uuid},'
                f'total_allocated_assignments_cents={total_allocated_assignments_cents}, '
                f'subsidy_balance={subsidy_balance}, '
                f'positive_total_price_cents={positive_total_price_cents}, '
            )
            return (False, REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY)

        # Lastly, use all of these pieces to ensure that the assignments to potentially
        # allocate won't exceed the spend limit of this policy
        if self.content_would_exceed_limit(
            total_allocated_and_spent_cents,
            self.spend_limit,
            positive_total_price_cents,
        ):
            logger.info(
                f'content_would_exceed_limit function: '
                f'subsidy_uuid={self.subsidy_uuid}, '
                f'policy_uuid={self.uuid}, '
                f'total_allocated_and_spent_centers={total_allocated_and_spent_cents}, '
                f'spend_limit={self.spend_limit}, '
                f'positive_total_price_cents={positive_total_price_cents}, '
            )
            return (False, REASON_POLICY_SPEND_LIMIT_REACHED)

        return (True, None)

    def allocate(self, learner_emails, content_key, content_price_cents):
        """
        Creates allocated ``LearnerContentAssignment`` records.

        Params:
          learner_emails: A list of learner emails for whom content should be allocated.
          content_key: Typically a course key (although theoretically could be *any* content identifier).
          content_price_cents: A *negative* integer reflecting the current price of the content in USD cents.
        """
        return assignments_api.allocate_assignments(
            self.assignment_configuration,
            learner_emails,
            content_key,
            content_price_cents,
        )


class PolicyGroupAssociation(TimeStampedModel):
    """
    This model ties together a policy (SubsidyAccessPolicy) and a group (EnterpriseGroup in edx-enterprise).

    .. no_pii: This model has no PII
    """

    class Meta:
        unique_together = [
            ('subsidy_access_policy', 'enterprise_group_uuid'),
        ]

    subsidy_access_policy = models.ForeignKey(
        SubsidyAccessPolicy,
        related_name="groups",
        on_delete=models.CASCADE,
        null=False,
        help_text="The SubsidyAccessPolicy that this group is tied to.",
    )

    enterprise_group_uuid = models.UUIDField(
        editable=True,
        unique=False,
        null=True,
        blank=True,
        help_text='The uuid that uniquely identifies the associated group.',
    )


class ForcedPolicyRedemption(TimeStampedModel):
    """
    There is frequently a need to force through a redemption
    (and related enrollment/fulfillment) of a particular learner,
    covered by a particular subsidy access policy, into some specific course run.
    This needs exists for reasons related to upstream business constraints,
    notably in cases where a course is included in a policy's catalog,
    but the desired course run is not discoverable due to the
    current state of its metadata. This model supports executing such a redemption.

    .. no_pii: This model has no PII
    """
    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
        help_text='The uuid that uniquely identifies this policy record.',
    )
    subsidy_access_policy = models.ForeignKey(
        SubsidyAccessPolicy,
        related_name="forced_redemptions",
        on_delete=models.SET_NULL,
        null=True,
        help_text="The SubsidyAccessPolicy that this forced redemption relates to.",
    )
    lms_user_id = models.IntegerField(
        null=False,
        blank=False,
        db_index=True,
        help_text=(
            "The id of the Open edX LMS user record that identifies the learner.",
        ),
    )
    course_run_key = models.CharField(
        max_length=255,
        blank=False,
        null=False,
        db_index=True,
        help_text=(
            "The course run key to enroll the learner into.",
        ),
    )
    content_price_cents = models.BigIntegerField(
        null=False,
        blank=False,
        help_text="Cost of the content in USD Cents, should be >= 0.",
    )
    wait_to_redeem = models.BooleanField(
        default=False,
        help_text="If selected, will not force redemption when the record is saved via Django admin.",
    )
    redeemed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="The time the forced redemption succeeded.",
    )
    errored_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="The time the forced redemption failed.",
    )
    traceback = models.TextField(
        blank=True,
        null=True,
        editable=False,
        help_text="Any traceback we recorded when an error was encountered.",
    )
    transaction_uuid = models.UUIDField(
        null=True,
        blank=True,
        editable=False,
        db_index=True,
        help_text=(
            "The transaction uuid caused by successful redemption.",
        ),
    )
    history = HistoricalRecords()

    @property
    def policy_uuid(self):
        """
        Convenience property used by this model's Admin class.
        """
        return self.subsidy_access_policy.uuid

    def __str__(self):
        return (
            f'<{self.__class__.__name__} policy_uuid={self.subsidy_access_policy.uuid}, '
            f'transaction_uuid={self.transaction_uuid}, '
            f'lms_user_id={self.lms_user_id}, course_run_key={self.course_run_key}>'
        )

    def create_assignment(self):
        """
        For assignment-based policies, an allocated ``LearnerContentAssignment`` must exist
        before redemption can occur.
        """
        assignment_configuration = self.subsidy_access_policy.assignment_configuration
        # Ensure that the requested content key is available for the related customer.
        _ = get_and_cache_content_metadata(
            assignment_configuration.enterprise_customer_uuid,
            self.course_run_key,
        )

        client = self.subsidy_access_policy.lms_api_client
        ecu_record = client.get_enterprise_user(
            self.subsidy_access_policy.enterprise_customer_uuid,
            self.lms_user_id,
        )
        if not ecu_record:
            raise Exception(f'No ECU record could be found for lms_user_id {self.lms_user_id}')

        user_email = ecu_record.get('user', {}).get('email')
        if not user_email:
            raise Exception(f'No email could be found for lms_user_id {self.lms_user_id}')

        return assignments_api.allocate_assignments(
            assignment_configuration,
            [user_email],
            self.course_run_key,
            self.content_price_cents,
            known_lms_user_ids=[self.lms_user_id],
        )

    def force_redeem(self, extra_metadata=None):
        """
        Forces redemption for the requested course run key in the associated policy.
        """
        if self.redeemed_at and self.transaction_uuid:
            # Just return if we've already got a successful redemption.
            return

        if self.subsidy_access_policy.access_method == AccessMethods.ASSIGNED:
            self.create_assignment()

        try:
            with self.subsidy_access_policy.lock():
                can_redeem, reason, existing_transactions = self.subsidy_access_policy.can_redeem(
                    self.lms_user_id, self.course_run_key, skip_enrollment_deadline_check=True,
                )
                extra_metadata = extra_metadata or {}
                if can_redeem:
                    result = self.subsidy_access_policy.redeem(
                        self.lms_user_id,
                        self.course_run_key,
                        existing_transactions,
                        metadata={
                            FORCE_ENROLLMENT_KEYWORD: True,
                            **extra_metadata,
                        },
                    )
                    self.transaction_uuid = result['uuid']
                    self.redeemed_at = result['modified']
                    self.save()
                else:
                    raise Exception(f'Failed forced redemption: {reason}')
        except SubsidyAccessPolicyLockAttemptFailed as exc:
            logger.exception(exc)
            self.errored_at = localized_utcnow()
            self.traceback = format_traceback(exc)
            self.save()
            raise
        except SubsidyAPIHTTPError as exc:
            error_payload = exc.error_payload()
            self.errored_at = localized_utcnow()
            self.traceback = format_traceback(exc) + f'\nResponse payload:\n{error_payload}'
            self.save()
            logger.exception(f'{exc} when creating transaction in subsidy API: {error_payload}')
            raise
