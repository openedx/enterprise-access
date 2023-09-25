"""
Primary Python API for interacting with Assignment
records and business logic.
"""
from django.db.models import Sum

from .constants import LearnerContentAssignmentStateChoices
from .models import AssignmentConfiguration, LearnerContentAssignment


class AllocationException(Exception):
    """
    Exception class specific to allocation commands and queries.
    """


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
        assignment_configuration=assignment_configuration,
        **additional_filters,
    )
    return queryset


def get_assignments_by_learner_email_and_content(
    assignment_configuration,
    learner_emails,
    content_key,
):
    """
    Returns a queryset of all ``LearnerContentAssignment`` records
    in the given assignment configuration for the provided list
    of learner_emails and the given content_key.
    """
    return get_assignments_for_configuration(
        assignment_configuration,
        learner_email__in=learner_emails,
        content_key=content_key,
    )


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
        always be an integer <= 0.

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
    if content_price_cents > 0:
        raise AllocationException('Allocation price must be <= 0')

    # Fetch any existing assignments for all pairs of (learner, content) in this assignment config.
    existing_assignments = get_assignments_by_learner_email_and_content(
        assignment_configuration,
        learner_emails,
        content_key,
    )

    # Existing Assignments in consideration by state
    already_allocated_or_accepted = []
    cancelled_or_errored_to_update = []

    # Maintain a set of emails with existing records - we know we don't have to create
    # new assignments for these.
    learner_emails_with_existing_assignments = set()

    # Split up the existing assignment records by state
    for assignment in existing_assignments:
        learner_emails_with_existing_assignments.add(assignment.learner_email)
        if assignment.state in LearnerContentAssignmentStateChoices.REALLOCATE_STATES:
            assignment.content_quantity = content_price_cents
            assignment.state = LearnerContentAssignmentStateChoices.ALLOCATED
            cancelled_or_errored_to_update.append(assignment)
        else:
            already_allocated_or_accepted.append(assignment)

    # Bulk update and get a list of refreshed objects
    updated_assignments = _update_and_refresh_assignments(
        cancelled_or_errored_to_update,
        ['content_quantity', 'state']
    )

    # Narrow down creation list of learner emails
    learner_emails_for_assignment_creation = set(learner_emails) - learner_emails_with_existing_assignments

    # Initialize and save LearnerContentAssignment instances for each of them
    created_assignments = _create_new_assignments(
        assignment_configuration,
        learner_emails_for_assignment_creation,
        content_key,
        content_price_cents,
    )

    # Return a mapping of the action we took to lists of relevant assignment records.
    return {
        'updated': updated_assignments,
        'created': created_assignments,
        'no_change': already_allocated_or_accepted,
    }


def _update_and_refresh_assignments(assignment_records, fields_changed):
    """
    Helper to bulk save the given assignment_records
    and refresh their state from the DB.
    """
    # Save the assignments to update
    LearnerContentAssignment.bulk_update(assignment_records, fields_changed)

    # Get a list of refreshed objects that we just updated
    return LearnerContentAssignment.objects.filter(
        uuid__in=[record.uuid for record in assignment_records],
    )


def _create_new_assignments(assignment_configuration, learner_emails, content_key, content_price_cents):
    """
    Helper to bulk save new LearnerContentAssignment instances.
    """
    assignments_to_create = [
        LearnerContentAssignment(
            assignment_configuration=assignment_configuration,
            learner_email=learner_email,
            content_key=content_key,
            content_quantity=content_price_cents,
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
        )
        for learner_email in learner_emails
    ]

    # Do the bulk creation to save these records
    return LearnerContentAssignment.bulk_create(assignments_to_create)
