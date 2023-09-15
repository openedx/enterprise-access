"""
API Filters for resources defined in the ``assignment_policy`` app.
"""
from ...content_assignments.models import AssignmentConfiguration
from .base import HelpfulFilterSet


class AssignmentConfigurationFilter(HelpfulFilterSet):
    """
    Base filter for AssignmentConfiguration views.
    """
    class Meta:
        model = AssignmentConfiguration
        fields = ['active']
