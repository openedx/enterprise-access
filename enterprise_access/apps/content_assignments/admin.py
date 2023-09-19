""" Admin configuration for content_assignment models. """

from django.contrib import admin
from djangoql.admin import DjangoQLSearchMixin
from simple_history.admin import SimpleHistoryAdmin

from enterprise_access.apps.content_assignments import models


@admin.register(models.AssignmentConfiguration)
class AssignmentConfigurationAdmin(DjangoQLSearchMixin, SimpleHistoryAdmin):
    """
    Admin configuration for AssignmentConfigurations.
    """
    list_display = (
        'enterprise_customer_uuid',
        'uuid',
        'active',
        'modified',
    )
    search_fields = (
        'uuid',
        'enterprise_customer_uuid',
    )
    list_filter = ('active',)
    ordering = ['-modified']
    read_only_fields = (
        'created',
        'modified',
    )


@admin.register(models.LearnerContentAssignment)
class LearnerContentAssignment(DjangoQLSearchMixin, SimpleHistoryAdmin):
    """
    Admin configuration for LearnerContentAssignments.
    """
    list_display = (
        'assignment_configuration',
        'learner_email',
        'lms_user_id',
        'content_key',
        'state',
        'content_quantity',
        'modified',
    )
    ordering = ['-modified']
    search_fields = (
        'uuid',
        'learner_email',
        'lms_user_id',
    )
    list_filter = ('state',)
    read_only_fields = (
        'last_notification_at',
        'created',
        'modified',
        'lms_user_id',
        'assignment_configuration',
    )
