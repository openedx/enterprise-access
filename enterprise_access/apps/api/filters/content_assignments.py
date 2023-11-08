"""
API Filters for resources defined in the ``assignment_policy`` app.
"""
from ...content_assignments.models import AssignmentConfiguration, LearnerContentAssignment
from .base import CharInFilter, HelpfulFilterSet


class AssignmentConfigurationFilter(HelpfulFilterSet):
    """
    Base filter for AssignmentConfiguration views.
    """
    class Meta:
        model = AssignmentConfiguration
        fields = ['active']


class LearnerContentAssignmentAdminFilter(HelpfulFilterSet):
    """
    Base filter for LearnerContentAssignment views.
    """
    learner_state = CharInFilter(field_name='learner_state', lookup_expr='in')

    class Meta:
        model = LearnerContentAssignment
        fields = [
            'content_key',
            'learner_email',
            'lms_user_id',
            'state',
            'learner_state',
        ]
