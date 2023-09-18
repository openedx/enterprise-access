"""
Models for content_assignments
"""
from uuid import UUID, uuid4

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
    enterprise_customer_uuid = models.UUIDField(
        db_index=True,
        null=False,
        blank=False,
        # This field should, in practice, never be null.
        # However, specifying a default quells makemigrations and helps prevent migrate from failing on existing
        # populated databases.
        default=UUID('0' * 32),
        help_text="The owning Enterprise Customer's UUID. Cannot be blank or null.",
    )
    active = models.BooleanField(
        db_index=True,
        default=True,
        help_text='Whether this assignment configuration is active. Defaults to True.',
    )
    # TODO: Below this line add fields to support rules that control the creation and lifecycle of assignments.
    #
    # Possibilities include:
    #   - `max_assignments` to limit the total allowed assignments.
    #   - `max_age` to control the amount of time before an allocated assignment is auto-expired.

    history = HistoricalRecords()

    def delete(self, *args, **kwargs):
        """
        Perform a soft-delete, overriding the standard delete() method to prevent hard-deletes.

        If this instance was already soft-deleted, invoking delete() is a no-op.
        """
        if self.active:
            if 'reason' in kwargs and kwargs['reason']:
                self._change_reason = kwargs['reason']  # pylint: disable=attribute-defined-outside-init
            self.active = False
            self.save()


class LearnerContentAssignment(TimeStampedModel):
    """
    Represent an assignment of a piece of content to a learner.
    This model is unique on (assignment_configuration, learner identifier, content identifier).
    This means that only one combination of (learner, content) can exist in a given
    assignment configuration.  So in the lifecycle of a given assignment,
    state transitions such as:
      `allocated` -> `cancelled` -> `allocated` -> `accepted`
    are allowed, and we can use the history table of this model to ascertain
    when/why such state transitions occurred.

    .. pii: The learner_email field stores PII, which is to be scrubbed after 90 days via management command.
    .. pii_types: email_address
    .. pii_retirement: local_api
    """
    class Meta:
        unique_together = [
            ('assignment_configuration', 'learner_email', 'content_key'),
            ('assignment_configuration', 'lms_user_id', 'content_key'),
        ]

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
