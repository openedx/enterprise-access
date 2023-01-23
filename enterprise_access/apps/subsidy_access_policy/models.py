""" Models for subsidy_access_policy """

from abc import abstractmethod
from uuid import uuid4

from django.db import models
from django_extensions.db.models import TimeStampedModel
from simple_history.models import HistoricalRecords

from enterprise_access.apps.subsidy_access_policy.constants import AccessMethods
from enterprise_access.apps.subsidy_access_policy.mocks import catalog_client, group_client, subsidy_client


class SubsidyAccessPolicy(TimeStampedModel):
    """
    Tie together information used to control access to a subsidy.
    This abstract model joins group, catalog, and access method.

    .. no_pii:
    """
    class Meta:
        """
        Metaclass for SubsidyAccessPolicy.
        """
        abstract = True

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
    )
    active = models.BooleanField(default=False)
    group_uuid = models.UUIDField(null=True, blank=True, db_index=True)
    catalog_uuid = models.UUIDField(null=True, blank=True, db_index=True)
    subsidy_uuid = models.UUIDField(null=False, blank=False, db_index=True)
    access_method = models.CharField(
        max_length=25,
        blank=False,
        null=False,
        choices=AccessMethods.CHOICES,
        default=AccessMethods.DIRECT,
        db_index=True
    )
    history = HistoricalRecords()

    @property
    def enterprise_customer_uuid(self):
        "Returns the enterprise_customer_uuid. Equivalent to the group uuid at present"
        return self.group_uuid

    @abstractmethod
    def can_redeem(self, learner_id, content_key):
        """
        Check that a given learner can redeem the given content.
        """
        if not catalog_client.catalog_contains_content(self.catalog_uuid, content_key):
            return False
        if not group_client.group_contains_learner(self.group_uuid, learner_id):
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
        if self.can_redeem(learner_id, content_key):
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


class SubscriptionAccessPolicy(SubsidyAccessPolicy):
    """
    A subsidy access policy for subscriptions.

    .. no_pii:
    """

    def can_redeem(self, learner_id, content_key):
        if subsidy_client.get_license_for_learner(self.subsidy_uuid, learner_id):
            return super().can_redeem(learner_id, content_key)

        group_uuids = list(group_client.get_groups_for_learner(learner_id))
        if self.group_uuid in group_uuids:
            if subsidy_client.get_license_for_group(self.subsidy_uuid, self.group_uuid):
                return super().can_redeem(learner_id, content_key)

        return False


class LearnerCreditAccessPolicy(SubsidyAccessPolicy):
    """
    A subsidy access policy for learner credit.

    .. no_pii:
    """
    class Meta:
        """
        Metaclass for LearnerCreditAccessPolicy.
        """
        abstract = True

    def can_redeem(self, learner_id, content_key): # lint-amnesty, pylint: disable=useless-parent-delegation
        """
        Check that a given learner can redeem the given content.
        """
        return super().can_redeem(learner_id, content_key)


class LicenseRequestAccessPolicy(SubscriptionAccessPolicy):
    """
    A subsidy access policy for subscriptions with a request type access method.
    """


class LicenseAccessPolicy(SubscriptionAccessPolicy):
    """
    A subsidy access policy for subscriptions with a direct type access method.
    """


class PerLearnerEnrollmentCapLearnerCreditAccessPolicy(LearnerCreditAccessPolicy):
    """
    Policy that limits the number of enrollments transactions for a learner in a subsidy.
    """
    per_learner_enrollment_limit = models.IntegerField(
        blank=True,
        default=0,
    )

    def can_redeem(self, learner_id, content_key):
        learner_transaction_count = subsidy_client.transactions_for_learner(
            subsidy_uuid = self.subsidy_uuid,
            learner_id = learner_id,
            ).count()
        if learner_transaction_count < self.per_learner_enrollment_limit:
            return super().can_redeem(learner_id, content_key)

        return False


class PerLearnerSpendCapLearnerCreditAccessPolicy(LearnerCreditAccessPolicy):
    """
    Policy that limits the amount of learner spend for enrollment transactions.
    """
    per_learner_spend_limit = models.IntegerField(
        blank=True,
        default=0,
    )

    def can_redeem(self, learner_id, content_key):
        spent_amount = subsidy_client.amount_spent_for_learner(
            subsidy_uuid = self.subsidy_uuid,
            learner_id = learner_id,
            )
        course_price = catalog_client.get_course_price(content_key)
        if (spent_amount + course_price) < self.per_learner_spend_limit:
            return super().can_redeem(learner_id, content_key)

        return False


class CappedEnrollmentLearnerCreditAccessPolicy(LearnerCreditAccessPolicy):
    """
    Policy that limits the maximum amount that can be spent aggregated across all users covered by this policy
    """
    spend_limit = models.IntegerField(
        blank=True,
        default=0,
    )

    def can_redeem(self, learner_id, content_key):
            group_amount_spent = subsidy_client.amount_spent_for_group_and_catalog(
                subsidy_uuid = self.subsidy_uuid,
                group_uuid = self.group_uuid,
                catalog_uuid = self.catalog_uuid,
                )
            course_price = catalog_client.get_course_price(content_key)
            if  (group_amount_spent + course_price) < self.spend_limit:
                return super().can_redeem(learner_id, content_key)

            return False
