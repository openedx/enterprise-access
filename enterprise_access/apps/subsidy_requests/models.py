""" subsidy_requests models. """

from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils.translation import ugettext_lazy as _

from model_utils.models import TimeStampedModel

from simple_history.models import HistoricalRecords

from enterprise_access.apps.subsidy_requests.constants import (
    PendingRequestReminderFrequency,
    SubsidyTypeChoices,
)


class SubsidyRequestCustomerConfiguration(TimeStampedModel):
    """
    Stores subsidy_requests configuration for a customers

    .. no_pii: This model has no PII
    """

    enterprise_customer_uuid = models.UUIDField(
        primary_key=True,
    )

    subsidy_requests_enabled = models.BooleanField(
        default=False,
        help_text=_(
            "Whether or not subsidy requests are enabled for an enterprise."
        )
    )

    subsidy_type = models.CharField(
        max_length=32,
        blank=True,
        null=True,
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

    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
    )

    history = HistoricalRecords()

    @property
    def _history_user(self):
        return self.changed_by

    @_history_user.setter
    def _history_user(self, value):
        self.changed_by = value
