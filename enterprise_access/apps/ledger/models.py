import collections
from uuid import uuid4

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

# Units on the ledger model - probably yes. TODO: say why
# Unit conversion - price/seat is captured somewhere, is it in here?
# This is currently single-entry - should it be double-entry?
# Do we need an account representing SF, one representing entitlements or enrollments, and the transaction
# moves between those two accounts?
# Are transactions immutable? Yes.
# Let's not persist aggregates - the transactions are the only truth. TODO: say why

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

# https://docs.djangoproject.com/en/3.2/ref/databases/#mysql-character-fields
# is why max_length is 255 for the fields below.

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
    # todo: make a mixin using AbstractModel or something
    # also note: if you do something that raises an exception and causes the record to not persist,
    # idempotency is not preserved (i.e. you could do an action with the same key in a way that
    # _does not_ raise an exception and get a different, non exception, output).
    idempotency_key = models.CharField(
        max_length=255,
        blank=False,
        null=False,
        unique=True,
    )
    unit = models.CharField(
        max_length=255,
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

    def balance(self):
        with atomic():
            transactions = Transaction.objects.filter(
                ledger=self,
            ).select_related('reversal')
            agg = transactions.annotate(
                row_total=Coalesce(models.Sum('quantity'), 0) + Coalesce(models.Sum('reversal__quantity'), 0)
            )
            agg = agg.aggregate(total_quantity=Coalesce(models.Sum('row_total'), 0))
            return agg['total_quantity']


class Transaction(TimeStampedModel):
    """
    Represents a quantity moving in or out of the ledger.  It's purely in USD-cents for now.
    """
    class Meta:
        unique_together = [('ledger', 'idempotency_key')]

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
    )
    idempotency_key = models.CharField(
        max_length=255,
        blank=False,
        null=False,
        db_index=True,
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
    metadata = JSONField(
        blank=True,
        null=True,
    )


class Reversal(TimeStampedModel):
    """
    Represents a reversal of some or all of a transaction, but no more.
    """
    class Meta:
        unique_together = [('transaction', 'idempotency_key')]

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
    )
    idempotency_key = models.CharField(
        max_length=255,
        blank=False,
        null=False,
        db_index=True,
    )
    transaction = models.OneToOneField(
        Transaction,
        related_name='reversal',
        null=True,
        on_delete=models.SET_NULL,
    )
    # these are always the opposite sign of the transaction (i.e. negative)
    # have to enforce this somehow...
    quantity = models.IntegerField(
        null=False,
        blank=False,
    )
    metadata = JSONField(
        blank=True,
        null=True,
    )
