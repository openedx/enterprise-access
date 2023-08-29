"""
Primary Python API for interacting with Assignment
records and business logic.
"""
from django.db.models import Sum

from .constants import LearnerContentAssignmentStateChoices
from .models import LearnerContentAssignment


def get_assignments_for_policy(
    subsidy_access_policy,
    state=LearnerContentAssignmentStateChoices.ALLOCATED,
):
    """
    Returns a queryset of all ``LearnerContentAssignment`` records
    for the given policy, optionally filtered to only those
    associated with the given ``learner_emails``.
    """
    queryset = LearnerContentAssignment.objects.select_related(
        'assignment_policy',
        'assignment_policy__subsidy_access_policy',
    ).filter(
        assignment_policy__subsidy_access_policy=subsidy_access_policy,
        state=state,
    )
    return queryset


def get_allocated_quantity_for_policy(subsidy_access_policy):
    """
    Returns a float representing the total quantity, in USD cents, currently allocated
    via Assignments for the given policy.
    """
    assignments_queryset = get_assignments_for_policy(subsidy_access_policy)
    aggregate = assignments_queryset.aggregate(
        total_quantity=Sum('content_quantity'),
    )
    return aggregate['total_quantity']
