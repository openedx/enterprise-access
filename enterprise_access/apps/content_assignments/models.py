"""
Models for content_assignments
"""
import logging
from datetime import datetime
from os import urandom
from uuid import UUID, uuid4

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models
from django.db.models import Case, Exists, F, Max, OuterRef, Q, Value, When
from django.db.models.fields import CharField, DateTimeField, IntegerField
from django.db.models.functions import Cast, Coalesce
from django.db.models.lookups import GreaterThan
from django.utils import timezone
from django_extensions.db.models import TimeStampedModel
from pytz import UTC
from simple_history.models import HistoricalRecords
from simple_history.utils import bulk_create_with_history, bulk_update_with_history

from enterprise_access.utils import format_traceback

from .constants import (
    NUM_DAYS_BEFORE_AUTO_EXPIRATION,
    RETIRED_EMAIL_ADDRESS_FORMAT,
    AssignmentActionErrors,
    AssignmentActions,
    AssignmentLearnerStates,
    AssignmentRecentActionTypes,
    LearnerContentAssignmentStateChoices
)

logger = logging.getLogger(__name__)

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

    def _should_acknowledge_expired_assignment(self, assignment):
        """
        Returns a tuple of booleans indicating whether the given assignment should be acknowledged and
        whether the assignment has already been acknowledged.

        Example: (False, False) means that the assignment should not be acknowledged, and has not yet been acknowledged.
        Example: (True, False) means that the assignment should be acknowledged, and has not yet been acknowledged.
        Example: (False, True) means that the assignment should not be acknowledged, and has already been acknowledged.
        """
        if assignment.state != LearnerContentAssignmentStateChoices.EXPIRED:
            return False, False

        last_expiration = assignment.get_last_successful_expiration_action()
        expiration_last_acknowledged = assignment.get_last_successful_acknowledged_expired_action()
        if not last_expiration:
            logger.error(
                'Assignment %s is in state %s but has no successful expiration action.',
                assignment.uuid,
                LearnerContentAssignmentStateChoices.EXPIRED,
            )

        # Check whether expiration has ever been acknowledged; if not, acknowledge it. If
        # it has been acknowledged before, check whether last expiration action is newer
        # than the last acknowledged expiration action.
        has_acknowledged_recent_expiration = (
            expiration_last_acknowledged and last_expiration and
            last_expiration.completed_at <= expiration_last_acknowledged.completed_at
        )
        if not has_acknowledged_recent_expiration:
            return True, False

        # Otherwise, the expiration has already been acknowledged and should not be acknowledged again.
        return False, True

    def _should_acknowledge_cancelled_assignment(self, assignment):
        """
        Returns a tuple of booleans indicating whether the given assignment should be acknowledged and
        whether the assignment has already been acknowledged.

        Example: (False, False) means that the assignment should not be acknowledged, and has not yet been acknowledged.
        Example: (True, False) means that the assignment should be acknowledged, and has not yet been acknowledged.
        Example: (False, True) means that the assignment should not be acknowledged, and has already been acknowledged.
        """
        if assignment.state != LearnerContentAssignmentStateChoices.CANCELLED:
            return False, False

        last_cancellation = assignment.get_last_successful_cancel_action()
        cancellation_last_acknowledged = assignment.get_last_successful_acknowledged_cancelled_action()
        if not last_cancellation:
            logger.error(
                'Assignment %s is in state %s but has no successful expiration action.',
                assignment.uuid,
                LearnerContentAssignmentStateChoices.EXPIRED,
            )

        # Check whether cancelation has ever been acknowledged; if not, acknowledge it. If
        # it has been acknowledged before, check whether last cancelation action is newer
        # than the last acknowledged cancelation action.
        has_acknowledged_recent_cancelation = (
            cancellation_last_acknowledged and last_cancellation and
            last_cancellation.completed_at <= cancellation_last_acknowledged.completed_at
        )
        if not has_acknowledged_recent_cancelation:
            return True, False

        # Otherwise, the cancelation has already been acknowledged and should not be acknowledged again.
        return False, True

    def acknowledge_assignments(self, assignment_uuids, lms_user_id):
        """
        Acknowledges the given assignment UUIDs, related to this AssignmentConfiguration.

        Returns a tuple of lists of assignments:

        * acknowledged_assignments: assignments that were successfully acknowledged
        * already_acknowledged_assignments: assignments that were already acknowledged
        * unacknowledged_assignments: assignments that could not be acknowledged, and were
          not already acknowledged

        Raises a ValidationError if no assignments were found for the given assignment_uuids and
        the requesting user's lms_user_id.
        """
        assignments_to_acknowledge = self.assignments.filter(
            uuid__in=assignment_uuids,
            lms_user_id=lms_user_id,
        )
        if not assignments_to_acknowledge:
            raise ValidationError(
                f'No assignments found for assignment_uuids={assignment_uuids} and lms_user_id={lms_user_id}.'
            )

        acknowledged_assignments = []
        already_acknowledged_assignments = []
        unacknowledged_assignments = []

        for assignment in assignments_to_acknowledge:
            should_ack_expiration, already_acknowledged_expiration = self._should_acknowledge_expired_assignment(
                assignment
            )
            should_ack_cancellation, already_acknowledged_cancellation = self._should_acknowledge_cancelled_assignment(
                assignment
            )

            # Acknowledge the expiration, if necessary.
            if should_ack_expiration:
                assignment.add_successful_acknowledged_expired_action()
                acknowledged_assignments.append(assignment)

            # Acknowledge the cancellation, if necessary.
            if should_ack_cancellation:
                assignment.add_successful_acknowledged_cancelled_action()
                acknowledged_assignments.append(assignment)

            # Learner has already acknowledged this expiration or cancellation, so add it to
            # the returned already acknowledged list.
            if already_acknowledged_expiration or already_acknowledged_cancellation:
                already_acknowledged_assignments.append(assignment)

            # If we didn't acknowledge the assignment (e.g., assignment isn't expired or cancelled),
            # add it to returned unacknowledged list. This is a defensive check / safegaurd, and provides
            # feedback to the caller that some assignments were not acknowledged.
            if (
                not should_ack_expiration and
                not should_ack_cancellation and
                assignment not in already_acknowledged_assignments
            ):
                unacknowledged_assignments.append(assignment)

        # Given any unacknowledged assignments (e.g., assignments that aren't
        # expired or cancelled), log an error as this is unexpected.
        if unacknowledged_assignments:
            logger.error(
                'Attempted to acknowledge assignments %s but assignments could not be acknowledged: %s',
                assignment_uuids,
                unacknowledged_assignments,
            )

        return acknowledged_assignments, already_acknowledged_assignments, unacknowledged_assignments


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

    .. pii: The learner_email field stores PII,
       which is to be scrubbed after 90 days via management command.
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
    parent_content_key = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
        help_text=(
            "The globally unique content identifier of the parent content to assign to the learner.  Joinable with "
            "ContentMetadata.content_key in enterprise-catalog."
        ),
    )
    is_assigned_course_run = models.BooleanField(
        null=False,
        blank=False,
        default=False,
        help_text=(
            "Whether the content_key corresponds to a course run. If True, the content_key should be a course run key."
        ),
    )
    content_title = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text=(
            "The ContentMetadata.title from content corresponding to the content_key"
        ),
    )
    content_quantity = models.BigIntegerField(
        null=False,
        blank=False,
        help_text="Cost of the content in USD Cents.",
    )
    preferred_course_run_key = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
        help_text=(
            "The preferred course run that the admin primarily intends for the learner, and the one used to control "
            "nudge emails. This is automatically set at assignment creation or re-allocation time."
        ),
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
    allocated_at = models.DateTimeField(
        null=False,
        blank=True,
        default=timezone.now,
        help_text="The last time the assignment was allocated. Cannot be null.",
    )
    accepted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="The last time this assignment was accepted. Null means the assignment is not accepted.",
    )
    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="The last time this assignment was cancelled. Null means the assignment is not cancelled.",
    )
    expired_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="The last time this assignment was expired. Null means the assignment is not expired.",
    )
    errored_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="The last time this assignment was in an error state. Null means the assignment is not errored.",
    )
    reversed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="The last time this assignment was reversed. Null means the assignment is not currently reversed.",
    )
    transaction_uuid = models.UUIDField(
        blank=True,
        null=True,
        help_text=(
            "A reference to the ledger transaction associated with the subsidy supporting this assignment.  Likely "
            f"null if state != {LearnerContentAssignmentStateChoices.ACCEPTED}."
        ),
    )
    allocation_batch_id = models.UUIDField(
        null=True,
        blank=True,
        default=None,
        help_text=(
            "A reference to the batch that this assignment was created in. Helpful for grouping assignments together."
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

    @property
    def learner_acknowledged(self):
        """
        Returns whether or not the learner has acknowledged the assignment.
        """
        if self.state == LearnerContentAssignmentStateChoices.EXPIRED:
            last_expired_action = self.get_last_successful_expiration_action()
            if not last_expired_action:
                logger.warning(
                    'LearnerContentAssignment with UUID %s is in an expired state, but has no related '
                    'actions in an expired state.',
                    self.uuid,
                )
                return False
            return last_expired_action.learner_acknowledged

        if self.state == LearnerContentAssignmentStateChoices.CANCELLED:
            last_cancelled_action = self.get_last_successful_cancel_action()
            if not last_cancelled_action:
                logger.warning(
                    'LearnerContentAssignment with UUID %s is in a cancelled state, but has no related '
                    'actions in a cancelled state.',
                    self.uuid,
                )
                return False
            return last_cancelled_action.learner_acknowledged

        # Fallback to None, in case the assignment is in a state that may not be acknowledged.
        return None

    def get_allocation_timeout_expiration(self):
        """
        Returns the date at which this assignment expires due to
        waiting too long to move into the "accepted" state.  Note that
        sending a reminder notification for an assignment does not
        reset the auto-expiration date.
        """
        allocation_timeout_expiration = self.allocated_at + timezone.timedelta(days=NUM_DAYS_BEFORE_AUTO_EXPIRATION)
        allocation_timeout_expiration = allocation_timeout_expiration.replace(tzinfo=UTC)
        return allocation_timeout_expiration

    def get_last_successful_linked_action(self):
        """
        Returns the last successful "linked" LearnerContentAssignmentActions for this assignment,
        or None if no such record exists.
        """
        return self.actions.filter(
            action_type=AssignmentActions.LEARNER_LINKED,
            error_reason=None,
        ).order_by('-completed_at').first()

    def add_successful_linked_action(self):
        """
        Adds a successful "linked" LearnerContentAssignmentAction for this assignment record,
        regardless of if one such action record already exists.
        """
        return self.actions.create(
            action_type=AssignmentActions.LEARNER_LINKED,
            error_reason=None,
            completed_at=timezone.now(),
        )

    def add_errored_linked_action(self, exc):
        """
        Adds an errored "linked" action for this assignment record, given an exception instance.
        """
        return self.actions.create(
            action_type=AssignmentActions.LEARNER_LINKED,
            error_reason=AssignmentActionErrors.INTERNAL_API_ERROR,
            traceback=format_traceback(exc),
        )

    def get_last_successful_notified_action(self):
        """
        Returns the last successful "notified" LearnerContentAssignmentActions for this assignment,
        or None if no such record exists. Can be used as a proxy for understanding
        when the learner was most recently allocated this assignment.
        """
        return self.actions.filter(
            action_type=AssignmentActions.NOTIFIED,
            error_reason=None,
        ).order_by('-completed_at').first()

    def add_successful_notified_action(self):
        """
        Adds a successful "notified" LearnerContentAssignmentAction for this assignment record.
        If a successful notified action already exists for this assignment, returns
        that linked action record instead.
        """
        return self.actions.create(
            action_type=AssignmentActions.NOTIFIED,
            error_reason=None,
            completed_at=timezone.now(),
        )

    def add_errored_notified_action(self, exc):
        """
        Adds an errored action about the notification of the allocation of this assignment record,
        given an exception instance.
        """
        return self.actions.create(
            action_type=AssignmentActions.NOTIFIED,
            error_reason=AssignmentActionErrors.EMAIL_ERROR,
            traceback=format_traceback(exc),
        )

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

    def add_errored_reminded_action(self, exc):
        """
        Adds an errored "reminded" LearnerContentAssignmentAction for this assignment record.
        """
        return self.actions.create(
            action_type=AssignmentActions.REMINDED,
            error_reason=AssignmentActionErrors.EMAIL_ERROR,
            traceback=format_traceback(exc),
        )

    def get_last_successful_cancel_action(self):
        """
        Returns all successful "cancelled" LearnerContentAssignmentActions for this assignment,
        or None if no such record exists.
        """
        return self.actions.filter(
            action_type=AssignmentActions.CANCELLED,
            error_reason=None,
        ).order_by('-completed_at').first()

    def add_successful_cancel_action(self):
        """
        Adds a successful "cancel" LearnerContentAssignmentAction for this assignment record.
        """
        return self.actions.create(
            action_type=AssignmentActions.CANCELLED,
            completed_at=timezone.now(),
        )

    def add_errored_cancel_action(self, exc):
        """
        Adds an errored "cancel" LearnerContentAssignmentAction for this assignment record.
        """
        return self.actions.create(
            action_type=AssignmentActions.CANCELLED,
            error_reason=AssignmentActionErrors.EMAIL_ERROR,
            traceback=format_traceback(exc),
        )

    def get_last_successful_expiration_action(self):
        """
        Returns all successful "expired" LearnerContentAssignmentActions for this assignment,
        or None if no such record exists.
        """
        return self.actions.filter(
            action_type=AssignmentActions.EXPIRED,
            error_reason=None,
        ).order_by('-completed_at').first()

    def add_successful_expiration_action(self):
        """
        Adds a successful expiration LearnerContentAssignmentAction for this assignment record.
        """
        return self.actions.create(
            action_type=AssignmentActions.EXPIRED,
            completed_at=timezone.now(),
        )

    def add_errored_expiration_action(self, exc):
        """
        Adds an errored expiration LearnerContentAssignmentAction for this assignment record.
        """
        return self.actions.create(
            action_type=AssignmentActions.EXPIRED,
            error_reason=AssignmentActionErrors.EMAIL_ERROR,
            traceback=format_traceback(exc),
        )

    def get_last_successful_redeemed_action(self):
        """
        Returns all successful "redeemed" LearnerContentAssignmentActions for this assignment,
        or None if no such record exists.
        """
        return self.actions.filter(
            action_type=AssignmentActions.REDEEMED,
            error_reason=None,
        ).order_by('-completed_at').first()

    def add_successful_redeemed_action(self):
        """
        Adds a successful redeemed LearnerContentAssignmentAction for this assignment record.
        """
        return self.actions.create(
            action_type=AssignmentActions.REDEEMED,
            completed_at=timezone.now(),
        )

    def add_errored_redeemed_action(self, exc):
        """
        Adds an errored redeemed LearnerContentAssignmentAction for this assignment record.
        """
        return self.actions.create(
            action_type=AssignmentActions.REDEEMED,
            error_reason=AssignmentActionErrors.ENROLLMENT_ERROR,
            traceback=format_traceback(exc),
        )

    def get_last_successful_acknowledged_cancelled_action(self):
        """
        Returns the last successful "acknowledged" cancellation LearnerContentAssignmentActions for this assignment,
        or None if no such record exists.
        """
        return self.actions.filter(
            action_type=AssignmentActions.CANCELLED_ACKNOWLEDGED,
            error_reason=None,
        ).order_by('-completed_at').first()

    def add_successful_acknowledged_cancelled_action(self):
        """
        Adds a successful acknowledged LearnerContentAssignmentAction for this assignment record.
        """
        return self.actions.create(
            action_type=AssignmentActions.CANCELLED_ACKNOWLEDGED,
            completed_at=timezone.now(),
        )

    def get_last_successful_acknowledged_expired_action(self):
        """
        Returns the last successful "acknowledged" expiration LearnerContentAssignmentActions for this assignment,
        or None if no such record exists.
        """
        return self.actions.filter(
            action_type=AssignmentActions.EXPIRED_ACKNOWLEDGED,
            error_reason=None,
        ).order_by('-completed_at').first()

    def add_successful_acknowledged_expired_action(self):
        """
        Adds a successful acknowledged LearnerContentAssignmentAction for this assignment record.
        """
        return self.actions.create(
            action_type=AssignmentActions.EXPIRED_ACKNOWLEDGED,
            completed_at=timezone.now(),
        )

    def add_successful_reversal_action(self):
        """
        Adds a successful 'reversed' action for this assignment record.
        """
        return self.actions.create(
            action_type=AssignmentActions.REVERSED,
            completed_at=timezone.now(),
        )

    @staticmethod
    def _unique_retired_email():
        """
        Helper to return a templated email address
        that's relatively uniqueified with the addition of a random, 8-byte,
        hex string.
        """
        nonce = urandom(8).hex()
        return RETIRED_EMAIL_ADDRESS_FORMAT.format(nonce)

    def clear_pii(self):
        """
        Removes PII field values from this assignment by setting
        the ``learner_email`` field to a templated email address
        that's relatively uniqueified with the addition of a random, 8-byte,
        hex string. Does the same for related historical records.
        """
        retired_email = self._unique_retired_email()
        self.learner_email = retired_email
        self.history.update(learner_email=retired_email)  # pylint: disable=no-member

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
        * In order to use LearnerContentAssignmentAdminResponseSerializer, you must call this method on the queryset.

        Args:
            queryset (QuerySet): LearnerContentAssignment queryset, vanilla.

        Returns:
            QuerySet: LearnerContentAssignment queryset, same objects but with extra fields annotated.
        """
        # Annotate a derived field ``recent_action_time`` using pure ORM so that we can order_by() it later.
        # ``recent_action_time`` is defined as the max of the assignment's allocation time
        # or the most recent, successful reminder action.
        new_queryset = queryset.annotate(
            most_recent_reminder=Coalesce(
                Max(
                    'actions__completed_at',
                    filter=Q(actions__action_type=AssignmentActions.REMINDED),
                    output_field=DateTimeField(),
                ),
                Cast(datetime.min, DateTimeField()),
            )
        ).annotate(
            recent_action=Case(
                When(
                    GreaterThan(F('allocated_at'), F('most_recent_reminder')),
                    then=Value(AssignmentRecentActionTypes.ASSIGNED),
                ),
                When(
                    GreaterThan(F('most_recent_reminder'), F('allocated_at')),
                    then=Value(AssignmentRecentActionTypes.REMINDED),
                ),
                output_field=CharField(),
            ),
            recent_action_time=Case(
                When(
                    GreaterThan(F('allocated_at'), F('most_recent_reminder')),
                    then=F('allocated_at'),
                ),
                When(
                    GreaterThan(F('most_recent_reminder'), F('allocated_at')),
                    then=F('most_recent_reminder'),
                ),
                output_field=DateTimeField(),
            ),
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
                    error_reason__isnull=True,
                    completed_at__isnull=False,
                )
            ),
            # ... or if they have an errored notification.
            has_errored_notification=Exists(
                LearnerContentAssignmentAction.objects.filter(
                    assignment=OuterRef('uuid'),
                    action_type=AssignmentActions.NOTIFIED,
                    error_reason__isnull=False,
                )
            )
        ).annotate(
            learner_state=Case(
                When(
                    Q(state=LearnerContentAssignmentStateChoices.ALLOCATED) &
                    Q(has_errored_notification=True) &
                    Q(has_notification=False),
                    then=Value(AssignmentLearnerStates.FAILED),
                ),
                When(
                    Q(state=LearnerContentAssignmentStateChoices.ALLOCATED) & Q(has_notification=False),
                    then=Value(AssignmentLearnerStates.NOTIFYING),
                ),
                When(
                    Q(state=LearnerContentAssignmentStateChoices.ALLOCATED) & Q(has_notification=True),
                    then=Value(AssignmentLearnerStates.WAITING),
                ),
                When(
                    Q(state=LearnerContentAssignmentStateChoices.EXPIRED),
                    then=Value(AssignmentLearnerStates.EXPIRED),
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
        on_delete=models.CASCADE,
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

    class Meta:
        ordering = ['created']

    def __str__(self):
        return (
            f'uuid={self.uuid}, action_type={self.action_type}, error_reason={self.error_reason}'
        )

    @property
    def learner_acknowledged(self):
        """
        Returns True if this action has been acknowledged, False otherwise. If
        the action cannot be acknowledged, returns None.
        """
        # Check whether user has acknowledged the expiration.
        if self.action_type == AssignmentActions.EXPIRED:
            last_acknowledged_expiration = self.assignment.get_last_successful_acknowledged_expired_action()
            if not last_acknowledged_expiration:
                return False
            return last_acknowledged_expiration.completed_at > self.completed_at

        # Check whether user has acknowledged the cancellation.
        if self.action_type == AssignmentActions.CANCELLED:
            last_acknowledged_cancellation = self.assignment.get_last_successful_acknowledged_cancelled_action()
            if not last_acknowledged_cancellation:
                return False
            return last_acknowledged_cancellation.completed_at > self.completed_at

        return None
