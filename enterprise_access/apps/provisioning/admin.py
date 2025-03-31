""" Admin configuration for provisioning. """

from django.contrib import admin
from django.urls import reverse
from django.utils.safestring import mark_safe
from djangoql.admin import DjangoQLSearchMixin

from enterprise_access.apps.provisioning import models


@admin.register(models.ProvisionNewCustomerWorkflow)
class ProvisionNewCustomerWorkflowAdmin(DjangoQLSearchMixin, admin.ModelAdmin):
    """
    Admin class for the customer provisioning workflow.
    """
    djangoql_completion_enabled_by_default = False

    list_display = (
        'uuid',
        'created',
        'succeeded_at',
        'failed_at',
    )
    ordering = ['-created']
    search_fields = (
        'uuid',
        'input_data',
        'output_data',
    )
    readonly_fields = (
        'created',
        'modified',
        'create_customer_step_link',
        'create_admin_users_step_link',
    )

    def create_customer_step_link(self, obj):
        step_record = obj.get_create_customer_step()
        if not step_record:
            return None
        return mark_safe('<a href="{}">{}</a>'.format(
            reverse("admin:provisioning_getcreatecustomerstep_change", args=(step_record.pk,)),
            step_record.pk,
        ))
    create_customer_step_link.short_description = 'Create Customer Step'

    def create_admin_users_step_link(self, obj):
        step_record = obj.get_create_enterprise_admin_users_step()
        if not step_record:
            return None
        return mark_safe('<a href="{}">{}</a>'.format(
            reverse("admin:provisioning_getcreateenterpriseadminusersstep_change", args=(step_record.pk,)),
            step_record.pk,
        ))
    create_admin_users_step_link.short_description = 'Create Admin Users Step'


class ProvisionWorkflowStepAdminBase(admin.ModelAdmin):
    """
    Base class for provisioning step admin classes.
    """
    djangoql_completion_enabled_by_default = False

    fields = (
        'input_data',
        'output_data',
        'succeeded_at',
        'failed_at',
        'exception_message',
        'created',
        'modified',
        'workflow_record_link',
    )
    readonly_fields = (
        'created',
        'modified',
        'workflow_record_link',
    )

    def workflow_record_link(self, obj):
        workflow_record = obj.get_workflow_record()
        if not workflow_record:
            return None
        return mark_safe('<a href="{}">{}</a>'.format(
            reverse("admin:provisioning_provisionnewcustomerworkflow_change", args=(workflow_record.pk,)),
            workflow_record.pk,
        ))
    workflow_record_link.short_description = 'Workflow Record'


@admin.register(models.GetCreateCustomerStep)
class GetCreateCustomerStepAdmin(DjangoQLSearchMixin, ProvisionWorkflowStepAdminBase):
    """
    Admin model for the customer creation step.
    """


@admin.register(models.GetCreateEnterpriseAdminUsersStep)
class GetCreateEnterpriseAdminUsersStep(DjangoQLSearchMixin, ProvisionWorkflowStepAdminBase):
    """
    Admin model for the admin user creation step.
    """
    fields = ProvisionWorkflowStepAdminBase.fields + ('preceding_step_link',)
    readonly_fields = ProvisionWorkflowStepAdminBase.readonly_fields + ('preceding_step_link',)

    def preceding_step_link(self, obj):
        step_record = obj.get_preceding_step_record()
        if not step_record:
            return None
        return mark_safe('<a href="{}">{}</a>'.format(
            reverse("admin:provisioning_getcreatecustomerstep_change", args=(step_record.pk,)),
            step_record.pk,
        ))
    preceding_step_link.short_description = 'Preceding customer creation step'
