"""
Admin for workflows app.
"""
import json
from django.utils.safestring import mark_safe
from django import forms
from django.contrib import admin
from django.forms.models import inlineformset_factory
from django.http.request import HttpRequest
from django.urls import reverse
from django.utils.html import format_html
from djangoql.admin import DjangoQLSearchMixin
from ordered_model.admin import OrderedInlineModelAdminMixin, OrderedStackedInline

from enterprise_access.apps.workflows import models


class WorkflowExecutionStatusInline(admin.TabularInline):
    """Inline admin class for the WorkflowExecutionStatus model."""
    model = models.WorkflowExecutionStatus
    extra = 0
    fields = ('user', 'status', 'current_step', 'created', 'modified', 'admin_link')
    readonly_fields = fields
    ordering = ('-modified',)

    def has_add_permission(self, request, obj=None):  # pylint: disable=unused-argument
        return False

    def has_delete_permission(self, request, obj=None, workflow_execution_status=None):  # pylint: disable=unused-argument
        return False

    def admin_link(self, instance):
        """
        Return a link to the admin change page for the instance.
        """
        url = instance.admin_change_url
        return format_html('<a href="{}">View</a>', url)


@admin.register(models.WorkflowExecutionStatus)
class WorkflowExecutionStatusAdmin(admin.ModelAdmin):
    """Admin class for the WorkflowExecutionStatus model."""
    list_display = (
        'uuid',
        'workflow_definition',
        'status',
        'user',
        # 'current_step',
        'created',
        'modified',
    )
    fields = (
        'uuid',
        'workflow_definition',
        'status',
        'user',
        # 'formatted_current_step',
        # 'executed_steps_links',
        # 'remaining_steps_links',
        'created',
        'modified',
    )
    readonly_fields = fields
    search_fields = ('uuid', 'workflow_definition__name', 'user__username', 'user__email', 'user__lms_user_id')
    list_filter = ('workflow_definition', 'status')

    # def formatted_current_step(self, obj):
    #     """
    #     Display the current step as a hyperlink to the admin change page.
    #     """
    #     current_step = obj.current_step
    #     if not current_step:
    #         return "-"

    #     url = reverse('admin:workflows_workflowgroupactionstepthrough_change', args=[current_step.id])
    #     return format_html(f'<a href="{url}">{current_step}</a>')
    # formatted_current_step.short_description = "Current step"

    # def executed_steps_links(self, obj):
    #     """
    #     Display executed steps as hyperlinks to the admin change page.
    #     """
    #     executed_steps = obj.executed_steps.order_by('modified')

    #     if not executed_steps:
    #         return "-"

    #     links = []
    #     for step_status in executed_steps:
    #         url = reverse('admin:workflows_workflowexecutionstepstatus_change', args=[step_status.id])
    #         links.append(f'<a href="{url}">{step_status}</a><br />')
    #     return format_html("".join(links))
    # executed_steps_links.short_description = "Executed steps"

    # def remaining_steps_links(self, obj):
    #     """
    #     Display remaining steps as hyperlinks in the admin list view.
    #     """
    #     remaining_steps = obj.remaining_workflow_steps

    #     if not remaining_steps:
    #         return "-"

    #     links = []
    #     for step in remaining_steps:
    #         url = reverse('admin:workflows_workflowactionstepthrough_change', args=[step.id])
    #         links.append(f'<a href="{url}">{step}</a><br />')
    #     return format_html("".join(links))
    # remaining_steps_links.short_description = "Remaining steps"


class WorkflowGroupActionStepThroughInline(OrderedStackedInline):
    """Inline admin class for the WorkflowGroupActionStepThrough model."""
    model = models.WorkflowGroupActionStepThrough
    fk_name = 'step_group'
    fields = ('step', 'group', 'move_up_down_links',)
    readonly_fields = ('move_up_down_links',)
    extra = 0
    ordering = ('order',)
    verbose_name_plural = "Steps or groups in this group "


class WorkflowItemThroughInline(OrderedStackedInline):
    """Inline admin class for managing WorkflowItemThrough in WorkflowDefinition."""
    model = models.WorkflowItemThrough
    fields = ('action_step', 'step_group', 'move_up_down_links',)
    readonly_fields = ('move_up_down_links',)
    extra = 0
    ordering = ('order',)
    verbose_name_plural = "Workflow Items (Steps or Groups)"


@admin.register(models.WorkflowItemThrough)
class WorkflowItemThroughAdmin(DjangoQLSearchMixin, admin.ModelAdmin):
    list_display = ('id', 'workflow_definition', 'action_step', 'step_group')
    search_fields = ('workflow_definition__name',)
    list_filter = ('workflow_definition',)


@admin.register(models.WorkflowDefinition)
class WorkflowDefinitionAdmin(DjangoQLSearchMixin, OrderedInlineModelAdminMixin, admin.ModelAdmin):
    """Admin class for the WorkflowDefinition model."""
    list_display = ('name', 'is_active', 'is_default')
    search_fields = ('name',)
    list_filter = ('is_active', 'is_default')
    inlines = (WorkflowItemThroughInline, WorkflowExecutionStatusInline,)


@admin.register(models.WorkflowEnterpriseCustomer)
class WorkflowEnterpriseCustomerAdmin(DjangoQLSearchMixin, admin.ModelAdmin):
    """Admin class for the WorkflowEnterpriseCustomer model."""
    list_display = ('workflow_definition', 'enterprise_customer_uuid')
    search_fields = ('enterprise_customer_uuid',)
    list_filter = ('workflow_definition',)


@admin.register(models.WorkflowActionStep)
class WorkflowActionStepAdmin(DjangoQLSearchMixin, admin.ModelAdmin):
    """Admin class for the WorkflowActionStep model."""
    list_display = ('name', 'action_reference')
    search_fields = ('name', 'action_reference')


@admin.register(models.WorkflowGroupActionStepThrough)
class WorkflowGroupActionStepThroughAdmin(DjangoQLSearchMixin, admin.ModelAdmin):
    """Admin class for the WorkflowGroupActionStepThrough model."""
    list_display = ('step', 'order')
    search_fields = ('step__name',)


@admin.register(models.WorkflowExecutionStepStatus)
class WorkflowExecutionStepStatusAdmin(DjangoQLSearchMixin, admin.ModelAdmin):
    """Admin class for the WorkflowExecutionStepStatus model."""
    list_display = ('id', 'workflow_execution', 'step', 'status', 'task_id')
    search_fields = ('id', 'workflow_execution__uuid', 'step__name', 'task_id')
    list_filter = ('status', 'workflow_execution__workflow_definition')
    fields = (
        'id', 'workflow_execution', 'step', 'status', 
        'task_id', 'formatted_result', 'error_message',
        'created', 'modified',
    )
    readonly_fields = fields

    def formatted_result(self, obj):
        """
        Display the result as human-readable, formatted JSON.
        """
        # Format the JSON with indentation for readability
        formatted_json = json.dumps(obj.result, indent=4, ensure_ascii=False)
        # Use `mark_safe` to prevent Django from auto-escaping the HTML
        return mark_safe(f'<pre>{formatted_json}</pre>')
    formatted_result.short_description = "Result"


@admin.register(models.WorkflowStepGroup)
class WorkflowStepGroupAdmin(DjangoQLSearchMixin, OrderedInlineModelAdminMixin, admin.ModelAdmin):
    """Admin class for the WorkflowStepGroup model."""
    list_display = ('name', 'run_in_parallel')
    search_fields = ('name',)
    inlines = [WorkflowGroupActionStepThroughInline]
