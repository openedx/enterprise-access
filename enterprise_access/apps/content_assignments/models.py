"""
Models for content_assignments
"""
from uuid import UUID, uuid4

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models
from django.db.models import Case, Exists, F, Max, OuterRef, Q, Value, When
from django.db.models.fields import BooleanField, CharField, DateTimeField, IntegerField
from django.db.models.functions import Coalesce
from django.utils import timezone
from django_extensions.db.models import TimeStampedModel
from simple_history.models import HistoricalRecords
from simple_history.utils import bulk_create_with_history, bulk_update_with_history

from .constants import (
    AssignmentActionErrors,
    AssignmentActions,
    AssignmentLearnerStates,
    AssignmentRecentActionTypes,
    LearnerContentAssignmentStateChoices
)

BULK_OPERATION_BATCH_SIZE = 50


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

    def __str__(self):
        return f'uuid={self.uuid}, customer={self.enterprise_customer_uuid}'

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

    @property
    def policy(self):
        """ Helper to safely fetch the related policy object or None. """
        try:
            return self.subsidy_access_policy  # pylint: disable=no-member
        except ObjectDoesNotExist:
            return None


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
        db_index=True,
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

    def __str__(self):
        return (
            f'uuid={self.uuid}, state={self.state}, learner_email={self.learner_email}, content_key={self.content_key}'
        )

    def clean(self):
        """
        Validates that content_quantity <= 0.
        """
        if self.content_quantity and self.content_quantity > 0:
            raise ValidationError(f'{self} cannot have a positive content quantity.')

    @classmethod
    def bulk_create(cls, assignment_records):
        """
        Creates new ``LearnerContentAssignment`` records in bulk,
        while saving their history:
        https://django-simple-history.readthedocs.io/en/latest/common_issues.html#bulk-creating-a-model-with-history
        """
        return bulk_create_with_history(
            assignment_records,
            cls,
            batch_size=BULK_OPERATION_BATCH_SIZE,
        )

    @classmethod
    def bulk_update(cls, assignment_records, updated_field_names):
        """
        Updates and saves the given ``assignment_records`` in bulk,
        while saving their history:
        https://django-simple-history.readthedocs.io/en/latest/common_issues.html#bulk-updating-a-model-with-history-new

        Note that the simple-history utility function uses Django's bulk_update() under the hood:
        https://docs.djangoproject.com/en/3.2/ref/models/querysets/#bulk-update

        which does *not* call save(), so we have to manually update the `modified` field
        during this bulk operation in order for that field's value to be updated.
        """
        for record in assignment_records:
            record.modified = timezone.now()

        return bulk_update_with_history(
            assignment_records,
            cls,
            updated_field_names + ['modified'],
            batch_size=BULK_OPERATION_BATCH_SIZE,
        )

    def get_successful_linked_action(self):
        """
        Returns the first successful "linked" LearnerContentAssignmentActions for this assignment,
        or None if no such record exists.
        """
        return self.actions.filter(
            action_type=AssignmentActions.LEARNER_LINKED,
            error_reason=None,
        ).first()

    def add_successful_linked_action(self):
        """
        Adds a successful "linked" LearnerContentAssignmentAction for this assignment record.
        If a successful linked action already exists for this assignment, returns
        that linked action record instead.
        """
        record, was_created = self.actions.get_or_create(
            action_type=AssignmentActions.LEARNER_LINKED,
            error_reason=None,
            defaults={
                'completed_at': timezone.now(),
            },
        )
        return record, was_created

    def get_successful_notified_action(self):
        """
        Returns the first successful "notified" LearnerContentAssignmentActions for this assignment,
        or None if no such record exists.
        """
        return self.actions.filter(
            action_type=AssignmentActions.NOTIFIED,
            error_reason=None,
        ).first()

    def add_successful_notified_action(self):
        """
        Adds a successful "notified" LearnerContentAssignmentAction for this assignment record.
        If a successful notified action already exists for this assignment, returns
        that linked action record instead.
        """
        record, was_created = self.actions.get_or_create(
            action_type=AssignmentActions.NOTIFIED,
            error_reason=None,
            defaults={
                'completed_at': timezone.now(),
            },
        )
        return record, was_created

    def get_last_successful_reminded_action(self):
        """
        Returns all successful "reminded" LearnerContentAssignmentActions for this assignment,
        or None if no such record exists.
        """
        return self.actions.filter(
            action_type=AssignmentActions.REMINDED,
            error_reason=None,
        ).order_by('-completed_at').first()

    def add_successful_reminded_action(self):
        """
        Adds a successful "reminded" LearnerContentAssignmentAction for this assignment record.
        """
        return self.actions.create(
            action_type=AssignmentActions.REMINDED,
            completed_at=timezone.now(),
        )

    def get_recent_action_data(self):
        """
        Return structured data about the most recent action, meant to feed the serializer.

        Recent action can ONLY be one of:
        * AssignmentRecentActionTypes.REMINDED: Most recent ``reminded`` action.
        * AssignmentRecentActionTypes.ASSIGNED: Assignment record creation event, if no reminded actions exist.

        Notes:
        * These are not 1:1 with EITHER the AssignmentAction types, OR internal lifecycle states of assignments.
          Instead it is a hybrid of both.
        * This logic duplicates what is performed in annotate_dynamic_fields_onto_queryset() to annotate the
          `recent_action` and `recent_action_time` fields on an assignment queryset.  We rely on the serializer to
          function even when the assignment object is not derived from a viewset's get_queryset().

        Returns:
            dict: {
                'action_type': <one of AssignmentRecentActionTypes>,
                'timestamp': <time of recent action>,
            }
        """
        reminded_actions = self.actions.filter(action_type=AssignmentActions.REMINDED)
        if len(reminded_actions) > 0:
            recent_action_type = AssignmentRecentActionTypes.REMINDED
            recent_action_time = reminded_actions.order_by('completed_at').last().completed_at
        else:
            recent_action_type = AssignmentRecentActionTypes.ASSIGNED
            recent_action_time = self.created
        return {
            'action_type': recent_action_type,
            'timestamp': recent_action_time,
        }

    def get_learner_state(self):
        """
        Returns the learner state of this assignment (not to be confused with state).

        Notes:
        * learner_state is not 1:1 with EITHER the AssignmentAction types, OR internal lifecycle states of assignments.
          Instead it is a hybrid of both.
        * This logic duplicates what is performed in annotate_dynamic_fields_onto_queryset() to annotate the
          `learner_state` field on an assignment queryset.  We rely on the serializer to function even when the
          assignment object is not derived from a viewset's get_queryset().

        Returns:
            str: One of AssignmentLearnerStates, or None if the assignment doesn't map to one.
        """
        has_notification = bool(LearnerContentAssignmentAction.objects.filter(
            assignment=self,
            action_type=AssignmentActions.NOTIFIED,
        ))
        if self.state == LearnerContentAssignmentStateChoices.ALLOCATED and not has_notification:
            return AssignmentLearnerStates.NOTIFYING
        elif self.state == LearnerContentAssignmentStateChoices.ALLOCATED and has_notification:
            return AssignmentLearnerStates.WAITING
        elif self.state == LearnerContentAssignmentStateChoices.ERRORED and has_notification:
            return AssignmentLearnerStates.FAILED
        else:
            return None

    @classmethod
    def annotate_dynamic_fields_onto_queryset(cls, queryset):
        """
        Annotate extra dynamic fields used by this viewset for DRF-supported ordering and filtering.

        Fields added:
        * learner_state (CharField)
        * learner_state_sort_order (IntegerField)
        * recent_action (CharField)
        * recent_action_time (DateTimeField)

        Notes:
        * This class method duplicates the logic found in instance methods get_learner_state() and
          get_recent_action_data(), but using pure ORM queryset logic instead of in-memory calculations.

        Args:
            queryset (QuerySet): LearnerContentAssignment queryset, vanilla.

        Returns:
            QuerySet: LearnerContentAssignment queryset, same objects but with extra fields annotated.
        """
        # Annotate a derived field ``recent_action_time`` using pure ORM so that we can order_by() it later.
        # ``recent_action_time`` is defined as the time of the most recent reminder, and falls back to assignment
        # creation time if there are no reminders.
        new_queryset = queryset.annotate(
            recent_action_time=Coalesce(
                # Time of most recent reminder.
                Max('actions__completed_at', filter=Q(actions__action_type=AssignmentActions.REMINDED)),
                # Fallback to created time.
                F('created'),
                # Coerce CreationDateTimeField into a compatible field.
                output_field=DateTimeField(),
            )
        )

        # Annotate a derived field ``recent_action``
        new_queryset = new_queryset.annotate(
            has_reminded=Exists(
                LearnerContentAssignmentAction.objects.filter(
                    assignment=OuterRef('uuid'),
                    action_type=AssignmentActions.REMINDED,
                )
            )
        ).annotate(
            recent_action=Case(
                When(has_reminded=False, then=Value(AssignmentRecentActionTypes.ASSIGNED)),
                When(has_reminded=True, then=Value(AssignmentRecentActionTypes.REMINDED)),
                output_field=BooleanField(),
            )
        )

        # Annotate a derived field ``learner_state`` using pure ORM so that we do not need to store it as duplicate
        # source-of-truth data in the database.  This improves system integrity within production while simultaneously
        # increasing analytics complexity downstream of production.
        new_queryset = new_queryset.annotate(
            # Step 1 is to add a dynamic field representing whether the learner has been successfully notified.
            has_notification=Exists(
                LearnerContentAssignmentAction.objects.filter(
                    assignment=OuterRef('uuid'),
                    action_type=AssignmentActions.NOTIFIED,
                )
            )
        ).annotate(
            learner_state=Case(
                When(
                    Q(state=LearnerContentAssignmentStateChoices.ALLOCATED) & Q(has_notification=False),
                    then=Value(AssignmentLearnerStates.NOTIFYING),
                ),
                When(
                    Q(state=LearnerContentAssignmentStateChoices.ALLOCATED) & Q(has_notification=True),
                    then=Value(AssignmentLearnerStates.WAITING),
                ),
                When(
                    Q(state=LearnerContentAssignmentStateChoices.ERRORED),
                    then=Value(AssignmentLearnerStates.FAILED),
                ),
                # `accepted` and `cancelled` assignments will serialize with a NULL learner_state. This has no UX impact
                # because those two states aren't displayed anyway.
                default=None,
                output_field=CharField()
            )
        )

        # Annotate a derived field ``learner_state_sort_order`` using pure ORM so that we can order_by() it later.  It
        # ostensibly sorts assignment lifecycle states, but has one additional trick up its sleeve: allocated
        # assignments are further sorted by not-notified first, then notified last.
        learner_state_sort_order_cases = [
            When(learner_state=learner_state, then=Value(sort_order))
            for sort_order, learner_state in enumerate(AssignmentLearnerStates.SORT_ORDER)
        ]
        new_queryset = new_queryset.annotate(
            learner_state_sort_order=Case(
                *learner_state_sort_order_cases,
                default=Value(999),  # Anything that isn't a learner state gets sorted last.
                output_field=IntegerField(),
            )
        )

        return new_queryset


class LearnerContentAssignmentAction(TimeStampedModel):
    """
    A model that persists information regarding certain non-lifecycle actions
    on ``LearnerContentAssignment`` records.

    .. no_pii: This model has no PII
    """
    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
    )
    assignment = models.ForeignKey(
        LearnerContentAssignment,
        related_name="actions",
        on_delete=models.SET_NULL,
        null=True,
        help_text="The LearnerContentAssignment on which this action was performed.",
    )
    action_type = models.CharField(
        max_length=255,
        blank=False,
        null=False,
        db_index=True,
        choices=AssignmentActions.CHOICES,
        help_text="The type of action take on the related assignment record.",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="The time at which the action was successfully completed.",
    )
    error_reason = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
        choices=AssignmentActionErrors.CHOICES,
        help_text="The type of error that occurred during the action, if any.",
    )
    traceback = models.TextField(
        blank=True,
        null=True,
        editable=False,
        help_text="Any traceback we recorded when an error was encountered.",
    )

    history = HistoricalRecords()

    def __str__(self):
        return (
            f'uuid={self.uuid}, action_type={self.action_type}, error_reason={self.error_reason}'
        )
