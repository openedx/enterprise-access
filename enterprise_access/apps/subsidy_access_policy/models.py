"""
Models for subsidy_access_policy
"""
import logging
import sys
from contextlib import contextmanager
from uuid import UUID, uuid4

import requests
from django.conf import settings
from django.core.cache import cache as django_cache
from django.core.exceptions import ValidationError
from django.db import models
from django_extensions.db.models import TimeStampedModel
from edx_django_utils.cache.utils import get_cache_key

from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.content_assignments import api as assignments_api
from enterprise_access.apps.content_assignments.constants import LearnerContentAssignmentStateChoices
from enterprise_access.cache_utils import request_cache, versioned_cache_key
from enterprise_access.utils import is_none, is_not_none, localized_utcnow

from ..content_assignments.models import AssignmentConfiguration
from .constants import (
    CREDIT_POLICY_TYPE_PRIORITY,
    REASON_CONTENT_NOT_IN_CATALOG,
    REASON_LEARNER_ASSIGNMENT_CANCELLED,
    REASON_LEARNER_ASSIGNMENT_FAILED,
    REASON_LEARNER_MAX_ENROLLMENTS_REACHED,
    REASON_LEARNER_MAX_SPEND_REACHED,
    REASON_LEARNER_NOT_ASSIGNED_CONTENT,
    REASON_LEARNER_NOT_IN_ENTERPRISE,
    REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY,
    REASON_POLICY_EXPIRED,
    REASON_POLICY_SPEND_LIMIT_REACHED,
    REASON_SUBSIDY_EXPIRED,
    AccessMethods,
    TransactionStateChoices
)
from .content_metadata_api import (
    get_and_cache_catalog_contains_content,
    get_and_cache_content_metadata,
    get_list_price_for_content,
    list_price_dict_from_usd_cents
)
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
from .utils import ProxyAwareHistoricalRecords, create_idempotency_key_for_transaction, get_versioned_subsidy_client

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
    retired = models.BooleanField(
        default=False,
        help_text=(
            "True means redeemability of content using this policy has been enabled. "
            "Set this to False to deactivate the policy but keep it visible from an admin's perspective "
            "(useful when you want to just expire a policy without expiring the whole plan)."
        ),
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
    def is_assignable(self):
        """
        Convenience property to determine if this policy is assignable.
        """
        return self.access_method == AccessMethods.ASSIGNED

    def clean(self):
        """
        Used to help validate field values before saving this model instance.
        """
        for field_name, (constraint_function, error_message) in self.FIELD_CONSTRAINTS.items():
            field = getattr(self, field_name)
            if not constraint_function(field):
                raise ValidationError(f'{self} {error_message}')

    def save(self, *args, **kwargs):
        """
        Override to persist policy type.
        """
        if type(self).__name__ == SubsidyAccessPolicy.__name__:
            # it doesn't make sense to create an object of SubsidyAccessPolicy
            # because it is not a concrete policy
            raise TypeError("Can not create object of class SubsidyAccessPolicy")

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

    def subsidy_balance(self):
        """
        Returns total remaining balance for the associated subsidy ledger.
        """
        current_balance = self.subsidy_record().get('current_balance') or 0
        return int(current_balance)

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

        Override this in sub-classess that use assignments.  Empty definition needed here in order to make serializer
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
                'aggregates_for_policy cache hit: subsidy %s, policy %s',
                self.subsidy_uuid, self.uuid,
            )
            return cached_response.value

        response_payload = self.subsidy_client.list_subsidy_transactions(
            subsidy_uuid=self.subsidy_uuid,
            subsidy_access_policy_uuid=self.uuid,
        )
        result = response_payload['aggregates']
        logger.info(
            'aggregates_for_policy cache miss: subsidy %s, policy %s',
            self.subsidy_uuid, self.uuid,
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
            '[POLICY REDEEMABILITY]: policy: %s, is_redeemable: %s, reason: %s'
            'lms_user_id: %s, content_key: %s, extra=%s'
        )
        logger.info(message, self.uuid, is_redeemable, reason, lms_user_id, content_key, extra)

    def can_redeem(self, lms_user_id, content_key, skip_customer_user_check=False):
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
            '[POLICY REDEEMABILITY] Checking for policy: %s, lms_user_id: %s, content_key: %s',
            self.uuid, lms_user_id, content_key,
        )
        # inactive policy
        if not self.is_redemption_enabled:
            self._log_redeemability(False, REASON_POLICY_EXPIRED, lms_user_id, content_key)
            return (False, REASON_POLICY_EXPIRED, [])

        # learner not associated to enterprise
        if not skip_customer_user_check:
            if not self.lms_api_client.enterprise_contains_learner(self.enterprise_customer_uuid, lms_user_id):
                self._log_redeemability(False, REASON_LEARNER_NOT_IN_ENTERPRISE, lms_user_id, content_key)
                return (False, REASON_LEARNER_NOT_IN_ENTERPRISE, [])

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

        # TODO: Add Course Upgrade/Registration Deadline Passed Error here

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
            * Whether the subsidy is active (non-expired).
            * Whether the subsidy has remaining balance.
            * Whether the transactions associated with policy have exceeded the policy-wide spend limit.
        """
        # inactive policy
        if not self.is_redemption_enabled:
            logger.info('[credit_available] policy %s inactive', self.uuid)
            return False

        # learner not linked to enterprise
        if not skip_customer_user_check:
            if not self.lms_api_client.enterprise_contains_learner(self.enterprise_customer_uuid, lms_user_id):
                logger.info(
                    '[credit_available] learner %s not linked to enterprise %s',
                    lms_user_id,
                    self.enterprise_customer_uuid
                )
                return False

        # verify associated subsidy is current (non-expired)
        try:
            if not skip_inactive_subsidy_check and not self.is_subsidy_active:
                logger.info('[credit_available] SubsidyAccessPolicy.subsidy_record() returned inactive subsidy')
                return False
        except requests.exceptions.HTTPError as exc:
            # when associated subsidy is soft-deleted, the subsidy retrieve API raises an exception.
            logger.info('[credit_available] SubsidyAccessPolicy.subsidy_record() raised HTTPError: %s', exc)
            return False

        # verify associated subsidy has remaining balance
        if self.subsidy_balance() <= 0:
            logger.info('[credit_available] SubsidyAccessPolicy.subsidy_record() returned empty balance')
            return False

        # verify spend against policy and configured spend limit
        if not self.has_credit_available_with_spend_limit():
            logger.info('[credit_available] policy %s has exceeded spend limit', self.uuid)
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
            try:
                creation_payload = {
                    'subsidy_uuid': str(self.subsidy_uuid),
                    'lms_user_id': lms_user_id,
                    'content_key': content_key,
                    'subsidy_access_policy_uuid': str(self.uuid),
                    'metadata': metadata,
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

    @classmethod
    def resolve_policy(cls, redeemable_policies):
        """
        Select one out of multiple policies which have already been deemed redeemable.

        Prioritize learner credit policies, and then prioritize policies with a sooner expiration date,
        and then subsidies that have smaller balances.  The type priority is codified in ``*_POLICY_TYPE_PRIORITY``
        variables in constants.py.

        Deficiencies:
        * If multiple policies with equal policy types, balances, and expiration dates tie for first place,
          the result is non-deterministic.

        Original spec:
        https://2u-internal.atlassian.net/wiki/spaces/SOL/pages/229212214/Commission+Subsidy+Access+Policy+API#Policy-Resolver

        Args:
            redeemable_policies (list of SubsidyAccessPolicy): A list of subsidy access policies to select one from.

        Returns:
           SubsidyAccessPolicy: one policy selected from the input list.
        """
        # gate for experimental functionality to resolve multiple policies
        if not getattr(settings, 'MULTI_POLICY_RESOLUTION_ENABLED', False):
            logger.info('resolve_policy MULTI_POLICY_RESOLUTION_ENABLED disabled')
            return redeemable_policies[0]

        if len(redeemable_policies) == 1:
            return redeemable_policies[0]

        # resolve policies by:
        # - priority (of type)
        # - expiration, sooner to expire first
        # - balance, lower balance first
        sorted_policies = sorted(
            redeemable_policies,
            key=lambda p: (p.priority, p.subsidy_expiration_datetime, p.subsidy_balance()),
        )
        logger.info('resolve_policy multiple policies resolved')
        return sorted_policies[0]

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

    def can_redeem(self, lms_user_id, content_key, skip_customer_user_check=False):
        """
        Checks if the given lms_user_id has a number of existing subsidy transactions
        LTE to the learner enrollment cap declared by this policy.
        """
        # perform generic access checks
        should_attempt_redemption, reason, existing_redemptions = \
            super().can_redeem(lms_user_id, content_key, skip_customer_user_check)
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


class PerLearnerSpendCreditAccessPolicy(CreditPolicyMixin, SubsidyAccessPolicy):
    """
    Policy that limits the amount of learner spend for enrollment transactions.

    .. no_pii: This model has no PII
    """
    objects = PolicyManager()

    # Policies of this type *must not* define a per-learner enrollment limit or an assignment configuration
    FIELD_CONSTRAINTS = {
        'assignment_configuration': (is_none, 'must not relate to an AssignmentConfiguration.'),
        'per_learner_enrollment_limit': (is_none, 'must not define a per-learner enrollment limit.'),
    }

    class Meta:
        """
        Metaclass for PerLearnerSpendCreditAccessPolicy.
        """
        proxy = True

    def can_redeem(self, lms_user_id, content_key, skip_customer_user_check=False):
        """
        Determines whether learner can redeem a subsidy access policy given the
        limits specified on the policy.
        """
        # perform generic access checks
        should_attempt_redemption, reason, existing_redemptions = super().can_redeem(
            lms_user_id,
            content_key,
            skip_customer_user_check,
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
                    False, REASON_LEARNER_MAX_SPEND_REACHED, lms_user_id, content_key, extra=existing_redemptions,
                )
                return (False, REASON_LEARNER_MAX_SPEND_REACHED, existing_redemptions)

        # learner can redeem the subsidy access policy
        self._log_redeemability(True, None, lms_user_id, content_key)
        return (True, None, existing_redemptions)

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


class AssignedLearnerCreditAccessPolicy(CreditPolicyMixin, SubsidyAccessPolicy):
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
                'get_assignment cache hit: policy %s lms_user_id %s content_key %s',
                self.uuid, lms_user_id, content_key,
            )
            return cached_response.value

        assignment = assignments_api.get_assignment_for_learner(
            self.assignment_configuration,
            lms_user_id,
            content_key,
        )
        request_cache(namespace=REQUEST_CACHE_NAMESPACE).set(cache_key, assignment)
        logger.info(
            'get_assignment cache hit: policy %s lms_user_id %s content_key %s',
            self.uuid, lms_user_id, content_key,
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
        return list_price_dict_from_usd_cents(found_assignment.content_quantity * -1)

    def can_redeem(self, lms_user_id, content_key, skip_customer_user_check=False):
        """
        Checks if the given lms_user_id has an existing assignment on the given content_key, ready to be accepted.
        """
        # perform generic access checks
        should_attempt_redemption, reason, existing_redemptions = super().can_redeem(
            lms_user_id,
            content_key,
            skip_customer_user_check,
        )
        if not should_attempt_redemption:
            self._log_redeemability(False, reason, lms_user_id, content_key, extra=existing_redemptions)
            return (False, reason, existing_redemptions)
        # Now that the default checks are complete, proceed with the custom checks for this assignment policy type.
        found_assignment = self.get_assignment(lms_user_id, content_key)
        if not found_assignment:
            self._log_redeemability(
                False, REASON_LEARNER_NOT_ASSIGNED_CONTENT, lms_user_id, content_key, extra=existing_redemptions,
            )
            return (False, REASON_LEARNER_NOT_ASSIGNED_CONTENT, existing_redemptions)
        elif found_assignment.state == LearnerContentAssignmentStateChoices.CANCELLED:
            self._log_redeemability(
                False, REASON_LEARNER_ASSIGNMENT_CANCELLED, lms_user_id, content_key, extra=existing_redemptions,
            )
            return (False, REASON_LEARNER_ASSIGNMENT_CANCELLED, existing_redemptions)
        elif found_assignment.state == LearnerContentAssignmentStateChoices.ERRORED:
            self._log_redeemability(
                False, REASON_LEARNER_ASSIGNMENT_FAILED, lms_user_id, content_key, extra=existing_redemptions,
            )
            return (False, REASON_LEARNER_ASSIGNMENT_FAILED, existing_redemptions)
        elif found_assignment.state == LearnerContentAssignmentStateChoices.ACCEPTED:
            # This should never happen.  Even if the frontend had a bug that called the redemption endpoint for already
            # redeemed content, we already check for existing redemptions at the beginning of this function and fail
            # fast.  Reaching this block would be extremely weird.
            self._log_redeemability(
                False, REASON_LEARNER_NOT_ASSIGNED_CONTENT, lms_user_id, content_key, extra=existing_redemptions,
            )
            return (False, REASON_LEARNER_NOT_ASSIGNED_CONTENT, existing_redemptions)

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
        default=uuid4,
        editable=False,
        unique=True,
        null=False,
        help_text='The uuid that uniquely identifies the associated group.',
    )
