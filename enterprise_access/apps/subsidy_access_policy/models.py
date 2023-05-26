"""
Models for subsidy_access_policy
"""
import sys
from contextlib import contextmanager
from uuid import UUID, uuid4

import requests
from django.core.cache import cache as django_cache
from django.db import models
from django_extensions.db.models import TimeStampedModel
from edx_django_utils.cache.utils import get_cache_key
from simple_history.models import HistoricalRecords

from enterprise_access.apps.api_client.lms_client import LmsApiClient

from .constants import (
    CREDIT_POLICY_TYPE_PRIORITY,
    REASON_CONTENT_NOT_IN_CATALOG,
    REASON_LEARNER_MAX_ENROLLMENTS_REACHED,
    REASON_LEARNER_MAX_SPEND_REACHED,
    REASON_LEARNER_NOT_IN_ENTERPRISE,
    REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY,
    REASON_POLICY_NOT_ACTIVE,
    REASON_POLICY_SPEND_LIMIT_REACHED,
    AccessMethods
)
from .content_metadata_api import get_and_cache_catalog_contains_content, get_and_cache_content_metadata
from .exceptions import ContentPriceNullException, SubsidyAccessPolicyLockAttemptFailed, SubsidyAPIHTTPError
from .subsidy_api import get_and_cache_transactions_for_learner
from .utils import get_versioned_subsidy_client

POLICY_LOCK_RESOURCE_NAME = "subsidy_access_policy"


class PolicyManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(policy_type=self.model.__name__)


class SubsidyAccessPolicy(TimeStampedModel):
    """
    Tie together information used to control access to a subsidy.
    This model joins group, catalog, and access method.

    .. no_pii: This model has no PII
    """

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
    description = models.TextField(help_text="Brief description about a specific policy.")
    active = models.BooleanField(
        default=False,
        help_text='Whether this policy is active, defaults to false.',
    )
    catalog_uuid = models.UUIDField(
        db_index=True,
        help_text='The primary identifier of the catalog associated with this policy.',
    )
    subsidy_uuid = models.UUIDField(
        db_index=True,
        help_text='The primary identifier of the subsidy associated with this policy.',
    )
    group_uuid = models.UUIDField(
        db_index=True,
        null=True,
        blank=True,
        help_text=(
            "Optional, currently useless field for future Enterprise Groups implementation."
        ),
    )
    access_method = models.CharField(
        max_length=32,
        choices=AccessMethods.CHOICES,
        default=AccessMethods.DIRECT,
        help_text='The mechanism by which learners access content in this policy, defaults to "direct".',
    )

    per_learner_enrollment_limit = models.IntegerField(
        null=True,
        blank=True,
        default=None,
        help_text=(
            'The maximum number of enrollments allowed for a single learner under this policy. '
            'Defaults to null, which means that no such maximum exists.'
        ),
    )
    per_learner_spend_limit = models.IntegerField(
        null=True,
        blank=True,
        default=None,
        help_text=(
            'The maximum amount of allowed money spent for a single learner under this policy. '
            'Denoted in USD cents. '
            'Defaults to null, which means that no such maximum exists.'
        ),
    )
    spend_limit = models.IntegerField(
        null=True,
        blank=True,
        default=None,
        help_text=(
            'The maximum number of allowed dollars to be spent, in aggregate, by all learners '
            'under this policy. Denoted in USD cents. '
            'Defaults to null, which means that no such maximum exists.'
        ),
    )

    history = HistoricalRecords()

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

    def save(self, *args, **kwargs):
        """
        Override to persist policy type.
        """
        if type(self).__name__ == SubsidyAccessPolicy.__name__:
            # it doesn't make sense to create an object of SubsidyAccessPolicy
            # because it is not a concrete policy
            raise TypeError("Can not create object of class SubsidyAccessPolicy")

        self.policy_type = type(self).__name__
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
        finally:
            return super().__new__(proxy_class)  # pylint: disable=lost-exception

    def subsidy_record(self):
        return self.subsidy_client.retrieve_subsidy(subsidy_uuid=self.subsidy_uuid)

    def subsidy_balance(self):
        """
        Returns total remaining balance for the associated subsidy ledger.
        """
        return int(self.subsidy_record().get('current_balance'))

    def remaining_balance(self):
        """
        Synonym for subsidy_balance().
        """
        return self.subsidy_balance()

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
        return get_and_cache_content_metadata(
            self.enterprise_customer_uuid,
            content_key,
        )

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

    def aggregates_for_policy(self):
        """
        Returns aggregate transaction data for this policy.
        """
        response_payload = self.subsidy_client.list_subsidy_transactions(
            subsidy_uuid=self.subsidy_uuid,
            subsidy_access_policy_uuid=self.uuid,
        )
        return response_payload['aggregates']

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

    def will_exceed_spend_limit(self, content_key, content_metadata=None):
        """
        Returns true if redeeming this course would exceed
        the ``spend_limit`` set by this policy.
        """
        if self.spend_limit is None:
            return False

        content_price = self.get_content_price(content_key, content_metadata=content_metadata)
        spent_amount = self.aggregates_for_policy().get('total_quantity') or 0
        return (spent_amount + content_price) >= self.spend_limit

    def can_redeem(self, lms_user_id, content_key, skip_customer_user_check=False):
        """
        Check that a given learner can redeem the given content.
        """
        if not self.active:
            return (False, REASON_POLICY_NOT_ACTIVE)

        if not skip_customer_user_check:
            if not self.lms_api_client.enterprise_contains_learner(self.enterprise_customer_uuid, lms_user_id):
                return (False, REASON_LEARNER_NOT_IN_ENTERPRISE)

        if not self.catalog_contains_content_key(content_key):
            return (False, REASON_CONTENT_NOT_IN_CATALOG)

        subsidy_can_redeem_payload = self.subsidy_client.can_redeem(
            self.subsidy_uuid,
            lms_user_id,
            content_key,
        )
        if not subsidy_can_redeem_payload.get('can_redeem', False):
            return (False, REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY)

        content_metadata = self.get_content_metadata(content_key)
        if not content_metadata:
            return (False, REASON_CONTENT_NOT_IN_CATALOG)

        if self.will_exceed_spend_limit(content_key, content_metadata=content_metadata):
            return (False, REASON_POLICY_SPEND_LIMIT_REACHED)

        return (True, None)

    def redeem(self, lms_user_id, content_key, metadata=None):
        """
        Redeem a subsidy for the given learner and content.
        Returns:
            A ledger transaction, or None if the subsidy was not redeemed.
        """
        if self.access_method == AccessMethods.DIRECT:
            try:
                return self.subsidy_client.create_subsidy_transaction(
                    subsidy_uuid=self.subsidy_uuid,
                    lms_user_id=lms_user_id,
                    content_key=content_key,
                    subsidy_access_policy_uuid=self.uuid,
                    metadata=metadata,
                )
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

        Prioritize learner credit policies, and then prioritize policies with subsidies that have smaller balances.  The
        type priority is codified in ``*_POLICY_TYPE_PRIORITY`` variables in constants.py.

        Deficiencies:
        * If multiple policies with equal policy types and equal subsidy balances tie for first place, the result is
          non-deterministic.

        Original spec:
        https://2u-internal.atlassian.net/wiki/spaces/SOL/pages/229212214/Commission+Subsidy+Access+Policy+API#Policy-Resolver

        Args:
            redeemable_policies (list of SubsidyAccessPolicy): A list of subsidy access policies to select one from.

        Returns:
           SubsidyAccessPolicy: one policy selected from the input list.
        """
        if len(redeemable_policies) == 1:
            return redeemable_policies[0]

        # For now, we inefficiently make one call per subsidy record.
        sorted_policies = sorted(
            redeemable_policies,
            key=lambda p: (p.priority, p.subsidy_balance()),
        )
        return sorted_policies[0]


class CreditPolicyMixin:
    """
    Mixin class for credit type policies.
    """

    @property
    def priority(self):
        return CREDIT_POLICY_TYPE_PRIORITY


class PerLearnerEnrollmentCreditAccessPolicy(SubsidyAccessPolicy, CreditPolicyMixin):
    """
    Policy that limits the number of enrollments transactions for a learner in a subsidy.

    .. no_pii: This model has no PII
    """

    objects = PolicyManager()

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
        should_attempt_redemption, reason = super().can_redeem(lms_user_id, content_key, skip_customer_user_check)
        if not should_attempt_redemption:
            return (False, reason)

        has_per_learner_enrollment_limit = self.per_learner_enrollment_limit is not None
        if has_per_learner_enrollment_limit:
            # only retrieve transactions if there is a per-learner enrollment limit
            learner_transactions_count = len(self.transactions_for_learner(lms_user_id)['transactions'])
            # check whether learner exceeded the per-learner enrollment limit
            if learner_transactions_count >= self.per_learner_enrollment_limit:
                return (False, REASON_LEARNER_MAX_ENROLLMENTS_REACHED)

        # learner can redeem the subsidy access policy
        return (True, None)

    def credit_available(self, lms_user_id=None):
        if self.remaining_balance_per_user(lms_user_id) > 0:
            return True
        return False

    def remaining_balance_per_user(self, lms_user_id=None):
        """
        Returns the remaining redeemable credit for the user.
        """
        existing_transaction_count = len(self.transactions_for_learner(lms_user_id)['transactions'])
        return self.per_learner_enrollment_limit - existing_transaction_count


class PerLearnerSpendCreditAccessPolicy(SubsidyAccessPolicy, CreditPolicyMixin):
    """
    Policy that limits the amount of learner spend for enrollment transactions.

    .. no_pii: This model has no PII
    """

    objects = PolicyManager()

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
        should_attempt_redemption, reason = super().can_redeem(lms_user_id, content_key, skip_customer_user_check)
        if not should_attempt_redemption:
            return (False, reason)

        has_per_learner_spend_limit = self.per_learner_spend_limit is not None
        if has_per_learner_spend_limit:
            # only retrieve transactions if there is a per-learner spend limit
            existing_learner_transaction_aggregates = self.transactions_for_learner(lms_user_id)['aggregates']
            spent_amount = existing_learner_transaction_aggregates.get('total_quantity') or 0
            content_price = self.get_content_price(content_key)
            if (spent_amount + content_price) >= self.per_learner_spend_limit:
                return (False, REASON_LEARNER_MAX_SPEND_REACHED)

        # learner can redeem the subsidy access policy
        return (True, None)

    def credit_available(self, lms_user_id=None):
        return self.remaining_balance_per_user(lms_user_id) > 0

    def remaining_balance_per_user(self, lms_user_id=None):
        """
        Returns the remaining redeemable credit for the user.
        """
        spent_amount = self.transactions_for_learner(lms_user_id)['aggregates'].get('total_quantity') or 0
        return self.per_learner_spend_limit - spent_amount
