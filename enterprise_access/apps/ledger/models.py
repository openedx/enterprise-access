import collections
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.dispatch import receiver
from django.utils.translation import ugettext_lazy as _
from jsonfield.encoder import JSONEncoder
from jsonfield.fields import JSONField
from model_utils.models import SoftDeletableModel, TimeStampedModel
from simple_history.models import HistoricalRecords
from simple_history.utils import bulk_update_with_history

# Create your models here.

class UnitChoices:
    USD_CENTS = 'usd_cents'
    SEATS = 'seats'
    JPY = 'jpy'
    CHOICES = (
        (USD_CENTS, 'U.S. Dollar (Cents)'),
        (SEATS, 'Seats in a course'),
        (JPY, 'Japanese Yen'),
    )


class Ledger(TimeStampedModel):
    """
    A ledger you can credit and debit, associated with a single subsidy plan.
    """
    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
    )
    metadata = JSONField(
        blank=True,
        null=True,
    )


class Transaction(TimeStampedModel):
    """
    Represents a quantity moving in or out of the ledger.  It's purely in USD-cents for now.
    """
    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
    )
    ledger = models.ForeignKey(
        Ledger,
        related_name='transactions',
        null=True,
        on_delete=models.SET_NULL,
    )
    quantity = models.BigIntegerField(
        null=False,
        blank=False,
    )
    unit = models.CharField(
        max_length=1024,
        blank=False,
        null=False,
        choices=UnitChoices.CHOICES,
        default=UnitChoices.USD_CENTS,
        db_index=True,
    )
    metadata = JSONField(
        blank=True,
        null=True,
    )


class Reversal(TimeStampedModel):
    """
    Represents a reversal of some or all of a transaction, but no more.
    """
    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
    )
    transaction = models.ForeignKey(
        Ledger,
        related_name='reversals',
        null=True,
        on_delete=models.SET_NULL,
    )
    quantity = models.IntegerField(
        null=False,
        blank=False,
    )
    metadata = JSONField(
        blank=True,
        null=True,
    )
