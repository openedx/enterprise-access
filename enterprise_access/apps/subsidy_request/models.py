""" Models for subsidy_request. """

from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import ugettext_lazy as _
from model_utils.models import TimeStampedModel
from simple_history.models import HistoricalRecords

from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates, SubsidyTypeChoices


class SubsidyRequest(TimeStampedModel):
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

    lms_user_id = models.IntegerField()

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
        default=SubsidyRequestStates.PENDING_REVIEW
    )

    reviewed_at = models.DateTimeField(
        null=True,
        blank=True
    )

    reviewer_lms_user_id = models.IntegerField(
        null=True,
        blank=True
    )

    denial_reason = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    def approve(self, reviewer_lms_user_id):
        raise NotImplementedError

    def deny(self, reviewer_lms_user_id, reason):
        raise NotImplementedError

    def clean(self):
        if self.state != SubsidyRequestStates.PENDING_REVIEW:
            if not (self.reviewed_at and self.reviewer_lms_user_id):
                raise ValidationError('Both reviewer_lms_user_id and reviewed_at are required for a review.')

        return super().clean()

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

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
        if self.state == SubsidyRequestStates.APPROVED_FULFILLED:
            if not (self.subscription_plan_uuid and self.license_uuid):
                raise ValidationError(
                    'Both subscription_plan_uuid and license_uuid are required for a fulfilled license request.'
                )

        return super().clean()

    def __str__(self):
        """
        Return human-readable string representation.
        """
        return f'<LicenseRequest for {self.course_id}>'

    def approve(self, reviewer_lms_user_id):
        raise NotImplementedError

    def deny(self, reviewer_lms_user_id, reason):
        raise NotImplementedError


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
        if self.state == SubsidyRequestStates.APPROVED_FULFILLED:
            if not (self.coupon_id and self.coupon_code):
                raise ValidationError(
                    'Both coupon_id and coupon_code are required for a fulfilled coupon request.'
                )

        return super().clean()

    def __str__(self):
        """
        Return human-readable string representation.
        """
        return f'<CouponCodeRequest for {self.course_id}>'

    def approve(self, reviewer_lms_user_id):
        raise NotImplementedError

    def deny(self, reviewer_lms_user_id, reason):
        raise NotImplementedError


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
