from datetime import datetime
from functools import lru_cache
import mock
from uuid import uuid4

from pytz import UTC

from django.db import models
from model_utils.models import TimeStampedModel

from enterprise_access.apps.ledger import api as ledger_api
from enterprise_access.apps.ledger.utils import create_idempotency_key_for_transaction
from enterprise_access.apps.ledger.models import Ledger, UnitChoices


MOCK_GROUP_CLIENT = mock.MagicMock()
MOCK_CATALOG_CLIENT = mock.MagicMock()
MOCK_ENROLLMENT_CLIENT = mock.MagicMock()

CENTS_PER_DOLLAR = 100


def now():
    return UTC.localize(datetime.utcnow())


class TimeStampedModelWithUuid(TimeStampedModel):
    class Meta:
        abstract = True

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
    )


class Subsidy(TimeStampedModelWithUuid):
    """
    """
    class Meta:
        abstract = True

    starting_balance = models.BigIntegerField(
        null=False, blank=False,
    )
    ledger = models.ForeignKey(Ledger, null=True, on_delete=models.SET_NULL)
    unit = models.CharField(
        max_length=255,
        blank=False,
        null=False,
        choices=UnitChoices.CHOICES,
        default=UnitChoices.USD_CENTS,
        db_index=True,
    )
    opportunity_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )
    customer_uuid = models.CharField(  # would really be a UUID
        max_length=255,
    )
    active_datetime = models.DateTimeField(null=True, default=None)
    expiration_datetime = models.DateTimeField(null=True, default=None)

    @property
    def catalog_client(self):
        return MOCK_CATALOG_CLIENT

    def current_balance(self):
        return self.ledger.balance()

    def create_transaction(self, idempotency_key, quantity, metadata):
        return ledger_api.create_transaction(
            ledger=self.ledger,
            quantity=quantity,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )

    def commit_transaction(self, transaction, reference_id):
        transaction.reference_id = reference_id
        transaction.save()

    def rollback_transaction(self, transaction):
        # delete it, or set some state?
        pass

    def is_redeemable(self, learner_id, content_key, redemption_datetime=None):
        raise NotImplementedError

    def redeem(self, learner_id, content_key, **kwargs):
        # The subsidy should determine the redemption quantity
        raise NotImplementedError


class LearnerCreditSubsidy(Subsidy):
    """
    A subsidy model for Learner Credit/bucket of money.
    """
    @property
    def enrollment_client(self):
        return MOCK_ENROLLMENT_CLIENT

    @lru_cache(maxsize=128)
    def price_for_content(self, content_key):
        return self.catalog_client.get_content_metadata(content_key).get('price') * CENTS_PER_DOLLAR

    def is_redeemable(self, learner_id, content_key, redemption_datetime=None):
        return self.current_balance() >= self.price_for_content(content_key)

    def redeem(self, learner_id, content_key, **kwargs):
        """
        Actual enrollment happens downstream of this.
        commit a transaction here.
        """
        if not self.is_redeemable(learner_id, content_key, now()):
            return

        transaction_metadata = {
            'content_key': content_key,
            'learner_id': learner_id,
        }

        quantity = self.price_for_content(content_key)

        idempotency_key = create_idempotency_key_for_transaction(
            self,
            quantity,
            learner_id=learner_id,
            content_key=content_key,
        )
        transaction = self.create_transaction(
            idempotency_key,
            quantity * -1,
            transaction_metadata,
        )

        try:
            reference_id = self.enrollment_client.enroll(learner_id, content_key, transaction)
            self.commit_transaction(transaction, reference_id)
        except Exception as exc:
            self.rollback_transaction(transaction)
            raise exc

        return transaction


class SubscriptionSubsidy(Subsidy):
    subscription_plan_uuid = models.UUIDField(null=False, blank=False, db_index=True)

    class Meta:
        # The choice of what a subsidy is unique on dictates behavior
        # that we can implement around the lifecycle of the subsidy.
        # For instance, making this type of subsidy unique on the (customer, plan id, unit)
        # means that every renewal or roll-over of a plan must result in a new plan id.
        unique_together = []

    @property
    def subscription_client(self):
        mock_client = mock.MagicMock()
        mock_client.get_plan_metadata.return_value = {
            'licenses': {
                'pending': 0,
                'total': 0,
            },
        }
        mock_client.create_license.return_value = uuid4()
        mock_client.get_license.return_value = {
            'uuid': uuid4(),
            'status': 'activated',
        }
        return mock_client

    def is_license_available(self, learner_id):
        """
        """
        plan_metadata = self.subscription_client.get_plan_metadata(
            self.subscription_plan_uuid,
        )
        return plan_metadata['licenses']['pending'] > 0

    def get_license_for_learner(self, learner_id):
        return self.subscription_client.get_license(
            self.subscription_plan_uuid,
            learner_id,
        )

    def assign_license(self, learner_id, **kwargs):
        """
        Calls an subscription API client to grant a license as a redemption
        for this subsidy.
        """
        # Note: licenses are created when the plan is created
        # so we're not creating a new one, here.
        license_metadata = self.subscription_client.assign_license(
            self.subscription_plan_uuid,
            learner_id,
        )
        return license_metadata

    def is_redeemable(self, learner_id, content_key, redemption_datetime=None):
        return self.get_license_for_learner(learner_id) or self.is_license_available(learner_id)

    def redeem(self, learner_id, content_key, **kwargs):        # pylint: disable=unused-argument
        """
        For subscription subsidies, a redemption is either the fact that the
        learner is already assigned a license for the plan, or the result
        of assigning an available license to the learner.
        """
        assigned_license = self.get_license_for_learner(learner_id)
        if not assigned_license:
            assigned_license = self.assign_license(learner_id)
        return assigned_license


class SubsidyAccessPolicy(TimeStampedModelWithUuid):
    """
    (group, subsidy, catalog, access method, and optional total value)
    """
    class Meta:
        abstract = True

    group_uuid = models.UUIDField(null=True, blank=True, db_index=True)
    # children must define this FK, probably
    # subsidy = models.ForeignKey(Subsidy, on_delete=models.SET_NULL)
    catalog_uuid = models.UUIDField(null=True, blank=True, db_index=True)
    access_method = None

    @property
    def group_client(self):
        return MOCK_GROUP_CLIENT

    @classmethod
    def get_policies_for_groups(cls, group_uuids):
        return cls.objects.filter(group_uuid__in=group_uuids)

    @property
    def catalog_client(self):
        return MOCK_CATALOG_CLIENT

    def is_entitled(self, learner_id, content_key):
        if not self.catalog_client.catalog_contains_content(self.catalog_uuid, content_key):
            return False

        if not self.group_client.group_contains_learner(self.group_uuid, learner_id):
            return False

        elif self.subsidy.is_redeemable(learner_id, content_key):
            return True

        return False

    def use_entitlement(self, learner_id, content_key):
        if self.is_entitled(learner_id, content_key):
            return self.subsidy.redeem(learner_id, content_key)
        return False


class SubscriptionAccessPolicy(SubsidyAccessPolicy):
    """
    """
    subsidy = models.ForeignKey(SubscriptionSubsidy, null=True, on_delete=models.SET_NULL)


class LearnerCreditAccessPolicy(SubsidyAccessPolicy):
    """
    """
    subsidy = models.ForeignKey(LearnerCreditSubsidy, null=True, on_delete=models.SET_NULL)


"""
TODO: There are different flavors of LearnerCreditAccessPolicy
that correspond to different policy rules.
"""
