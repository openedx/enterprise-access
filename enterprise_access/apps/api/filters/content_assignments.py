"""
API Filters for resources defined in the ``assignment_policy`` app.
"""
from django_filters import CharFilter

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
    learner_state = CharFilter(field_name='learner_state', lookup_expr='exact')
    learner_state__in = CharInFilter(field_name='learner_state', lookup_expr='in')

    class Meta:
        model = LearnerContentAssignment
        fields = {
            'content_key': ['exact', 'in'],
            'learner_email': ['exact', 'in'],
            'lms_user_id': ['exact', 'in'],
            'state': ['exact', 'in'],
        }
