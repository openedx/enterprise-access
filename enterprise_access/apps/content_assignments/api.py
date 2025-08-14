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

from enterprise_access.apps.content_assignments.content_metadata_api import (
    get_content_metadata_for_assignments,
    is_date_n_days_from_now,
    parse_datetime_string
)
from enterprise_access.apps.content_assignments.tasks import (
    send_exec_ed_enrollment_warmer,
    send_reminder_email_for_pending_assignment
)
from enterprise_access.apps.core.models import User
from enterprise_access.apps.subsidy_access_policy.content_metadata_api import get_and_cache_content_metadata
from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates
from enterprise_access.utils import (
    chunks,
    get_automatic_expiration_date_and_reason,
    get_normalized_metadata_for_assignment,
    localized_utcnow
)

from .constants import AssignmentAutomaticExpiredReason, LearnerContentAssignmentStateChoices
from .models import AssignmentConfiguration, LearnerContentAssignment
from .tasks import (
    create_pending_enterprise_learner_for_assignment_task,
    send_assignment_automatically_expired_email,
    send_email_for_new_assignment
)

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

ASSIGNMENT_REALLOCATION_FIELDS = [
    'lms_user_id', 'learner_email', 'allocation_batch_id',
    'content_quantity', 'state', 'preferred_course_run_key',
    'allocated_at', 'cancelled_at', 'expired_at', 'errored_at',
    'parent_content_key', 'is_assigned_course_run',
]


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
        content_key (str): A content key representing a course or course run which the assignments are for.

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
    Helper method to take a course run key and normalize it into a course key
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

    +---+------------------------+-----------------------+-------------------------------------------------+
    | # | assignment content_key | requested content_key |                     How to compare?             |
    +---+------------------------+-----------------------+-------------------------------------------------+
    | 1 | course                 | course                | Simple comparison.                              |
    | 2 | course                 | course run            | Convert course run to course key, then compare. |
    | 3 | course run             | course                | Simple comparison via the parent_content_key,   |
    |   |                        |                       | returning first run-based assignment match.     |
    | 4 | course run             | course run            | Simple comparison.                              |
    +---+------------------------+-----------------------+-------------------------------------------------+

    Args:
        assignment_configuration (AssignmentConfiguration):
            The assignment configuration within which to search for assignments.
        lms_user_id (int): One lms_user_id which the assignments are for.
        content_key (str): A content key representing a course or course run which the assignments are for.

    Returns:
        ``LearnerContentAssignment``: Existing assignment relevant to a learner's redemption request, or None if not
        found.

    Raises:
        ``django.core.exceptions.MultipleObjectsReturned``: This should be impossible because of a db-level uniqueness
        constraint across [assignment_configuration,lms_user_id,content_key].  BUT still technically possible if
        internal staff managed to create a duplicate assignment configuration for a single enterprise.
    """
    queryset = LearnerContentAssignment.objects.select_related('assignment_configuration')

    try:
        # First, try to find a corresponding assignment based on the specific content key,
        # considering both assignments' content key and parent content key.
        return queryset.get(
            Q(content_key=content_key) | Q(parent_content_key=content_key),
            assignment_configuration=assignment_configuration,
            lms_user_id=lms_user_id,
        )
    except LearnerContentAssignment.DoesNotExist:
        logger.info(
            f'No assignment found with content_key or parent_content_key {content_key} '
            f'for {assignment_configuration} and lms_user_id {lms_user_id}',
        )
    except LearnerContentAssignment.MultipleObjectsReturned as exc:
        logger.error(
            f'Multiple assignments found with content_key or parent_content_key {content_key} '
            f'for {assignment_configuration} and lms_user_id {lms_user_id}',
        )
        raise exc

    # If no exact match was found, try to normalize the content key and find a match. This happens when
    # the content_key is a course run key and the assignment's content_key is a course key, as depicted
    # by row 2 in the above docstring matrix.
    content_key_to_match = _normalize_course_key_from_metadata(assignment_configuration, content_key)
    if not content_key_to_match:
        logger.error(f'Unable to normalize content_key {content_key} for {assignment_configuration} and {lms_user_id}')
        return None

    try:
        return queryset.get(
            content_key=content_key_to_match,
            assignment_configuration=assignment_configuration,
            lms_user_id=lms_user_id,
        )
    except LearnerContentAssignment.DoesNotExist:
        logger.info(
            f'No assignment found with normalized content_key {content_key_to_match} '
            f'for {assignment_configuration} and lms_user_id {lms_user_id}',
        )
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


def allocate_assignments(
    assignment_configuration, learner_emails, content_key, content_price_cents, known_lms_user_ids=None,
):
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
      - ``content_key``: Either a course or course run key, representing the content to be allocated.
      - ``content_price_cents``: The cost of redeeming the content, in USD cents, at the time of allocation. Should
        always be an integer >= 0.
      - ``known_lms_user_ids``: Optional list of known lms user ids corresponding to the provided emails.
        If present, it's assumed to be *all* lms user ids for the provided emails, and that no duplicate
        user emails are provided.

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
    # Set a batch ID to track assignments updated and/or created together.
    allocation_batch_id = uuid4()

    message = (
        'Allocating assignments: assignment_configuration=%s, batch_id=%s, '
        'learner_emails=%s, content_key=%s, content_price_cents=%s'
    )
    logger.info(
        message, assignment_configuration.uuid, allocation_batch_id,
        learner_emails, content_key, content_price_cents
    )

    if content_price_cents < 0:
        raise AllocationException('Allocation price must be >= 0')

    learner_emails_to_allocate = _deduplicate_learner_emails_to_allocate(learner_emails)

    # We store the allocated quantity as a (future) debit
    # against a store of value, so we negate the provided non-negative
    # content_price_cents, and then persist that in the assignment records.
    content_quantity = content_price_cents * -1

    if known_lms_user_ids:
        lms_user_ids_by_email = dict(zip(
            [email.lower() for email in learner_emails_to_allocate],
            known_lms_user_ids
        ))
        emails_by_lms_user_id = dict(zip(known_lms_user_ids, learner_emails_to_allocate))
    else:
        lms_user_ids_by_email, emails_by_lms_user_id = _map_allocation_emails_with_lms_user_ids(
            learner_emails_to_allocate,
        )
    existing_assignments = _get_existing_assignments_for_allocation(
        assignment_configuration,
        learner_emails_to_allocate,
        content_key,
        lms_user_ids_by_email,
    )
    # Maintain a set of emails with existing records - we know we don't have to create
    # new assignments for these.
    learner_emails_with_existing_assignments = set()

    # Keep a running list of all existing assignments that will need to be included in bulk update.
    existing_assignments_needs_update = set()

    # This step to find and update the preferred_course_run_key is required in order
    # for nudge emails to target the start date of the new run. For run-based assignments,
    # the preferred_course_run_key is the same as the assignment's content_key.
    preferred_course_run_key = _get_preferred_course_run_key(assignment_configuration, content_key)

    # Determine if the assignment's content_key is a course run or a course key based
    # on an associated parent content key. If the parent content key is None, then the
    # assignment is for a course; otherwise, it's an assignment for a course run.
    parent_content_key = _get_parent_content_key(assignment_configuration, content_key)
    is_assigned_course_run = bool(parent_content_key)

    # Split up the existing assignment records by state
    for assignment in existing_assignments:
        if not assignment.lms_user_id:
            existing_lms_user_id = lms_user_ids_by_email.get(assignment.learner_email.lower())
            if existing_lms_user_id:
                assignment.lms_user_id = existing_lms_user_id
                existing_assignments_needs_update.add(assignment)

        if assignment.state == LearnerContentAssignmentStateChoices.EXPIRED and assignment.lms_user_id is not None:
            # If the existing assignment is expired and has an lms_user_id, it has a retired/expired email address
            # that we want to change based on our lookup of lms_user_id -> email.
            assignment_email_from_lms_user_id = emails_by_lms_user_id.get(assignment.lms_user_id)
            if assignment_email_from_lms_user_id is not None:
                assignment.learner_email = assignment_email_from_lms_user_id
                existing_assignments_needs_update.add(assignment)

        if assignment.state in LearnerContentAssignmentStateChoices.REALLOCATE_STATES:
            _reallocate_assignment(
                assignment,
                content_quantity,
                allocation_batch_id,
                preferred_course_run_key,
                parent_content_key,
                is_assigned_course_run,
            )
            existing_assignments_needs_update.add(assignment)
        elif assignment.state == LearnerContentAssignmentStateChoices.ALLOCATED:
            # For some already-allocated assignments being re-assigned, we might still need to update the preferred
            # course run for nudge email purposes.
            if assignment.preferred_course_run_key != preferred_course_run_key:
                assignment.preferred_course_run_key = preferred_course_run_key
                existing_assignments_needs_update.add(assignment)
            # Update the parent_content_key and is_assigned_course_run fields if they have changed.
            if assignment.parent_content_key != parent_content_key:
                assignment.parent_content_key = parent_content_key
                existing_assignments_needs_update.add(assignment)
            if assignment.is_assigned_course_run != is_assigned_course_run:
                assignment.is_assigned_course_run = is_assigned_course_run
                existing_assignments_needs_update.add(assignment)

        learner_emails_with_existing_assignments.add(assignment.learner_email.lower())

    with transaction.atomic():
        # Bulk update and get a list of refreshed objects
        updated_assignments = _update_and_refresh_assignments(
            existing_assignments_needs_update,
            ASSIGNMENT_REALLOCATION_FIELDS,
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
            lms_user_ids_by_email,
            allocation_batch_id,
        )

    # Enqueue an asynchronous task to link assigned learners to the customer
    # This has to happen outside of the atomic block to avoid a race condition
    # when the celery task does its read of updated/created assignments.
    for assignment in updated_assignments + created_assignments:
        create_pending_enterprise_learner_for_assignment_task.delay(assignment.uuid)
        send_email_for_new_assignment.delay(assignment.uuid)

    # Make a list of all pre-existing assignments that were not updated.
    unchanged_assignments = list(set(existing_assignments) - set(updated_assignments))

    # Return a mapping of the action we took to lists of relevant assignment records.
    return {
        'updated': updated_assignments,
        'created': created_assignments,
        'no_change': unchanged_assignments,
    }


def allocate_assignment_for_request(
    assignment_configuration,
    learner_email,
    content_key,
    content_price_cents,
    lms_user_id,
):
    """
    Creates or reallocates an assignment record for the given ``content_key`` in the given ``assignment_configuration``,
      and the provided ``learner_email``.

    Params:
      - ``assignment_configuration``: The AssignmentConfiguration record under which assignments should be allocated.
      - ``learner_email``: The email address of the learner to whom the assignment should be allocated.
      - ``content_key``: Either a course or course run key, representing the content to be allocated.
      - ``content_price_cents``: The cost of redeeming the content, in USD cents, at the time of allocation. Should
        always be an integer >= 0.
      - ``lms_user_id``: lms user id of the user.

    Returns: A LearnerContentAssignment record that was created or None.
    """
    # Set a batch ID to track assignments updated and/or created together.
    allocation_batch_id = uuid4()

    message = (
        'Allocating assignments: assignment_configuration=%s, batch_id=%s, '
        'learner_email=%s, content_key=%s, content_price_cents=%s'
    )
    logger.info(
        message, assignment_configuration.uuid, allocation_batch_id,
        learner_email, content_key, content_price_cents
    )

    if content_price_cents < 0:
        raise AllocationException('Allocation price must be >= 0')

    # We store the allocated quantity as a (future) debit
    # against a store of value, so we negate the provided non-negative
    # content_price_cents, and then persist that in the assignment records.
    content_quantity = content_price_cents * -1
    lms_user_ids_by_email = {learner_email.lower(): lms_user_id}
    existing_assignments = _get_existing_assignments_for_allocation(
        assignment_configuration,
        [learner_email],
        content_key,
        lms_user_ids_by_email,
    )

    # Re-allocate existing assignment
    if len(existing_assignments) > 0:
        assignment = next(iter(existing_assignments), None)
        if assignment and assignment.state in LearnerContentAssignmentStateChoices.REALLOCATE_STATES:
            preferred_course_run_key = _get_preferred_course_run_key(assignment_configuration, content_key)
            parent_content_key = _get_parent_content_key(assignment_configuration, content_key)
            is_assigned_course_run = bool(parent_content_key)
            _reallocate_assignment(
                assignment,
                content_quantity,
                allocation_batch_id,
                preferred_course_run_key,
                parent_content_key,
                is_assigned_course_run,
            )
            assignment.save()
            return assignment

    assignment = _create_new_assignments(
        assignment_configuration,
        [learner_email],
        content_key,
        content_quantity,
        lms_user_ids_by_email,
        allocation_batch_id,
    )
    # If the assignment was created, it will be a list with one item.
    if assignment:
        return assignment[0]
    return None


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


def _map_allocation_emails_with_lms_user_ids(learner_emails_to_allocate):
    """
    To allocate assignments, we'll need to lookup existing assignments
    by both email *and* lms_user_id. We'll also use these lms_user_ids
    to populate the `lms_user_id` field on existing assignments that
    don't currently have the field populated. The returned mapping of emails -> lms_user_id
    contains **lowered** email values.

    We'll also need a reverse lookup of lms_user_id -> email
    to re-populate the `learner_email` of existing assignments that are
    in an expired state (and thus have an auto-generated, non-identifiable `learner_email` value).
    The returned mapping of lms_user_id -> emails contains emails values
    in the casing provided by the caller.

    Returns:
      Two dicts, the first mapping email -> lms_user_id, and the second
      mapping lms_user_id -> email.
    """
    lms_user_ids_by_email = _get_lms_user_ids_by_email(learner_emails_to_allocate)

    emails_by_lms_user_id = {}
    for provided_email in learner_emails_to_allocate:
        lms_user_id = lms_user_ids_by_email.get(provided_email.lower())
        if lms_user_id is not None:
            emails_by_lms_user_id[lms_user_id] = provided_email

    return lms_user_ids_by_email, emails_by_lms_user_id


def _get_lms_user_ids_by_email(emails):
    """
    Helper to return a mapping of learner email addresses to lms_user_id.
    If no user record exists with a given email address, it will *not*
    be present in the mapping.

    Performance note: This results in one or more reads against the User model.
      The chunk size has been tuned to minimize the number of reads
      while simultaneously avoiding hard limits on statement length.
    """
    lms_user_ids_by_email = {}
    for email_chunk in chunks(emails, USER_EMAIL_READ_BATCH_SIZE):
        # Construct a list of tuples containing (email, lms_user_id) for every email in this chunk.
        # this is the part that could exceed max statement length if batch size is too large.
        # There's no case-insensitive IN query in Django, so we have to build up a big
        # OR type of query.
        queryset = User.objects.filter(
            _inexact_email_filter(email_chunk, field_name='email'),
            lms_user_id__isnull=False,
        ).annotate(
            email_lower=Lower('email'),
        ).values_list('email_lower', 'lms_user_id')

        # dict() on a list of 2-tuples treats the first elements as keys and second elements as values.
        lms_user_ids_by_email.update(dict(queryset))

    return lms_user_ids_by_email


def _get_existing_assignments_for_allocation(
    assignment_configuration, learner_emails_to_allocate, content_key, lms_user_ids_by_email,
):
    """
    Finds any existing assignments records related to the provided ``assignment_cofiguration``,
    ``content_key``, and the learners the client has requested to allocate.
    Learners are identified either via the `learner_email` field, or via the `lms_user_id` field
    based on the provided ``learner_emails_to_allocate`` and the provided mapping
    ``lms_user_ids_by_email``.
    """
    # Compose a set of all existing assignments related to the requested allocation.
    # A set is a fine data structure because Django models are hashable on their PK.
    existing_assignments = set()

    # Fetch any existing assignments for all pairs of (learner email, content) in this assignment config.
    assignments_for_emails_queryset = get_assignments_for_admin(
        assignment_configuration, learner_emails_to_allocate, content_key,
    )
    existing_assignments.update(assignments_for_emails_queryset)

    # Fetch existing assignments for all pairs of (known lms_user_id, content) in this assignment config.
    assignments_for_lms_user_ids_queryset = get_assignments_for_configuration(
        assignment_configuration,
        lms_user_id__in=lms_user_ids_by_email.values(),
        content_key=content_key,
    )
    existing_assignments.update(assignments_for_lms_user_ids_queryset)

    return existing_assignments


def _reallocate_assignment(
        assignment,
        content_quantity,
        allocation_batch_id,
        preferred_course_run_key,
        parent_content_key,
        is_assigned_course_run):
    """
    Modifies a ``LearnerContentAssignment`` record during the allocation flow.  The record
    is **not** saved.
    """
    assignment.content_quantity = content_quantity
    assignment.state = LearnerContentAssignmentStateChoices.ALLOCATED
    assignment.allocation_batch_id = allocation_batch_id
    assignment.allocated_at = localized_utcnow()
    assignment.accepted_at = None
    assignment.cancelled_at = None
    assignment.expired_at = None
    assignment.errored_at = None
    assignment.preferred_course_run_key = preferred_course_run_key
    assignment.parent_content_key = parent_content_key
    assignment.is_assigned_course_run = is_assigned_course_run
    # Prevent invalid data from entering the database by calling the low-level full_clean() function manually.
    assignment.full_clean()
    return assignment


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
    course_content_metadata = _get_content_summary(assignment_configuration, content_key)
    return course_content_metadata.get('content_title')


def _get_parent_content_key(assignment_configuration, content_key):
    """
    Helper to retrieve (from cache) the parent content key of a content_key's content_metadata.
    Note: content_key is either a course run key or a course key. Only course run keys have a
    parent course key.

    If content_key is for a course key, this will return the same key. Otherwise, the content_key
    represents a course run, and this will return the run's parent course key.
    """
    course_content_metadata = _get_content_summary(assignment_configuration, content_key)
    metadata_content_key = course_content_metadata.get('content_key')

    # Check if the assignment's content_key matches the returned content_key. If so, this is a course key
    # which has no parent key.
    if content_key == metadata_content_key:
        return None

    # Otherwise, this is a course run key, so return the parent course key
    return metadata_content_key


def _get_preferred_course_run_key(assignment_configuration, content_key):
    """
    During assignment allocation, time has passed since the last time an assignment
    was allocated/re-allocated. Therefore, it's entirely possible a new course run has been published.
    We assume the intent of the admin re-allocating this content is that they want to assign the NEW run.

    Returns:
      The preferred course run key (from cache) of a content_key'ed content_metadata
    """
    course_content_metadata = _get_content_summary(assignment_configuration, content_key)
    return course_content_metadata.get('course_run_key')


def _create_new_assignments(
    assignment_configuration,
    learner_emails,
    content_key,
    content_quantity,
    lms_user_ids_by_email,
    allocation_batch_id
):
    """
    Helper to bulk save new LearnerContentAssignment instances.
    """
    message = (
        'Allocation starting to create records: assignment_configuration=%s, batch_id=%s, '
        'learner_emails=%s, content_key=%s'
    )
    logger.info(
        message, assignment_configuration.uuid, allocation_batch_id,
        learner_emails, content_key,
    )

    # First, prepare assignment objects using data available in-memory only.
    content_title = _get_content_title(assignment_configuration, content_key)
    parent_content_key = _get_parent_content_key(assignment_configuration, content_key)
    preferred_course_run_key = _get_preferred_course_run_key(assignment_configuration, content_key)
    is_assigned_course_run = bool(parent_content_key)

    assignments_to_create = []
    for learner_email in learner_emails:
        assignment = LearnerContentAssignment(
            assignment_configuration=assignment_configuration,
            learner_email=learner_email,
            lms_user_id=lms_user_ids_by_email.get(learner_email.lower()),
            content_key=content_key,
            parent_content_key=parent_content_key,
            is_assigned_course_run=is_assigned_course_run,
            preferred_course_run_key=preferred_course_run_key,
            content_title=content_title,
            content_quantity=content_quantity,
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
            allocation_batch_id=allocation_batch_id,
            allocated_at=localized_utcnow(),
        )
        assignments_to_create.append(assignment)

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


def cancel_assignments(assignments: Iterable[LearnerContentAssignment], send_cancel_email_to_learner=True) -> dict:
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
        if send_cancel_email_to_learner:
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


def nudge_assignments(assignments, assignment_configuration_uuid, days_before_course_start_date):
    """
    Nudge assignments.

    This is a no-op for assignments in the following state: [allocated, errored, canceled, expired]. We only allow
    assignments which are in the accepted state.


    Args:
        assignments: An iterable of assignments associated to the payloads assignment_uuids and
        associated assignment_configuration_uuid
        assignment_configuration_uuid: Uuid of the assignment configuration from the api path
        days_before_course_start_date: Number of days prior to start date to nudge individual assignment
    """

    # Declare our expected response output
    nudged_assignment_uuids = []
    unnudged_assignment_uuids = []

    # Isolate assignment configuration metadata and associated assignments
    assignment_configuration = AssignmentConfiguration.objects.get(uuid=assignment_configuration_uuid)
    subsidy_access_policy = assignment_configuration.subsidy_access_policy
    enterprise_catalog_uuid = subsidy_access_policy.catalog_uuid
    # Check each assignment to validate its state and retreive its content metadata
    for assignment in assignments:
        # Send a log and append to the unnudged_assignment_uuids response
        # list assignments states that are not 'accepted'
        # Then continue to the next assignment without sending a nudge email
        if assignment.state != LearnerContentAssignmentStateChoices.ACCEPTED:
            logger.info(
                '[API_BRAZE_EMAIL_CAMPAIGN_NUDGING_ERROR_1] assignment: [%s]',
                assignment
            )
            unnudged_assignment_uuids.append(assignment.uuid)
            continue

        # log metadata for observability relating to the assignment configuration
        message = (
            '[API_BRAZE_EMAIL_CAMPAIGN_NUDGING_1] '
            'Assignment Configuration uuid: [%s], assignmnet_uuid: [%s], '
            'subsidy_access_policy_uuid: [%s], enterprise_catalog_uuid: [%s], '
            'enterprise_customer_uuid: [%s] '
        )
        logger.info(
            message,
            assignment_configuration.uuid,
            assignment.uuid,
            subsidy_access_policy.uuid,
            enterprise_catalog_uuid,
            assignment_configuration.enterprise_customer_uuid,
        )

        # retrieve content_metadata for the assignment, and isolate the necessary fields
        content_metadata_for_assignments = get_content_metadata_for_assignments(
            enterprise_catalog_uuid,
            [assignment],
        )
        content_metadata = content_metadata_for_assignments.get(assignment.content_key, {})
        normalized_metadata = get_normalized_metadata_for_assignment(assignment, content_metadata)

        start_date = normalized_metadata.get('start_date')
        course_type = content_metadata.get('course_type')

        # check if the course_type is an executive-education course
        is_executive_education_course_type = course_type == 'executive-education-2u'

        # Determine if the date from today + days_before_course_state_date is
        # equal to the date of the start date
        # If they are equal, then send the nudge email, otherwise continue
        datetime_start_date = parse_datetime_string(start_date, set_to_utc=True)
        can_send_nudge_notification_in_advance = is_date_n_days_from_now(
            target_datetime=datetime_start_date,
            num_days=days_before_course_start_date
        )

        # Determine if we can nudge a user, if we can nudge, log a message, send the nudge,
        # and append to the nudged_assignment_uuids response list
        # Otherwise, log a message, and append to the nudged_assignment_uuids response list
        if is_executive_education_course_type and can_send_nudge_notification_in_advance:
            message = (
                '[API_BRAZE_EMAIL_CAMPAIGN_NUDGING_2] assignment_configuration_uuid: [%s], '
                'assignment_uuid: [%s], start_date: [%s], datetime_start_date: [%s], '
                'days_before_course_start_date: [%s], can_send_nudge_notification_in_advance: [%s], '
                'course_type: [%s], is_executive_education_course_type: [%s]'
            )
            logger.info(
                message,
                assignment_configuration_uuid,
                assignment.uuid,
                start_date,
                datetime_start_date,
                days_before_course_start_date,
                can_send_nudge_notification_in_advance,
                course_type,
                is_executive_education_course_type
            )
            send_exec_ed_enrollment_warmer.delay(assignment.uuid, days_before_course_start_date)
            nudged_assignment_uuids.append(assignment.uuid)
        else:
            message = (
                '[API_BRAZE_EMAIL_CAMPAIGN_NUDGING_ERROR_2] assignment_configuration_uuid: [%s], '
                'assignment_uuid: [%s], start_date: [%s], datetime_start_date: [%s], '
                'days_before_course_start_date: [%s], can_send_nudge_notification_in_advance: [%s], '
                'course_type: [%s], is_executive_education_course_type: [%s]'
            )
            logger.info(
                message,
                assignment_configuration_uuid,
                assignment.uuid,
                start_date,
                datetime_start_date,
                days_before_course_start_date,
                can_send_nudge_notification_in_advance,
                course_type,
                is_executive_education_course_type
            )
            unnudged_assignment_uuids.append(assignment.uuid)
    # returns the lists as an object to the response
    return {
        'nudged_assignment_uuids': nudged_assignment_uuids,
        'unnudged_assignment_uuids': unnudged_assignment_uuids
    }


def expire_assignment(
    assignment: LearnerContentAssignment,
    content_metadata: dict,
    modify_assignment: bool = True
):
    """
    If applicable, retires the given assignment, returning an expiration reason.
    Otherwise, returns `None` as a reason.
    """
    if assignment.state not in LearnerContentAssignmentStateChoices.EXPIRABLE_STATES:
        logger.info('Cannot expire accepted assignment %s', assignment.uuid)
        return None

    automatic_expiration_date_and_reason = get_automatic_expiration_date_and_reason(assignment, content_metadata)
    automatic_expiration_date = automatic_expiration_date_and_reason['date']
    automatic_expiration_reason = automatic_expiration_date_and_reason['reason']

    if not automatic_expiration_date or automatic_expiration_date > localized_utcnow():
        logger.info('Assignment %s is not expired yet', assignment.uuid)
        return None

    logger.info(
        'Assignment should be expired. AssignmentConfigUUID: [%s], AssignmentUUID: [%s], Reason: [%s]',
        assignment.assignment_configuration.uuid,
        assignment.uuid,
        automatic_expiration_reason,
    )

    if modify_assignment:
        logger.info('Modifying assignment %s to expired', assignment.uuid)
        assignment.state = LearnerContentAssignmentStateChoices.EXPIRED
        assignment.expired_at = localized_utcnow()

        if automatic_expiration_reason == AssignmentAutomaticExpiredReason.NINETY_DAYS_PASSED:
            assignment.clear_pii()

        credit_request = getattr(assignment, 'credit_request', None)

        if credit_request:
            logger.info('Modifying credit request %s to expired', credit_request.uuid)
            credit_request.state = SubsidyRequestStates.EXPIRED
            credit_request.save()

        assignment.save()

        if not credit_request:
            send_assignment_automatically_expired_email.delay(assignment.uuid)

    return automatic_expiration_reason
