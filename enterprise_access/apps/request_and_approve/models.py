""" request_and_approve models. """

from django.db import models
from django.utils.translation import ugettext_lazy as _

from model_utils.models import TimeStampedModel

from simple_history.models import HistoricalRecords

from enterprise_access.apps.request_and_approve.constants import (
    PendingRequestReminderFrequency,
    SubsidyTypeChoices,
)


class SubsidyRequestCustomerConfiguration(TimeStampedModel):
    """
    Stores request_and_approve configuration for a customers

    .. no_pii: This model has no PII
    """

    enterprise_customer_uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
    )

    subsidy_requests_enabled = models.BooleanField(
        default=False,
        help_text=_(
            "Whether or not subsidy requests are enabled for an enterprise."
        )
    )

    subsidy_type = models.CharField(
        max_length=32,
        blank=False,
        null=False,
        choices=SubsidyTypeChoices.CHOICES,
        help_text=("Which type of subsidy is used to grant access."),
    )

    pending_request_reminder_frequency = models.CharField(
        max_length=32,
        blank=False,
        null=False,
        choices=PendingRequestReminderFrequency.CHOICES,
        help_text=(
            "How frequently to send reminders to admins that there "
            "are requests pending."
        ),
    )

    changed_by = models.TextField(
        blank=False,
        null=False,
        help_text=(
            "Name of (admin) user who makes a change to this config object."
        ),
    )

    history = HistoricalRecords()


    def save(self, *args, **kwargs):
        # Do something here to determine which user is saving the record
        super().save(*args, **kwargs)
