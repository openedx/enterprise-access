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
        'uuid',
        'enterprise_customer_uuid',
        'active',
        'modified',
    )
    search_fields = (
        'uuid',
        'enterprise_customer_uuid',
    )
    list_filter = ('active',)
    ordering = ['-modified']
    readonly_fields = (
        'created',
        'modified',
    )


class ActionInline(admin.TabularInline):
    """
    Inline admin for linking actions into their related assignment record.
    """
    model = models.LearnerContentAssignmentAction

    fields = (
        'action_type',
        'completed_at',
        'error_reason',
    )

    ordering = ['-modified']

    show_change_link = True

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request, obj):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('assignment')


@admin.register(models.LearnerContentAssignment)
class LearnerContentAssignmentAdmin(DjangoQLSearchMixin, SimpleHistoryAdmin):
    """
    Admin configuration for LearnerContentAssignments.
    """
    list_display = (
        'uuid',
        'get_assignment_configuration_uuid',
        'get_enterprise_customer_uuid',
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
        'assignment_configuration__uuid',
        'assignment_configuration__enterprise_customer_uuid',
    )
    list_filter = ('state',)
    readonly_fields = (
        'created',
        'modified',
        'lms_user_id',
        'get_enterprise_customer_uuid',
    )
    autocomplete_fields = ['assignment_configuration']

    list_select_related = ('assignment_configuration',)

    inlines = [ActionInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('assignment_configuration')

    @admin.display(ordering='uuid', description='Assignment configuration UUID')
    def get_assignment_configuration_uuid(self, obj):
        return obj.assignment_configuration.uuid

    @admin.display(ordering='enterprise_customer_uuid', description='Enterprise customer uuid')
    def get_enterprise_customer_uuid(self, obj):
        return obj.assignment_configuration.enterprise_customer_uuid


@admin.register(models.LearnerContentAssignmentAction)
class LearnerContentAssignmentActionAdmin(DjangoQLSearchMixin, SimpleHistoryAdmin):
    """
    Admin configuration for LearnerContentAssignmentAction.
    """
    list_display = (
        'uuid',
        'get_assignment',
        'action_type',
        'completed_at',
        'error_reason',
        'modified',
    )
    ordering = ['-modified']
    search_fields = (
        'uuid',
        'assignment__uuid',
        'traceback',
    )
    list_filter = ('action_type', 'error_reason')
    readonly_fields = (
        'created',
        'modified',
        'traceback',
    )
    autocomplete_fields = ['assignment']

    list_select_related = ('assignment',)

    @admin.display(ordering='uuid', description='Assignment UUID')
    def get_assignment(self, obj):
        return obj.assignment.uuid

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'assignment',
        )
