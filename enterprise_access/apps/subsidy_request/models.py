""" Models for subsidy_request. """

from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import ugettext_lazy as _
from model_utils.models import SoftDeletableModel, TimeStampedModel
from simple_history.models import HistoricalRecords
from simple_history.utils import bulk_update_with_history

from enterprise_access.apps.subsidy_request.constants import (
    SUBSIDY_REQUEST_BULK_OPERATION_BATCH_SIZE,
    SubsidyRequestStates,
    SubsidyTypeChoices
)
from enterprise_access.apps.subsidy_request.utils import localized_utcnow


class SubsidyRequest(TimeStampedModel, SoftDeletableModel):
    """
    Stores information related to a request for a subsidy (license or coupon).

    .. no_pii: This model has no PII
    """

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="%(app_label)s_%(class)s",
        on_delete=models.CASCADE,
    )

    course_id = models.CharField(
        null=True,
        blank=True,
        max_length=128
    )

    enterprise_customer_uuid = models.UUIDField()

    state = models.CharField(
        max_length=25,
        blank=False,
        null=False,
        choices=SubsidyRequestStates.CHOICES,
        default=SubsidyRequestStates.REQUESTED
    )

    reviewed_at = models.DateTimeField(
        null=True,
        blank=True
    )

    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="reviewed_%(app_label)s_%(class)s",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )

    decline_reason = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    def approve(self, reviewer):
        raise NotImplementedError

    def decline(self, reviewer, reason):
        raise NotImplementedError

    def clean(self):
        if self.state != SubsidyRequestStates.REQUESTED:
            if not (self.reviewed_at and self.reviewer):
                raise ValidationError('Both reviewer and reviewed_at are required for a review.')

        return super().clean()

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @classmethod
    def bulk_update(cls, subsidy_requests, field_names, batch_size=SUBSIDY_REQUEST_BULK_OPERATION_BATCH_SIZE):
        """
        django-simple-history functions by saving history using a post_save signal every time that
        an object with history is saved. However, for certain bulk operations, such as bulk_create, bulk_update,
        and queryset updates, signals are not sent, and the history is not saved automatically.
        However, django-simple-history provides utility functions to work around this.

        https://django-simple-history.readthedocs.io/en/2.12.0/common_issues.html#bulk-creating-and-queryset-updating
        """
        bulk_update_with_history(subsidy_requests, cls, field_names, batch_size=batch_size)

    class Meta:
        abstract = True


class LicenseRequest(SubsidyRequest):
    """
    Stores information related to a license request.

    .. no_pii: This model has no PII
    """

    subscription_plan_uuid = models.UUIDField(
        null=True,
        blank=True
    )

    license_uuid = models.UUIDField(
        null=True,
        blank=True
    )

    history = HistoricalRecords()

    def clean(self):
        if self.state == SubsidyRequestStates.APPROVED:
            if not (self.subscription_plan_uuid and self.license_uuid):
                raise ValidationError(
                    'Both subscription_plan_uuid and license_uuid are required for a fulfilled license request.'
                )

        return super().clean()

    def __str__(self):
        """
        Return human-readable string representation.
        """
        if self.course_id:
            return f'<LicenseRequest for user {self.user} and course {self.course_id}>'
        return f'<LicenseRequest for user {self.user}>'

    def approve(self, reviewer):
        self.reviewer = reviewer
        self.state = SubsidyRequestStates.PENDING
        self.reviewed_at = localized_utcnow()
        self.save()

    def decline(self, reviewer, reason=None):
        self.reviewer = reviewer
        self.state = SubsidyRequestStates.DECLINED
        self.decline_reason = reason
        self.reviewed_at = localized_utcnow()
        self.save()


class CouponCodeRequest(SubsidyRequest):
    """
    Stores information related to a coupon code request.

    .. no_pii: This model has no PII
    """

    coupon_id = models.IntegerField(
        null=True,
        blank=True
    )

    coupon_code = models.CharField(
        null=True,
        blank=True,
        max_length=128
    )

    history = HistoricalRecords()

    def clean(self):
        if self.state == SubsidyRequestStates.APPROVED:
            if not (self.coupon_id and self.coupon_code):
                raise ValidationError(
                    'Both coupon_id and coupon_code are required for a fulfilled coupon request.'
                )

        return super().clean()

    def __str__(self):
        """
        Return human-readable string representation.
        """
        if self.course_id:
            return f'<CouponCodeRequest for user {self.user} and course {self.course_id}>'
        return f'<CouponCodeRequest for user {self.user}>'

    def approve(self, reviewer):
        self.reviewer = reviewer
        self.state = SubsidyRequestStates.PENDING
        self.reviewed_at = localized_utcnow()
        self.save()

    def decline(self, reviewer, reason=None):
        self.reviewer = reviewer
        self.state = SubsidyRequestStates.DECLINED
        self.decline_reason = reason
        self.reviewed_at = localized_utcnow()
        self.save()


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
