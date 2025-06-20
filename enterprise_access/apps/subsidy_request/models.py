""" Models for subsidy_request. """
# AED 2025-05-01: pylint runner is crashing in github actions
# when this file is not disabled.
# pylint: skip-file

import collections
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _
from jsonfield.encoder import JSONEncoder
from jsonfield.fields import JSONField
from model_utils.models import SoftDeletableModel, TimeStampedModel
from simple_history.models import HistoricalRecords
from simple_history.utils import bulk_update_with_history

from enterprise_access.apps.subsidy_request.constants import (
    SUBSIDY_REQUEST_BULK_OPERATION_BATCH_SIZE,
    LearnerCreditRequestActionChoices,
    LearnerCreditRequestActionErrorReasons,
    LearnerCreditRequestUserMessages,
    SubsidyRequestStates,
    SubsidyTypeChoices
)
from enterprise_access.apps.subsidy_request.tasks import update_course_info_for_subsidy_request_task
from enterprise_access.utils import localized_utcnow


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
        db_index=True
    )

    course_id = models.CharField(
        null=True,
        blank=True,
        max_length=128
    )

    course_title = models.CharField(
        null=True,
        blank=True,
        max_length=255
    )

    course_partners = JSONField(
        blank=True,
        null=True,
        load_kwargs={'object_pairs_hook': collections.OrderedDict},
        dump_kwargs={'indent': 4, 'cls': JSONEncoder, 'separators': (',', ':')},
        help_text=_(
            "List of course partner dictionaries."
        )
    )

    enterprise_customer_uuid = models.UUIDField(
        db_index=True
    )

    state = models.CharField(
        max_length=25,
        blank=False,
        null=False,
        choices=SubsidyRequestStates.CHOICES,
        default=SubsidyRequestStates.REQUESTED,
        db_index=True
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
        indexes = [
            models.Index(fields=['uuid', 'state']),
            models.Index(fields=['user', 'enterprise_customer_uuid', 'state', 'course_id']),
        ]


class LicenseRequest(SubsidyRequest):
    """
    Stores information related to a license request.

    .. no_pii: This model has no PII
    """

    subscription_plan_uuid = models.UUIDField(
        null=True,
        blank=True,
        db_index=True
    )

    license_uuid = models.UUIDField(
        null=True,
        blank=True,
        db_index=True
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
        blank=True,
        db_index=True
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

    last_remind_date = models.DateTimeField(
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


class LearnerCreditRequestConfiguration(TimeStampedModel):
    """
    Stores configuration for learner credit requests.

    .. no_pii: This model has no PII.
    """

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
    )

    active = models.BooleanField(
        db_index=True,
        default=False,
        help_text='Whether this configuration is active. Defaults to True.',
    )

    history = HistoricalRecords()


class LearnerCreditRequest(SubsidyRequest):
    """
    Stores information related to a learner credit request.

    .. no_pii: This model has no PII
    """

    assignment = models.OneToOneField(
        'content_assignments.LearnerContentAssignment',
        related_name="credit_request",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="The content assignment associated with this request."
    )

    learner_credit_request_config = models.ForeignKey(
        LearnerCreditRequestConfiguration,
        related_name="learner_credit_requests",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="The learner credit request configuration associated with this request.",
    )

    course_price = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Cost of the content in USD Cents.",
    )

    history = HistoricalRecords()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'enterprise_customer_uuid', 'course_id'],
                name='unique_learner_course_request',
                condition=models.Q(
                    state__in=[
                        SubsidyRequestStates.REQUESTED,
                        SubsidyRequestStates.APPROVED,
                        SubsidyRequestStates.ERROR,
                        SubsidyRequestStates.ACCEPTED
                    ]),
            )
        ]

    def __str__(self):
        """
        Return human-readable string representation.
        """
        if self.course_id:
            return f'<LearnerCreditRequest for user {self.user} and course {self.course_id}>'
        return f'<LearnerCreditRequest for user {self.user}>'

    def approve(self, reviewer):
        self.reviewer = reviewer
        self.state = SubsidyRequestStates.APPROVED
        self.reviewed_at = localized_utcnow()
        self.save()

    def decline(self, reviewer, reason=None):
        self.reviewer = reviewer
        self.state = SubsidyRequestStates.DECLINED
        self.decline_reason = reason
        self.reviewed_at = localized_utcnow()
        self.save()

    def cancel(self, reviewer):
        self.state = SubsidyRequestStates.CANCELLED
        self.reviewer = reviewer
        self.reviewed_at = localized_utcnow()
        self.save()


class LearnerCreditRequestActions(TimeStampedModel):
    """
    Stores information related to actions performed on a learner credit request.

    .. no_pii: This model has no PII
    """
    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
    )

    learner_credit_request = models.ForeignKey(
        LearnerCreditRequest,
        related_name="actions",
        on_delete=models.CASCADE,
        help_text="The learner credit request associated with this action."
    )

    recent_action = models.CharField(
        max_length=25,
        blank=False,
        null=False,
        db_index=True,
        choices=LearnerCreditRequestActionChoices,
        help_text="The type of action taken on the learner credit request.",
    )

    status = models.CharField(
        max_length=25,
        blank=False,
        null=False,
        db_index=True,
        choices=LearnerCreditRequestUserMessages.CHOICES,
        help_text="The message shown to the user about the request status.",
    )

    error_reason = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
        choices=LearnerCreditRequestActionErrorReasons.CHOICES,
        help_text="The type of error that occurred during the action, if any.",
    )

    traceback = models.TextField(
        blank=True,
        null=True,
        editable=False,
        help_text="Any traceback we recorded when an error was encountered.",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ['created']
        verbose_name = "Learner Credit Request Action"
        verbose_name_plural = "Learner Credit Request Actions"

    def __str__(self):
        return (f"<LearnerCreditRequestActions for request {self.learner_credit_request}"
                f" with action {self.recent_action}>")

    @classmethod
    def create_action(
        cls,
        learner_credit_request,
        recent_action,
        status,
        error_reason=None,
        traceback=None,
    ):
        """
        Utility method to create a new LearnerCreditRequestActions instance.

        Args:
            learner_credit_request (LearnerCreditRequest): The associated learner credit request.
            recent_action (str): The type of action taken (must be a valid choice from
                LearnerCreditRequestActionChoices).
            status (str): The status message (must be a valid choice from LearnerCreditRequestUserMessages.CHOICES).
            error_reason (str, optional): The error reason if applicable (must be a valid choice
                from LearnerCreditRequestActionErrorReasons.CHOICES).
            traceback (str, optional): Any traceback information for debugging.

        Returns:
            LearnerCreditRequestActions: The created instance.

        Raises:
            ValidationError: If any of the provided values are invalid.
            ValueError: If required parameters are missing or invalid.
        """
        # Create the instance
        try:
            action = cls(
                learner_credit_request=learner_credit_request,
                recent_action=recent_action,
                status=status,
                error_reason=error_reason,
                traceback=traceback,
            )
            action.full_clean()
            action.save()
            return action
        except ValidationError as e:
            raise ValidationError(f"Failed to create LearnerCreditRequestActions: {e}")
        except Exception as e:
            raise ValueError(f"Unexpected error creating LearnerCreditRequestActions: {e}")


@receiver(models.signals.post_save, sender=CouponCodeRequest)
@receiver(models.signals.post_save, sender=LicenseRequest)
@receiver(models.signals.post_save, sender=LearnerCreditRequest)
def update_course_info_for_subsidy_request(sender, **kwargs):
    subsidy_request = kwargs['instance']
    if subsidy_request.course_title and subsidy_request.course_partners:
        return

    model_name = subsidy_request.__class__.__name__
    update_course_info_for_subsidy_request_task.delay(
        model_name,
        str(subsidy_request.uuid),
    )
