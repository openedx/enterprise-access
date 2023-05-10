"""
Models for subsidy_access_policy
"""
import functools
import sys
from contextlib import contextmanager
from uuid import UUID, uuid4

from django.conf import settings
from django.core.cache import cache as django_cache
from django.db import models
from django.utils.functional import cached_property
from django_extensions.db.models import TimeStampedModel
from edx_django_utils.cache.utils import get_cache_key
from edx_enterprise_subsidy_client import get_enterprise_subsidy_api_client
from simple_history.models import HistoricalRecords

from enterprise_access.apps.api_client.enterprise_catalog_client import EnterpriseCatalogApiClient
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.subsidy_access_policy.constants import (
    CREDIT_POLICY_TYPE_PRIORITY,
    REASON_CONTENT_NOT_IN_CATALOG,
    REASON_LEARNER_MAX_ENROLLMENTS_REACHED,
    REASON_LEARNER_MAX_SPEND_REACHED,
    REASON_LEARNER_NOT_IN_ENTERPRISE,
    REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY,
    REASON_POLICY_NOT_ACTIVE,
    AccessMethods
)

POLICY_LOCK_RESOURCE_NAME = "subsidy_access_policy"


class SubsidyAccessPolicyLockAttemptFailed(Exception):
    """
    Raise when attempt to lock SubsidyAccessPolicy failed due to an already existing lock acquired on the same resource.
    """


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

    @classmethod
    @functools.lru_cache(maxsize=None)
    def get_subsidy_client(cls):
        """
        A request-cached EnterpriseSubsidyAPIClient instance.
        """
        kwargs = {}
        if getattr(settings, 'ENTERPRISE_SUBSIDY_API_CLIENT_VERSION', None):
            kwargs['version'] = int(settings.ENTERPRISE_SUBSIDY_API_CLIENT_VERSION)
        return get_enterprise_subsidy_api_client(**kwargs)

    @property
    def subsidy_client(self):
        return self.get_subsidy_client()

    @cached_property
    def catalog_client(self):
        """
        A request-cached EnterpriseCatalogApiClient instance.
        """
        return EnterpriseCatalogApiClient()

    @cached_property
    def lms_api_client(self):
        """
        A request-cached LmsApiClient instance.
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

    def transactions_for_learner(self, lms_user_id):
        """
        TODO: figure out cache?
        """
        response_payload = self.subsidy_client.list_subsidy_transactions(
            subsidy_uuid=self.subsidy_uuid,
            lms_user_id=lms_user_id,
            subsidy_access_policy_uuid=self.uuid,
        )
        return {
            'transactions': response_payload['results'],
            'aggregates': response_payload['aggregates'],
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

    def can_redeem(self, lms_user_id, content_key):
        """
        Check that a given learner can redeem the given content.
        """
        if not self.active:
            return (False, REASON_POLICY_NOT_ACTIVE)
        if not self.catalog_client.contains_content_items(self.catalog_uuid, [content_key]):
            return (False, REASON_CONTENT_NOT_IN_CATALOG)
        # TODO: can we rely on JWT roles to check this?
        if not self.lms_api_client.enterprise_contains_learner(self.enterprise_customer_uuid, lms_user_id):
            return (False, REASON_LEARNER_NOT_IN_ENTERPRISE)
        if not self.subsidy_client.can_redeem(self.subsidy_uuid, lms_user_id, content_key):
            return (False, REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY)

        return (True, None)

    def redeem(self, lms_user_id, content_key, metadata=None):
        """
        Redeem a subsidy for the given learner and content.
        Returns:
            A ledger transaction, or None if the subsidy was not redeemed.
        """
        if self.access_method == AccessMethods.DIRECT:
            return self.subsidy_client.create_subsidy_transaction(
                subsidy_uuid=self.subsidy_uuid,
                lms_user_id=lms_user_id,
                content_key=content_key,
                subsidy_access_policy_uuid=self.uuid,
                metadata=metadata,
            )
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
        subsidy_client = cls.get_subsidy_client()
        # For now, we inefficiently make one call per subsidy record.
        subsidy_uuids = set(redeemable_policy.subsidy_uuid for redeemable_policy in redeemable_policies)
        subsidy_balances = {
            subsidy_uuid: subsidy_client.retrieve_subsidy(subsidy_uuid)["current_balance"]
            for subsidy_uuid in subsidy_uuids
        }
        sorted_policies = sorted(
            redeemable_policies,
            key=lambda p: (p.priority, subsidy_balances[p.subsidy_uuid]),
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

    def can_redeem(self, lms_user_id, content_key):
        """
        Checks if the given lms_user_id has a number of existing subsidy transactions
        LTE to the learner enrollment cap declared by this policy.
        """
        # perform generic access checks
        should_attempt_redemption, reason = super().can_redeem(lms_user_id, content_key)
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

    def can_redeem(self, lms_user_id, content_key):
        """
        Determines whether learner can redeem a subsidy access policy given the
        limits specified on the policy.
        """
        # perform generic access checks
        should_attempt_redemption, reason = super().can_redeem(lms_user_id, content_key)
        if not should_attempt_redemption:
            return (False, reason)

        has_per_learner_spend_limit = self.per_learner_spend_limit is not None
        if has_per_learner_spend_limit:
            # only retrieve transactions if there is a per-learner spend limit
            spent_amount = self.transactions_for_learner(lms_user_id)['aggregates'].get('total_quantity') or 0
            course_price = self.subsidy_client.get_subsidy_content_data(
                self.enterprise_customer_uuid,
                content_key
            )['content_price']

            # check whether learner exceeded per-learner spend limit
            if (spent_amount + course_price) >= self.per_learner_spend_limit:
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
