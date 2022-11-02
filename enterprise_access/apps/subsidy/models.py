import collections
import datetime
import mock
from uuid import uuid4

from pytz import UTC

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Coalesce
from django.db.transaction import atomic
from django.dispatch import receiver
from django.utils.translation import ugettext_lazy as _
from jsonfield.encoder import JSONEncoder
from jsonfield.fields import JSONField
from model_utils.models import SoftDeletableModel, TimeStampedModel
from simple_history.models import HistoricalRecords
from simple_history.utils import bulk_update_with_history

from enterprise_access.apps.ledger import api as ledger_api
from enterprise_access.apps.ledger.models import Ledger, UnitChoices


MOCK_GROUP_CLIENT = mock.MagicMock()

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
    customer_uuid = models.CharField( # would really be a UUID
        max_length=255,
    )
    active_datetime = models.DateTimeField(null=True, default=None)
    expiration_datetime = models.DateTimeField(null=True, default=None)


    def current_balance(self):
        return self.ledger.balance()

    def get_content_metadata(self, content_key):
        """
        TODO: 
        Get this from the catalog service.
        Cache the result.
        It'll have the price (relevant for learner credit subsidy types).
        """
        return {}

    def get_quantity_for_content(self, content_metadata):
        raise NotImplementedError

    def create_idempotency_key(self, learner_id, content_metadata, quantity, **kwargs):
        raise NotImplementedError(kwargs)

    def create_transaction(self, idempotency_key, quantity, metadata):
        ledger_api.create_transaction(
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

    def is_entitled(self, learner_id):
        raise NotImplementedError

    def create_entitlement(self, learner_id, **kwargs):
        raise NotImplementedError

    def is_redeemable(self, learner_id, content_key, redemption_datetime=None):
        raise NotImplementedError

    def create_redemption(self, learner_id, content_key, transaction=None, **kwargs):
        raise NotImplementedError


class LearnerCreditSubsidy(Subsidy):
    def is_entitled(self, learner_id):
        return self.current_balance() > 0

    def create_entitlement(self, learner_id, **kwargs):
        """
        No-op: they’ll use credit dollars just-in-time on redemption
        """
        return True

    def is_redeemable(self, learner_id, content_key, redemption_datetime=None):
        # TODO: consider the price of the content
        price_less_than_balance = True
        return self.is_entitled(learner_id) and price_less_than_balance

    def create_redemption(self, learner_id, content_key, transaction=None, **kwargs):
        """
        Actual enrollment happens downstream of this.
        commit a transaction here.
        """
        raise NotImplementedError


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

    def is_entitled(self, learner_id):
        """
        1. Is there a license unit available in this subsidy?
        """
        plan_metadata = self.subscription_client.get_plan_metadata(
            self.subscription_plan_uuid,
        )
        return plan_metadata['licenses']['pending'] > 0


    def create_entitlement(self, learner_id, **kwargs):
        """
        Calls an subscription API client to grant a license as a redemption
        for this subsidy.
        """
        # TODO: this is really license assignment.  licenses are created
        # when the plan is created.
        license_metadata = self.subscription_client.create_license(
            self.subscription_plan_uuid,
            learner_id,
        )
        return license_metadata

    def is_redeemable(self, learner_id, content_key, redemption_datetime=None):
        return bool(self.get_license_for_learner(learner_id))

    def create_redemption(self, learner_id, content_key, transaction=None, **kwargs):
        return True

    def get_quantity_for_content(self, content_metadata):
        return 1

    def bulk_create_entitlement(self, *args, **kwargs):
        # TODO
        pass

    def get_license_for_learner(self, learner_id):
        return self.subscription_client.get_license(
            self.subscription_plan_uuid,
            learner_id,
        )

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
    total_value = models.BigIntegerField(
        null=True, blank=True,
    )

    @property
    def group_client(self):
        return MOCK_GROUP_CLIENT

    @classmethod
    def get_policies_for_groups(cls, group_uuids):
        return cls.objects.filter(group_uuid__in=group_uuids)

    @property
    def catalog_client(self):
        return mock.MagicMock()

    def is_learner_entitled_to_subsidy(self, learner_id):
        raise NotImplementedError

    def give_entitlement_to_subsidy(self, learner_id):
        raise NotImplementedError

    def can_learner_redeem_for_content(self, learner_id, content_key):
        raise NotImplementedError

    def redeem_for_content(self, learner_id, content_key):
        raise NotImplementedError


class SubscriptionAccessPolicy(SubsidyAccessPolicy):
    """
    """
    subsidy = models.ForeignKey(SubscriptionSubsidy, null=True, on_delete=models.SET_NULL)

    def is_learner_entitled_to_subsidy(self, learner_id):
        if self.subsidy.get_license_for_learner(learner_id):
            return True

        group_uuids = {
            group['uuid'] for group
            in self.group_client.get_groups_for_learner(learner_id, self.subsidy.customer_uuid)
        }
        if self.group_uuid in group_uuids:
            if self.subsidy.is_entitled(learner_id):
                return True

        return False

    def give_entitlement_to_subsidy(self, learner_id):
        if _license := self.subsidy.get_license_for_learner(learner_id):
            return _license

        return self.subsidy.create_entitlement(learner_id)

    def can_learner_redeem_for_content(self, learner_id, content_key):
        """
        1. is the content in this policy's catalog?
        2. does the learner have a license?
        """
        if self.catalog_client.catalog_contains_content(self.catalog_uuid, content_key):
            return self.subsidy.is_redeemable(learner_id, content_key)

        return False

    def redeem_for_content(self, learner_id, content_key):
        if self.can_learner_redeem_for_content(learner_id, content_key):
            return self.subsidy.create_redemption(learner_id, content_key)
        return False


class LearnerCreditAccessPolicy(SubsidyAccessPolicy):
    """
    """
    subsidy = models.ForeignKey(LearnerCreditSubsidy, null=True, on_delete=models.SET_NULL)

    def is_learner_entitled_to_subsidy(self, learner_id):
        """
        True if the learner is in a group in the customer that can use credit for something.
        """
        group_uuids = {
            group['uuid'] for group
            in self.group_client.get_groups_for_learner(learner_id, self.subsidy.customer_uuid)
        }
        if self.group_uuid in group_uuids:
            if self.subsidy.is_entitled(learner_id):
                return True
        else:
            raise Exception('learner not in group')
                
        return False

    def give_entitlement_to_subsidy(self, learner_id):
        return self.subsidy.create_entitlement(learner_id)

    def can_learner_redeem_for_content(self, learner_id, content_key):
        """
        True if:
        - learner is entitled to this policy (which takes current balance into account)
        - and content is in catalog for this policy
        """
        if self.catalog_client.catalog_contains_content(self.catalog_uuid, content_key):
            if self.is_learner_entitled_to_subsidy(learner_id):
                return self.subsidy.is_redeemable(learner_id, content_key)

        return False

    def redeem_for_content(self, learner_id, content_key):
        """
        Transaction here Record the transaction and let them enroll.
        """
        if self.can_learner_redeem_for_content(learner_id, content_key):
            return self.subsidy.create_redemption(learner_id, content_key)
        return False
