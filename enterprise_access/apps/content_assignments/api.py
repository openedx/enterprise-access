"""
Primary Python API for interacting with Assignment
records and business logic.
"""
from __future__ import annotations  # needed for using QuerySet in type hinting.

import logging
from typing import Iterable
from uuid import uuid4

from django.db import transaction
from django.db.models import Q, Sum
from django.db.models.functions import Lower
from django.utils.timezone import now
from pytz import UTC

from enterprise_access.apps.content_assignments.tasks import send_reminder_email_for_pending_assignment
from enterprise_access.apps.core.models import User
from enterprise_access.apps.subsidy_access_policy.content_metadata_api import get_and_cache_content_metadata

from .constants import AssignmentAutomaticExpiredReason, LearnerContentAssignmentStateChoices
from .content_metadata_api import parse_datetime_string
from .models import AssignmentConfiguration, LearnerContentAssignment
from .tasks import (
    create_pending_enterprise_learner_for_assignment_task,
    send_assignment_automatically_expired_email,
    send_email_for_new_assignment
)
from .utils import chunks

logger = logging.getLogger(__name__)

# The number of emails we are allowed to filter on in a single User request.
#
# Batch size derivation inputs:
#   * The old MySQL client limit was 1 MB for a long time.
#   * 254 is the maximum number of characters in an email.
#   * 258 is the length an email plus 4 character delimiter: `', '`
#   * Divide result by 10 in case we are off by an order of magnitude.
#
# Batch size derivation formula: ((1 MB) / (258 B)) / 10 ≈ 350
USER_EMAIL_READ_BATCH_SIZE = 100


class AllocationException(Exception):
    """
    Exception class specific to allocation commands and queries.
    """
    user_message = 'An error occurred during allocation'


def _inexact_email_filter(emails, field_name='email'):
    """
    Helper that produces a Django Queryset filter
    to query for records by an ``email`` feel
    in a case-insensitive way.
    """
    email_filter = Q()
    for email in emails:
        kwargs = {f'{field_name}__iexact': email}
        email_filter |= Q(**kwargs)
    return email_filter


def create_assignment_configuration(enterprise_customer_uuid, **kwargs):
    """
    Create a new ``AssignmentConfiguration`` for the given customer identifier.
    """
    return AssignmentConfiguration.objects.create(
        enterprise_customer_uuid=enterprise_customer_uuid,
        **kwargs,
    )


def get_assignment_configuration(uuid):
    """
    Returns an `AssignmentConfiguration` record with the given uuid,
    or null if no such record exists.
    """
    try:
        return AssignmentConfiguration.objects.get(uuid=uuid)
    except AssignmentConfiguration.DoesNotExist:
        return None


def get_assignments_for_configuration(
    assignment_configuration,
    *args,
    **additional_filters,
):
    """
    Returns a queryset of all ``LearnerContentAssignment`` records
    for the given assignment configuration, optionally filtered
    further by the provided ``additional_filters``.
    """
    queryset = LearnerContentAssignment.objects.select_related(
        'assignment_configuration',
    ).filter(
        *args,
        assignment_configuration=assignment_configuration,
        **additional_filters,
    )
    return queryset


def get_assignments_for_admin(
    assignment_configuration,
    learner_emails,
    content_key,
):
    """
    Get any existing allocations relevant to an enterprise admin's allocation request.

    Method of content_key comparison for assignment lookup:

    +---+------------------------+-----------------------+--------------------+
    | # | assignment content_key | requested content_key |  How to compare?   |
    +---+------------------------+-----------------------+--------------------+
    | 1 | course                 | course                | Simple comparison. |
    | 2 | course                 | course run            | Not supported.     |
    | 3 | course run             | course                | Not supported.     |
    | 4 | course run             | course run            | Not supported.     |
    +---+------------------------+-----------------------+--------------------+

    Args:
        assignment_configuration (AssignmentConfiguration):
            The assignment configuration within which to search for assignments.
        learner_emails (list of str): A list of emails for which the admin intends to find existing assignments.
        content_key (str): A content key representing a course which the assignments are for.

    Returns:
        queryset of ``LearnerContentAssignment``: Existing records relevant to an admin's allocation request.
    """
    return get_assignments_for_configuration(
        assignment_configuration,
        _inexact_email_filter(learner_emails, field_name='learner_email'),
        content_key=content_key,
    )


def _normalize_course_key_from_metadata(assignment_configuration, content_key):
    """
    Helper method to take a course run key and normalize it into a coourse key
    utilizing the enterprise subsidy content metadata summary endpoint.
    """

    content_summary = _get_content_summary(assignment_configuration, content_key)
    return content_summary.get('content_key')


def get_assignment_for_learner(
    assignment_configuration,
    lms_user_id,
    content_key,
):
    """
    Get any existing allocations relevant to a learner's redemption request.

    There's no guarantee that the given `content_key` and the assignment object's `content_key` are both course keys or
    both course run keys, so a simple string comparison may not always suffice.  Method of content_key comparison for
    assignment lookup:

    +---+------------------------+-----------------------+----------------------------------------------+
    | # | assignment content_key | requested content_key |              How to compare?                 |
    +---+------------------------+-----------------------+----------------------------------------------+
    | 1 | course                 | course                | Simple comparison.                           |
    | 2 | course                 | course run            | Convert everything to courses, then compare. | (most common)
    | 3 | course run             | course                | Not supported.                               |
    | 4 | course run             | course run            | Not supported.                               |
    +---+------------------------+-----------------------+----------------------------------------------+

    Args:
        assignment_configuration (AssignmentConfiguration):
            The assignment configuration within which to search for assignments.
        lms_user_id (int): One lms_user_id which the assignments are for.
        content_key (str): A content key representing a course which the assignments are for.

    Returns:
        ``LearnerContentAssignment``: Existing assignment relevant to a learner's redemption request, or None if not
        found.

    Raises:
        ``django.core.exceptions.MultipleObjectsReturned``: This should be impossible because of a db-level uniqueness
        constraint across [assignment_configuration,lms_user_id,content_key].  BUT still technically possible if
        internal staff managed to create a duplicate assignment configuration for a single enterprise.
    """
    content_key_to_match = _normalize_course_key_from_metadata(assignment_configuration, content_key)
    if not content_key_to_match:
        logger.error(f'Unable to normalize content_key {content_key} for {assignment_configuration} and {lms_user_id}')
        return None
    queryset = LearnerContentAssignment.objects.select_related('assignment_configuration')
    try:
        return queryset.get(
            assignment_configuration=assignment_configuration,
            lms_user_id=lms_user_id,
            # assignment content_key is assumed to always be a course with no namespace prefix.
            content_key=content_key_to_match,
        )
    except LearnerContentAssignment.DoesNotExist:
        return None


def get_allocated_quantity_for_configuration(assignment_configuration):
    """
    Returns a float representing the total quantity, in USD cents, currently allocated
    via Assignments for the given configuration.
    """
    assignments_queryset = get_assignments_for_configuration(
        assignment_configuration,
        state=LearnerContentAssignmentStateChoices.ALLOCATED,
    )
    aggregate = assignments_queryset.aggregate(
        total_quantity=Sum('content_quantity'),
    )
    return aggregate['total_quantity'] or 0


def allocate_assignments(assignment_configuration, learner_emails, content_key, content_price_cents):
    """
    Creates or updates an allocated assignment record
    for the given ``content_key`` in the given ``assignment_configuration``,
    for each email in the list of ``learner_emails``.

    For existing assignment records with a (config, learner, content) combination, this function
    does the following:
      * If the existing record is cancelled or errored, update the existing record state to allocated
      * If the existing record is allocated or accepted, don't do anything with the record

    Params:
      - ``assignment_configuration``: The AssignmentConfiguration record under which assignments should be allocated.
      - ``learner_emails``: A list of learner email addresses to whom assignments should be allocated.
      - ``content_key``: Typically a *course* key to which the learner is assigned.
      - ``content_price_cents``: The cost of redeeming the content, in USD cents, at the time of allocation. Should
        always be an integer >= 0.

    Returns: A dictionary of updated, created, and unchanged assignment records. e.g.
      ```
      {
        'updated': [Updated LearnerContentAssignment records],
        'created': [Newly-created LearnerContentAssignment records],
        'no-change': [LearnerContentAssignment records that matched
                      the provided (config, learner, content) combination,
                      but were already in an 'allocated' or 'accepted' state],
      }
      ```

    """
    if content_price_cents < 0:
        raise AllocationException('Allocation price must be >= 0')

    learner_emails_to_allocate = _deduplicate_learner_emails_to_allocate(learner_emails)

    # We store the allocated quantity as a (future) debit
    # against a store of value, so we negate the provided non-negative
    # content_price_cents, and then persist that in the assignment records.
    content_quantity = content_price_cents * -1

    # Fetch any existing assignments for all pairs of (learner, content) in this assignment config.
    existing_assignments = get_assignments_for_admin(
        assignment_configuration,
        learner_emails_to_allocate,
        content_key,
    )

    # Existing Assignments in consideration by state
    already_allocated_or_accepted = []
    cancelled_or_errored_to_update = []

    # Maintain a set of emails with existing records - we know we don't have to create
    # new assignments for these.
    learner_emails_with_existing_assignments = set()

    # Try to populate lms_user_id field on any existing assignments found.  We already ran this function on these
    # assignments (when they were created in a prior request), but time has passed since then so the outcome might be
    # different this time. It's technically possible some learners have registered since the last request.
    assignments_with_updated_lms_user_id = _try_populate_assignments_lms_user_id(existing_assignments)

    # Split up the existing assignment records by state
    for assignment in existing_assignments:
        learner_emails_with_existing_assignments.add(assignment.learner_email.lower())
        if assignment.state in LearnerContentAssignmentStateChoices.REALLOCATE_STATES:
            assignment.content_quantity = content_quantity
            assignment.state = LearnerContentAssignmentStateChoices.ALLOCATED
            assignment.full_clean()
            cancelled_or_errored_to_update.append(assignment)
        else:
            already_allocated_or_accepted.append(assignment)

    # These two sets of updated assignments may contain duplicates when combined. Since the duplicates are just
    # references to the same assignment object, and django model instances are hashable (on PK), they can be
    # de-duplicated using set union.
    existing_assignments_to_update = set(cancelled_or_errored_to_update).union(assignments_with_updated_lms_user_id)

    with transaction.atomic():
        # Bulk update and get a list of refreshed objects
        updated_assignments = _update_and_refresh_assignments(
            existing_assignments_to_update,
            [
                # `lms_user_id` is updated via the _try_populate_assignments_lms_user_id() function.
                'lms_user_id',
                # `content_quantity` and `state` are updated via the for-loop above.
                'content_quantity', 'state',
            ]
        )

        # Narrow down creation list of learner emails
        learner_emails_for_assignment_creation = {
            email for email in learner_emails_to_allocate
            if email.lower() not in learner_emails_with_existing_assignments
        }

        # Initialize and save LearnerContentAssignment instances for each of them
        created_assignments = _create_new_assignments(
            assignment_configuration,
            learner_emails_for_assignment_creation,
            content_key,
            content_quantity,
        )

    # Enqueue an asynchronous task to link assigned learners to the customer
    # This has to happen outside of the atomic block to avoid a race condition
    # when the celery task does its read of updated/created assignments.
    for assignment in updated_assignments + created_assignments:
        create_pending_enterprise_learner_for_assignment_task.delay(assignment.uuid)
        send_email_for_new_assignment.delay(assignment.uuid)

    # Return a mapping of the action we took to lists of relevant assignment records.
    return {
        'updated': updated_assignments,
        'created': created_assignments,
        'no_change': already_allocated_or_accepted,
    }


def _deduplicate_learner_emails_to_allocate(learner_emails):
    """
    Helper to deduplicate learner emails to allocate before any
    allocation queries or logic take place.
    """
    deduplicated = []
    seen_lowercased_emails = set()
    for email in learner_emails:
        email_lower = email.lower()
        if email_lower not in seen_lowercased_emails:
            deduplicated.append(email)
            seen_lowercased_emails.add(email_lower)
    return deduplicated


def _update_and_refresh_assignments(assignment_records, fields_changed):
    """
    Helper to bulk save the given assignment_records
    and refresh their state from the DB.
    """
    # Save the assignments to update
    LearnerContentAssignment.bulk_update(assignment_records, fields_changed)

    # Get a list of refreshed objects that we just updated, along with their prefetched action records
    return list(
        LearnerContentAssignment.objects.prefetch_related('actions').filter(
            uuid__in=[record.uuid for record in assignment_records],
        )
    )


def _get_content_summary(assignment_configuration, content_key):
    """
    Helper to retrieve (from cache) the content metadata summary
    """
    content_metadata = get_and_cache_content_metadata(
        assignment_configuration.enterprise_customer_uuid,
        content_key,
    )
    return content_metadata


def _get_content_title(assignment_configuration, content_key):
    """
    Helper to retrieve (from cache) the title of a content_key'ed content_metadata
    """
    content_metadata = _get_content_summary(assignment_configuration, content_key)
    return content_metadata.get('content_title')


def _try_populate_assignments_lms_user_id(assignments):
    """
    For all given assignments, try to populate the lms_user_id field based on a matching User.

    Notes:
    * This function does NOT save() the assignment record, only alters the given objects as a side-effect..
    * This is a best-effort only; most of the time a User will not exist for an assignment, and this function is a no-op
      for that assignment.
    * If multiple User records match based on email, choice is non-deterministic.
    * Performance: This results in one or more reads against the User model.  The chunk size has been tuned to minimize
      the number of reads while simultaneously avoiding hard limits on statement length.

    Args:
        assignments (list of LearnerContentAssignment):
            The unsaved assignments on which to update the lms_user_id field.

    Returns:
        list of LearnerContentAssignment: A non-strict subset of the input assignments, only the ones altered.
    """
    # only operate on assignments that actually need to be updated.
    assignments_with_empty_lms_user_id = [assignment for assignment in assignments if assignment.lms_user_id is None]

    assignments_to_save = []

    for assignment_chunk in chunks(assignments_with_empty_lms_user_id, USER_EMAIL_READ_BATCH_SIZE):
        emails = [assignment.learner_email for assignment in assignment_chunk]
        # Construct a list of tuples containing (email, lms_user_id) for every assignment in this chunk.
        # this is the part that could exceed max statement length if batch size is too large.
        # There's no case-insensitive IN query in Django, so we have to build up a big
        # OR type of query.
        email_lms_user_id = User.objects.filter(
            _inexact_email_filter(emails, field_name='email'),
            lms_user_id__isnull=False,
        ).annotate(
            email_lower=Lower('email'),
        ).values_list('email_lower', 'lms_user_id')

        # dict() on a list of 2-tuples treats the first elements as keys and second elements as values.
        lms_user_id_by_email = dict(email_lms_user_id)

        for assignment in assignment_chunk:
            lms_user_id = lms_user_id_by_email.get(assignment.learner_email.lower())
            if lms_user_id:
                assignment.lms_user_id = lms_user_id
                assignments_to_save.append(assignment)

    return assignments_to_save


def _create_new_assignments(assignment_configuration, learner_emails, content_key, content_quantity):
    """
    Helper to bulk save new LearnerContentAssignment instances.
    """
    # First, prepare assignment objects using data available in-memory only.
    content_title = _get_content_title(assignment_configuration, content_key)
    assignments_to_create = []
    allocation_batch_id = uuid4()
    for learner_email in learner_emails:
        assignment = LearnerContentAssignment(
            assignment_configuration=assignment_configuration,
            learner_email=learner_email,
            content_key=content_key,
            content_title=content_title,
            content_quantity=content_quantity,
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
            allocation_batch_id=allocation_batch_id,
        )
        assignments_to_create.append(assignment)

    # Next, try to populate the lms_user_id field on all assignments to be created (resulting in reads against User).
    # Note: This covers the case where an admin assigns content to a learner AFTER they register.  For the case where an
    # admin assigns content to a learner BEFORE they register, see the User post_save hook implemented in signals.py.
    #
    # Do not store result because we are simply relying on the side-effect of the function (a subset of
    # `assignments_to_create` has been altered).
    _ = _try_populate_assignments_lms_user_id(assignments_to_create)

    # Validate all assignments to be created.
    for assignment in assignments_to_create:
        assignment.clean()

    # Do the bulk creation to save these records
    created_assignments = LearnerContentAssignment.bulk_create(assignments_to_create)

    # Return a list of refreshed objects that we just created, along with their prefetched action records
    return list(
        LearnerContentAssignment.objects.prefetch_related('actions').filter(
            uuid__in=[record.uuid for record in created_assignments],
        )
    )


def cancel_assignments(assignments: Iterable[LearnerContentAssignment]) -> dict:
    """
    Bulk cancel assignments.

    This is a no-op for assignments in the following non-cancelable states: [accepted, cancelled].  Cancelled and
    already-cancelled assignments are bundled in the response because this function is meant to be idempotent.

    Args:
        assignments (list(LearnerContentAssignment)): One or more assignments to cancel.

    Returns:
        A dict representing cancelled and non-cancelable assignments:
        {
            'cancelled': <list of 0 or more cancelled or already-cancelled assignments>,
            'non-cancelable': <list of 0 or more non-cancelable assignments, e.g. already accepted assignments>,
        }
    """
    # pylint: disable=import-outside-toplevel
    from enterprise_access.apps.content_assignments.tasks import send_cancel_email_for_pending_assignment

    cancelable_assignments = set(
        assignment for assignment in assignments
        if assignment.state in LearnerContentAssignmentStateChoices.CANCELABLE_STATES
    )
    already_cancelled_assignments = set(
        assignment for assignment in assignments
        if assignment.state == LearnerContentAssignmentStateChoices.CANCELLED
    )
    non_cancelable_assignments = set(assignments) - cancelable_assignments - already_cancelled_assignments

    logger.info(f'Skipping {len(non_cancelable_assignments)} non-cancelable assignments.')
    logger.info(f'Skipping {len(already_cancelled_assignments)} already cancelled assignments.')
    logger.info(f'Canceling {len(cancelable_assignments)} assignments.')

    for assignment_to_cancel in cancelable_assignments:
        assignment_to_cancel.state = LearnerContentAssignmentStateChoices.CANCELLED

    cancelled_assignments = _update_and_refresh_assignments(cancelable_assignments, ['state'])
    for cancelled_assignment in cancelled_assignments:
        send_cancel_email_for_pending_assignment.delay(cancelled_assignment.uuid)

    return {
        'cancelled': list(set(cancelled_assignments) | already_cancelled_assignments),
        'non_cancelable': list(non_cancelable_assignments),
    }


def remind_assignments(assignments: Iterable[LearnerContentAssignment]) -> dict:
    """
    Bulk remind assignments.

    This is a no-op for assignments in the following states: [accepted, errored, canceled]. We only allow
    assignments which are in the allocated state. Reminded and already-reminded assignments are bundled in
    the response because this function is meant to be idempotent.


    Args:
        assignments (list(LearnerContentAssignment)): One or more assignments to remind.

    Returns:
        A dict representing reminded and non-remindable assignments:
        {
            'reminded': <list of 0 or more reminded or already-reminded assignments>,
            'non-remindable': <list of 0 or more non-remindable assignments>,
        }
    """
    remindable_assignments = set(
        assignment for assignment in assignments
        if assignment.state in LearnerContentAssignmentStateChoices.REMINDABLE_STATES
    )

    non_remindable_assignments = set(assignments) - remindable_assignments

    logger.info(f'Skipping {len(non_remindable_assignments)} non-remindable assignments.')
    logger.info(f'Reminding {len(remindable_assignments)} assignments.')

    reminded_assignments = _update_and_refresh_assignments(remindable_assignments, ['state'])
    for reminded_assignment in reminded_assignments:
        send_reminder_email_for_pending_assignment.delay(reminded_assignment.uuid)

    return {
        'reminded': list(set(reminded_assignments)),
        'non_remindable': list(non_remindable_assignments),
    }


def expire_assignment(assignment, content_metadata, modify_assignment=True):
    """
    If applicable, retires the given assignment, returning an expiration reason.
    Otherwise, returns `None` as a reason.
    """
    if assignment.state not in LearnerContentAssignmentStateChoices.EXPIRABLE_STATES:
        logger.info('Cannot expire accepted assignment %s', assignment.uuid)
        return None

    assignment_configuration = assignment.assignment_configuration
    subsidy_expiration_datetime = parse_datetime_string(assignment_configuration.policy.subsidy_expiration_datetime)
    if subsidy_expiration_datetime:
        subsidy_expiration_datetime = subsidy_expiration_datetime.replace(tzinfo=UTC)

    enrollment_end_date = _get_enrollment_end_date(content_metadata)
    auto_cancellation_date = assignment.get_auto_expiration_date()

    message = (
        'Checking expirability for AssignmentUUID: [%s], ContentKey: [%s], AssignmentExpiry: [%s], '
        'EnrollmentEnd: [%s], SubsidyExpiry: [%s]',
    )
    logger.info(
        message,
        assignment.uuid,
        assignment.content_key,
        auto_cancellation_date,
        enrollment_end_date,
        subsidy_expiration_datetime,
    )

    assignment_expiry_reason = None
    current_date = now()

    if auto_cancellation_date and current_date > auto_cancellation_date:
        assignment_expiry_reason = AssignmentAutomaticExpiredReason.NINETY_DAYS_PASSED
    elif enrollment_end_date and enrollment_end_date < current_date:
        assignment_expiry_reason = AssignmentAutomaticExpiredReason.ENROLLMENT_DATE_PASSED
    elif subsidy_expiration_datetime and subsidy_expiration_datetime < current_date:
        assignment_expiry_reason = AssignmentAutomaticExpiredReason.SUBSIDY_EXPIRED

    if assignment_expiry_reason:
        logger.info(
            'Assignment should be expired. AssignmentConfigUUID: [%s], AssignmentUUID: [%s], Reason: [%s]',
            assignment_configuration.uuid,
            assignment.uuid,
            assignment_expiry_reason,
        )

        if modify_assignment:
            logger.info('Modifying assignment %s to expired', assignment.uuid)
            assignment.state = LearnerContentAssignmentStateChoices.CANCELLED

            if assignment_expiry_reason == AssignmentAutomaticExpiredReason.NINETY_DAYS_PASSED:
                assignment.clear_pii()
                assignment.clear_historical_pii()

            assignment.save()
            send_assignment_automatically_expired_email.delay(assignment.uuid)

    return assignment_expiry_reason


def _get_enrollment_end_date(content_metadata):
    """
    Helper to get the enrollment end date from a content metadata record.
    """
    if content_metadata is not None:
        normalized_metadata = content_metadata.get('normalized_metadata') or {}
        enrollment_end_date_str = normalized_metadata.get('enroll_by_date')
        try:
            datetime_obj = parse_datetime_string(enrollment_end_date_str)
            if datetime_obj:
                return datetime_obj.replace(tzinfo=UTC)
        except ValueError:
            logger.warning(
                'Bad datetime format for %s, value: %s',
                content_metadata.get('key'),
                enrollment_end_date_str,
            )
    return None
