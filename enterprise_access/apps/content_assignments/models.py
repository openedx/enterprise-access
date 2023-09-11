"""
Models for content_assignments
"""
from uuid import uuid4

from django.db import models
from django_extensions.db.models import TimeStampedModel
from simple_history.models import HistoricalRecords

from .constants import LearnerContentAssignmentStateChoices


class AssignmentConfiguration(TimeStampedModel):
    """
    Manage the creation and lifecycle of LearnerContentAssignments according to configurable rules.

    .. no_pii: This model has no PII
    """
    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
    )


class LearnerContentAssignment(TimeStampedModel):
    """
    Represent an assignment of a piece of content to a learner.

    .. pii: The learner_email field stores PII, which is to be scrubbed after 90 days via management command.
    .. pii_types: email_address
    .. pii_retirement: local_api
    """
    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
    )
    assignment_configuration = models.ForeignKey(
        AssignmentConfiguration,
        related_name="assignments",
        on_delete=models.SET_NULL,
        db_index=True,
        null=True,
        help_text="AssignmentConfiguration defining the lifecycle rules of this assignment.",
    )
    learner_email = models.EmailField(
        null=False,
        blank=False,
        db_index=True,
        help_text="Email of learner to assign content. Automatically scrubbed after 90 days.",
    )
    lms_user_id = models.IntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "The id of the Open edX LMS user record with which this LearnerContentAssignment is associated. "
            "This may be null at time of creation."
        ),
    )
    content_key = models.CharField(
        max_length=255,
        blank=False,
        null=False,
        db_index=True,
        help_text=(
            "The globally unique content identifier to assign to the learner.  Joinable with "
            "ContentMetadata.content_key in enterprise-catalog."
        ),
    )
    content_quantity = models.BigIntegerField(
        null=False,
        blank=False,
        help_text="Cost of the content in USD Cents.",
    )
    state = models.CharField(
        max_length=255,
        blank=False,
        null=False,
        choices=LearnerContentAssignmentStateChoices.CHOICES,
        default=LearnerContentAssignmentStateChoices.ALLOCATED,
        help_text=(
            "The current state of the LearnerContentAssignment. One of: "
            f"{[choice[0] for choice in LearnerContentAssignmentStateChoices.CHOICES]}"
        ),
    )
    transaction_uuid = models.UUIDField(
        blank=True,
        null=True,
        help_text=(
            "A reference to the ledger transaction associated with the subsidy supporting this assignment.  Likely "
            f"null if state != {LearnerContentAssignmentStateChoices.ACCEPTED}."
        ),
    )
    last_notification_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "The last time the learner was notified or reminded about this assignment.  Null means the learner has not "
            "been notified."
        ),
    )
    history = HistoricalRecords()
