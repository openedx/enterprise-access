"""
Primary Python API for interacting with Assignment
records and business logic.
"""
from django.db.models import Sum

from .constants import LearnerContentAssignmentStateChoices
from .models import LearnerContentAssignment


def get_assignments_for_configuration(
    assignment_configuration,
    state=LearnerContentAssignmentStateChoices.ALLOCATED,
):
    """
    Returns a queryset of all ``LearnerContentAssignment`` records
    for the given assignment configuration.
    """
    queryset = LearnerContentAssignment.objects.select_related(
        'assignment_configuration',
    ).filter(
        assignment_configuration=assignment_configuration,
        state=state,
    )
    return queryset


def get_allocated_quantity_for_configuration(assignment_configuration):
    """
    Returns a float representing the total quantity, in USD cents, currently allocated
    via Assignments for the given configuration.
    """
    assignments_queryset = get_assignments_for_configuration(assignment_configuration)
    aggregate = assignments_queryset.aggregate(
        total_quantity=Sum('content_quantity'),
    )
    return aggregate['total_quantity']
