""" Models for subsidy_access_policy """

import sys
from uuid import uuid4

from django.db import models
from django_extensions.db.models import TimeStampedModel
from simple_history.models import HistoricalRecords

from enterprise_access.apps.api.utils import acquire_subsidy_policy_lock, release_subsidy_policy_lock
from enterprise_access.apps.api_client.discovery_client import DiscoveryApiClient
from enterprise_access.apps.api_client.enterprise_catalog_client import EnterpriseCatalogApiClient
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.subsidy_access_policy.constants import (
    CREDIT_POLICY_TYPE_PRIORITY,
    SUBSCRIPTION_POLICY_TYPE_PRIORITY,
    AccessMethods
)
from enterprise_access.apps.subsidy_access_policy.mocks import group_client, subsidy_client

SUBSIDY_POLICY_LOCK_TIMEOUT_SECONDS = 300


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
    policy_type = models.CharField(max_length=64, editable=False)

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
    )
    enterprise_customer_uuid = models.UUIDField(
        db_index=True,
        null=True,
        blank=False,
        help_text=(
            "The owning Enterprise Customer's UUID.  Cannot be blank or null."
        ),
    )
    description = models.TextField(help_text="Brief description about a specific policy.")
    active = models.BooleanField(default=False)
    catalog_uuid = models.UUIDField(db_index=True)
    subsidy_uuid = models.UUIDField(db_index=True)
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
    )

    per_learner_enrollment_limit = models.IntegerField(
        null=True,
        blank=True,
        default=0,
    )
    per_learner_spend_limit = models.IntegerField(
        null=True,
        blank=True,
        default=0,
    )
    spend_limit = models.IntegerField(
        null=True,
        blank=True,
        default=0,
    )

    history = HistoricalRecords()

    def save(self, *args, **kwargs):
        """
        Override to persist policy type.
        """
        # TODO: find a better way to do this. Raising an exception will cause 500
        if type(self).__name__ == SubsidyAccessPolicy.__name__:
            # it doesn't make sense to create an object of SubsidyAccessPolicy because it is not a concrete policy
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

    def can_redeem(self, learner_id, content_key):
        """
        Check that a given learner can redeem the given content.
        """
        enterprise_catalog_api_client = EnterpriseCatalogApiClient()
        lms_api_client = LmsApiClient()

        if not enterprise_catalog_api_client.contains_content_items(self.catalog_uuid, [content_key]):
            return False
        if not lms_api_client.enterprise_contains_learner(self.enterprise_customer_uuid, learner_id):
            return False
        if not subsidy_client.can_redeem(self.subsidy_uuid, learner_id, content_key):
            return False

        return True

    def redeem(self, learner_id, content_key):
        """
        Redeem a subsidy for the given learner and content.
        Returns:
            A ledger transaction id, or None if the subsidy was not redeemed.
        """
        if self.access_method == AccessMethods.DIRECT:
            return subsidy_client.redeem(self.subsidy_uuid, learner_id, content_key)
        if self.access_method == AccessMethods.REQUEST:
            return subsidy_client.request_redemption(self.subsidy_uuid, learner_id, content_key)
        return None

    def has_redeemed(self, learner_id, content_key):
        """
        Check if the subsidy has been redeemed.
        """
        if self.access_method == AccessMethods.DIRECT:
            return subsidy_client.has_redeemed(self.subsidy_uuid, learner_id, content_key)
        elif self.access_method == AccessMethods.REQUEST:
            return subsidy_client.has_requested(self.subsidy_uuid, learner_id, content_key)
        else:
            raise ValueError(f"unknown access method {self.access_method}")

    def acquire_lock(self, learner_id, content_key):  # pylint: disable=unused-argument
        """
        Acquire a lock for transaction isolation.
        """
        return acquire_subsidy_policy_lock(
                self.uuid,
                django_cache_timeout=SUBSIDY_POLICY_LOCK_TIMEOUT_SECONDS,
            )

    def release_lock(self, learner_id, content_key):  # pylint: disable=unused-argument
        """
        Release a previously acquired lock for transaction isolation.
        """
        return release_subsidy_policy_lock(
                self.uuid,
                django_cache_timeout=SUBSIDY_POLICY_LOCK_TIMEOUT_SECONDS,
            )

    @staticmethod
    def resolve_policy(redeemable_policies):
        """
        Select one out of multiple policies which have already been deemed redeemable.

        Prefer learner credit policies, and prefer smaller balances.

        Deficiencies:
        - if multiple policies with matching subsidies tie for first place, the
            result is non-deterministic.
        - if multiple policies with identical balances tie for first place, the
            result is non-deterministic.

        Spec:
        https://2u-internal.atlassian.net/wiki/spaces/SOL/pages/229212214/Commission+Subsidy+Access+Policy+API#Policy-Resolver
        """

        # for each policy we need current balance
        # policies don't have direct access to subsidy balance, must call subsidy api to get current balance but
        # instead of making a call for each policy we can implement a specific endpoint in enterprise-subsidy
        # to get current balance for multiple subsidies.
        subsidy_uuids = [redeemable_policy.subsidy_uuid for redeemable_policy in redeemable_policies]
        subsidies_balance = subsidy_client.get_current_balance(subsidy_uuids)
        sorted_policies = sorted(
            redeemable_policies,
            key=lambda p: (p.priority, subsidies_balance[p.subsidy_uuid]),
        )
        return sorted_policies[0]


class SubscriptionAccessPolicy(SubsidyAccessPolicy):
    """
    A subsidy access policy for subscriptions.

    .. no_pii: This model has no PII
    """
    objects = PolicyManager()
    class Meta:
        """
        Metaclass for SubscriptionAccessPolicy.
        """
        proxy = True

    def can_redeem(self, learner_id, content_key):
        if subsidy_client.get_license_for_learner(self.subsidy_uuid, learner_id):
            return super().can_redeem(learner_id, content_key)

        group_uuids = list(group_client.get_groups_for_learner(learner_id))
        if self.group_uuid in group_uuids:
            if subsidy_client.get_license_for_group(self.subsidy_uuid, self.group_uuid):
                return super().can_redeem(learner_id, content_key)

        return False

    @property
    def priority(self):
        return SUBSCRIPTION_POLICY_TYPE_PRIORITY


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

    def can_redeem(self, learner_id, content_key):
        learner_transaction_count = subsidy_client.transactions_for_learner(
            subsidy_uuid = self.subsidy_uuid,
            learner_id = learner_id,
        )
        if learner_transaction_count < self.per_learner_enrollment_limit:
            return super().can_redeem(learner_id, content_key)

        return False


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

    def can_redeem(self, learner_id, content_key):
        spent_amount = subsidy_client.amount_spent_for_learner(
            subsidy_uuid = self.subsidy_uuid,
            learner_id = learner_id,
            )
        course_price = DiscoveryApiClient().get_course_price(content_key)
        if (spent_amount + course_price) < self.per_learner_spend_limit:
            return super().can_redeem(learner_id, content_key)

        return False


class CappedEnrollmentLearnerCreditAccessPolicy(SubsidyAccessPolicy, CreditPolicyMixin):
    """
    Policy that limits the maximum amount that can be spent aggregated across all users covered by this policy.

    .. no_pii: This model has no PII
    """

    objects = PolicyManager()
    class Meta:
        """
        Metaclass for CappedEnrollmentLearnerCreditAccessPolicy.
        """
        proxy = True

    def can_redeem(self, learner_id, content_key):
        group_amount_spent = subsidy_client.amount_spent_for_group_and_catalog(
            subsidy_uuid = self.subsidy_uuid,
            group_uuid = self.group_uuid,
            catalog_uuid = self.catalog_uuid,
            )
        course_price = DiscoveryApiClient().get_course_price(content_key)
        if  (group_amount_spent + course_price) < self.spend_limit:
            return super().can_redeem(learner_id, content_key)

        return False
