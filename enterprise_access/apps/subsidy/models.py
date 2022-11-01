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
        Get this from the catalog service.
        Cache the result.
        It'll have the price (relevant for learner credit subsidy types.
        """
        return {}

    def get_quantity_for_content(self, content_metadata):
        raise NotImplementedError

    def redeem_content(self, learner_id, content_key):
        """
        This function should:
        1. check is_redeemable()
        2. Create a transaction.
        3. Create the redemption.
        4. Commit the transaction.
        """
        content_metadata = self.get_content_metadata(content_key)
        quantity = self.get_quantity_for_content(content_metadata)

        if not self.is_redeemable(learner_id, content_key, now()):
            return

        transaction_metadata = {
            'content_metadata': content_metadata,
            'learner_id': learner_id,
        }

        idempotency_key = self.idemptotency_key(learner_id, content_metadata, quantity)
        transaction = self.create_transaction(idempotency_key, quantity, transaction_metadata)

        try:
            reference_id = self.create_redemption(learner_id, content_metadata, transaction)
            self.commit_transaction(transaction, reference_id)
        except:
            self.rollback_transaction(transaction)

        return transaction

    def create_idempotency_key(self, learner_id, content_metadata, quantity, **kwargs):
        raise NotImplementedError(kwargs)

    def create_transaction(self, idempotency_key, quantity, metadata):
        ledger_api.create_transaction(
            ledger=self.ledger,
            quantity=quantity,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )

    def create_redemption(self, learner_id, content_metadata, transaction, **kwargs):
        raise NotImplementedError

    def commit_transaction(self, transaction, reference_id):
        transaction.reference_id = reference_id
        transaction.save()

    def rollback_transaction(self, transaction):
        # delete it, or set some state?
        pass

    def is_redeemable(self, learner_id, content_key, redemption_datetime):
        raise NotImplementedError


class LearnerCreditSubsidy(Subsidy):
    def create_redemption(self, learner_id, **kwargs):
        """
        Calls an enrollment API client to enroll learner in course.
        """


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
            },
        }
        mock_client.create_license.return_value = uuid4()
        mock_client.get_license.return_value = {
            'uuid': uuid4(),
            'status': 'activated',
        }
        return mock_client

    def is_redeemable(self, learner_id):
        """
        1. Is there a license unit available in this subsidy?
        """
        plan_metadata = self.subscription_client.get_plan_metadata(
            self.subscription_plan_uuid,
        )
        return plan_metadata['licenses']['pending'] > 0

        
    def create_redemption(self, learner_id, **kwargs):
        """
        Calls an subscription API client to grant a license as a redemption
        for this subsidy.
        """
        license_metadata = self.subscription_client.create_license(
            self.subscription_plan_uuid,
            learner_id,
        )
        return license_metadata

    def get_quantity_for_content(self, content_metadata):
        return 1

    def bulk_create_redemption(self, *args, **kwargs):
        # TODO
        pass

    def get_license_for_learner(self, learner_id):
        return self.subscription_client.get_license(
            self.subscription_plan_uuid,
            learner_id,
        )


class SubsidyAccessMethod(TimeStampedModelWithUuid):
    """    
    """
    # e.g. for licenses, this is just "license"
    method_label = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
    )
    def can_access(self, learner_id, subsidy):
        # access qua access, but also, have I hit my limit?
        pass


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

    @classmethod
    def get_policies_for_groups(cls, group_uuids):
        return cls.objects.filter(group_uuid__in=group_uuids)

    @property
    def group_client(self):
        """
        Use a groups API to find these.
        """
        return mock.MagicMock()

    @property
    def catalog_client(self):
        return mock.MagicMock()

    @classmethod
    def get_policies_for_learner_and_content(cls, learner_id, customer_uuid, content_key):
        """
        1. Get the groups for the learner.
        2. Get the policies for the customer and these groups.
        3. Filter, using the catalog service, to only the subsidies with a catalog that contains
        the given content_key.
        """
        group_uuids = [group['uuid'] for group in cls.group_client.get_groups_for_learner(learner_id, customer_uuid)]
        policies_by_catalog = {
            policy.catalog_uuid: policy
            for policy in cls.objects.filter(
                group_uuid__in=group_uuids,
                customer_uuid=customer_uuid,
            )
        }
        """
        In enterprise-catalog, we'd like an endpoint that, given a list of catalog ids
        and a piece of content, tells us which catalogs contain said content.
        """
        # wave hands about the above using policies_by_catalog.keys()
        # TODO: there's a preferred ordering of subsidy access methods.
        # e.g. license, then credit, then whatevs
        matching_catalog_uuids = []
        return [
            policies_by_catalog.get(matching_catalog_uuid)
            for matching_catalog_uuid in matching_catalog_uuids
        ]

    @classmethod
    def policy_for_which_learner_is_entitled_to_content(
        cls, learner_id, customer_uuid, content_key, redeem_at=None
    ):
        matching_policies = cls.get_policies_for_learner_and_content(
            learner_id, customer_uuid, content_key,
        )
        for policy in matching_policies:
            if policy.is_redeemable(learner_id, content_key, redeem_at or now()):
                return policy

    @classmethod
    def give_entitlement_to_content_in_some_policy(
            cls, learner_id, customer_uuid, content_key, redeem_at=None
    ):
        policy = cls.redeemable_policy(learner_id, customer_uuid, content_key, redeem_at)
        if not policy:
            return
        try:
            return subsidy.redeem_content(learner_id, content_key)
        except:
            raise

    def is_learner_entitled_to_policy(self, learner_id):
        raise NotImplementedError

    def give_entitlement(self, learner_id):
        raise NotImplementedError
        
    def is_learner_entitled_to_content_in_policy(self, learner_id, content_key):
        raise NotImplementedError
        
    def give_entitlement_for_content(self, learner_id, content_key):
        raise NotImplementedError

    
class SubscriptionAccessPolicy(SubsidyAccessPolicy):
    """
    """
    subsidy = models.ForeignKey(SubscriptionSubsidy, null=True, on_delete=models.SET_NULL)

    def get_license_for_learner(self, learner_id):
        """
        Fetch any existing, active, current license for this learner/customer pair.
        """
        return self.subsidy.get_license_for_learner(learner_id)

    def is_learner_entitled_to_policy(self, learner_id):
        if self.get_license_for_learner(learner_id):
            return True

        group_uuids = {
            group['uuid'] for group
            in self.group_client.get_groups_for_learner(learner_id, self.subsidy.customer_uuid)
        }
        if self.group_uuid in group_uuids:
            if self.subsidy.is_redeemable(learner_id):
                return True

        return False

    def give_entitlement(self, learner_id):
        if _license := self.get_license_for_learner(learner_id):
            return _license

        return self.subsidy.create_redemption(learner_id)

    def is_learner_entitled_to_content_in_policy(self, learner_id, content_key):
        """
        1. is the content in this policy's catalog?
        2. does the learner have a license?
        """
        if self.catalog_client.catalog_contains_content(self.catalog_uuid, content_key):
            if self.get_license_for_learner(learner_id):
                return True

        return False

    def give_entitlement_for_content(self, learner_id, content_key):
        if self.is_learner_entitled_to_content_in_policy(learner_id, content_key):
            return True
        return False
