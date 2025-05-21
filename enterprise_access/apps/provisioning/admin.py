""" Admin configuration for provisioning. """

from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from djangoql.admin import DjangoQLSearchMixin

from enterprise_access.apps.provisioning import forms
from enterprise_access.apps.provisioning import models


MAX_FORM_ADMINS = 5


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
        'create_catalog_step_link',
        'create_customer_agreement_step_link',
        'create_subscription_plan_step_link',
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

    def create_catalog_step_link(self, obj):
        step_record = obj.get_create_catalog_step()
        if not step_record:
            return None
        return mark_safe('<a href="{}">{}</a>'.format(
            reverse("admin:provisioning_getcreatecatalogstep_change", args=(step_record.pk,)),
            step_record.pk,
        ))
    create_catalog_step_link.short_description = 'Create Catalog Step'

    def create_customer_agreement_step_link(self, obj):
        step_record = obj.get_create_customer_agreement_step()
        if not step_record:
            return None
        return mark_safe('<a href="{}">{}</a>'.format(
            reverse("admin:provisioning_getcreatecustomeragreementstep_change", args=(step_record.pk,)),
            step_record.pk,
        ))
    create_customer_agreement_step_link.short_description = 'Create Customer Agreement Step'

    def create_subscription_plan_step_link(self, obj):
        step_record = obj.get_create_subscription_plan_step()
        if not step_record:
            return None
        return mark_safe('<a href="{}">{}</a>'.format(
            reverse("admin:provisioning_getcreatesubscriptionplanstep_change", args=(step_record.pk,)),
            step_record.pk,
        ))
    create_subscription_plan_step_link.short_description = 'Create Subscription Plan Step'


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
class GetCreateEnterpriseAdminUsersStepAdmin(DjangoQLSearchMixin, ProvisionWorkflowStepAdminBase):
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


@admin.register(models.GetCreateCatalogStep)
class GetCreateCatalogStepAdmin(DjangoQLSearchMixin, ProvisionWorkflowStepAdminBase):
    """
    Admin model for the catalog creation step.
    """
    fields = ProvisionWorkflowStepAdminBase.fields + ('preceding_step_link',)
    readonly_fields = ProvisionWorkflowStepAdminBase.readonly_fields + ('preceding_step_link',)

    def preceding_step_link(self, obj):
        step_record = obj.get_preceding_step_record()
        if not step_record:
            return None
        return mark_safe('<a href="{}">{}</a>'.format(
            reverse("admin:provisioning_getcreateenterpriseadminusersstep_change", args=(step_record.pk,)),
            step_record.pk,
        ))
    preceding_step_link.short_description = 'Preceding admin users creation step'


@admin.register(models.GetCreateCustomerAgreementStep)
class GetCreateCustomerAgreementStepAdmin(DjangoQLSearchMixin, ProvisionWorkflowStepAdminBase):
    """
    Admin model for the customer agreement creation step.
    """
    fields = ProvisionWorkflowStepAdminBase.fields + ('preceding_step_link',)
    readonly_fields = ProvisionWorkflowStepAdminBase.readonly_fields + ('preceding_step_link',)

    def preceding_step_link(self, obj):
        step_record = obj.get_preceding_step_record()
        if not step_record:
            return None
        return mark_safe('<a href="{}">{}</a>'.format(
            reverse("admin:provisioning_getcreatecatalogstep_change", args=(step_record.pk,)),
            step_record.pk,
        ))
    preceding_step_link.short_description = 'Preceding catalog creation step'


@admin.register(models.GetCreateSubscriptionPlanStep)
class GetCreateSubscriptionPlanStepAdmin(DjangoQLSearchMixin, ProvisionWorkflowStepAdminBase):
    """
    Admin model for the subscription plan creation step.
    """
    fields = ProvisionWorkflowStepAdminBase.fields + ('preceding_step_link',)
    readonly_fields = ProvisionWorkflowStepAdminBase.readonly_fields + ('preceding_step_link',)

    def preceding_step_link(self, obj):
        step_record = obj.get_preceding_step_record()
        if not step_record:
            return None
        return mark_safe('<a href="{}">{}</a>'.format(
            reverse("admin:provisioning_getcreatecustomeragreementstep_change", args=(step_record.pk,)),
            step_record.pk,
        ))
    preceding_step_link.short_description = 'Preceding customer agreement creation step'


@admin.register(models.AdminTriggerProvisionNewCustomerWorkflow)
class AdminTriggerProvisioningWorkflowAdmin(admin.ModelAdmin):
    """
    This admin is primarily for the "add" action, which corresponds to
    the ProvisionNewCustomerWorkflowAdminForm form.
    It won't display a list of proxy instances as they don't really exist.
    """

    def has_module_permission(self, request):
        """ Ensure this admin section is visible """
        return True

    def has_view_permission(self, request, obj=None):
        """ Allow viewing the add form. """
        return True

    def has_add_permission(self, request):
        """ We need to allow adding for our form submission. """
        return True

    def has_change_permission(self, request, obj=None):
        """ Proxy instances aren't really changed in this context. """
        return False

    def has_delete_permission(self, request, obj=None):
        """ Proxy instances aren't deleted. """
        return False

    def changelist_view(self, request, extra_context=None):
        """ Redirect to the add view, as the changelist for this proxy is not meaningful. """
        add_url = reverse(
            f'admin:{self.model._meta.app_label}_{self.model._meta.model_name}_add',
        )
        return HttpResponseRedirect(add_url)

    def _process_form(self, form, request):
        cleaned_data = form.clean()

        input_data_for_workflow = {}
        customer_step_input_data = {
            'name': cleaned_data.get('customer_name'),
            'slug': cleaned_data.get('customer_slug'),
            'country': cleaned_data.get('customer_country')
        }
        admin_users_step_input_data = [
            cleaned_data.get(f'admin_email_{i}')
            for i in range(MAX_FORM_ADMINS)
            if cleaned_data.get(f'admin_email_{i}')
        ]
        catalog_step_input = {
            'title': cleaned_data.get('catalog_title'),
            'catalog_query_id': cleaned_data.get('catalog_query_id'),
        }
        agreement_step_input = {
            'default_catalog_uuid': cleaned_data.get('agreement_default_catalog_uuid'),
        }
        plan_step_input = {
            'title': cleaned_data.get('plan_title'),
            'salesforce_opportunity_line_item': cleaned_data.get('plan_salesforce_opportunity_line_item'),
            'start_date': cleaned_data.get('plan_start_date'),
            'expiration_date': cleaned_data.get('plan_expiration_date'),
            'desired_num_licenses': cleaned_data.get('plan_desired_num_licenses'),
            'product_id': cleaned_data.get('plan_product_id'),
            'enterprise_catalog_uuid': cleaned_data.get('plan_enterprise_catalog_uuid'),
        }

        workflow_instance = None
        try:
            # Note: We are creating the base model instance here
            workflow_input_dict = models.ProvisionNewCustomerWorkflow.generate_input_dict(
                customer_step_input_data,
                admin_users_step_input_data,
                catalog_step_input,
                agreement_step_input,
                plan_step_input,
            )
            workflow_instance = models.ProvisionNewCustomerWorkflow.objects.create(
                input_data=workflow_input_dict,
            )

            workflow_instance.execute()

            if workflow_instance.succeeded_at:
                msg = _(f"Successfully triggered and completed workflow: {workflow_instance.uuid}")
                self.message_user(request, msg, messages.SUCCESS)
            elif workflow_instance.failed_at:
                msg = _(
                    f"Workflow triggered but failed: {workflow_instance.uuid}. "
                    f"Error: {workflow_instance.exception_message}"
                )
                self.message_user(request, msg, messages.ERROR)
            else:
                msg = _(
                    f"Workflow triggered: {workflow_instance.uuid}. Status uncertain."
                )
                self.message_user(request, msg, messages.WARNING)

        except Exception as e:
            self.message_user(request, _(f"Error triggering workflow: {str(e)}"), messages.ERROR)
            if workflow_instance and workflow_instance.pk and not workflow_instance.failed_at:
                workflow_instance.failed_at = timezone.now()
                workflow_instance.exception_message = f"Admin trigger exception: {str(e)}"
                workflow_instance.save(update_fields=['failed_at', 'exception_message', 'modified'])

        return workflow_instance


    def add_view(self, request, form_url='', extra_context=None):
        """
        Overrides the default add_view to present our custom form for triggering
        a ProvisionNewCustomerWorkflow.
        """
        if request.method == 'POST':
            form = forms.ProvisionNewCustomerWorkflowAdminForm(request.POST)

            if form.is_valid():
                workflow_instance = self._process_form(form, request)
                # Redirect to the actual workflow instance's admin page
                url = reverse(
                    "admin:provisioning_provisionnewcustomerworkflow_change",
                    args=[workflow_instance.pk]
                )
                return HttpResponseRedirect(url)
            else:
                return HttpResponseRedirect(request.get_full_path())
        else:
            form = forms.ProvisionNewCustomerWorkflowAdminForm()

        context = {
            **self.admin_site.each_context(request),
            'title': _('Trigger New Customer Provisioning Workflow'),
            'form': form,
            'opts': self.model._meta, # opts for AdminTriggerProvisioningWorkflow
            'app_label': self.model._meta.app_label,
        }
        return render(request, 'admin/provisioning/trigger_provisioning_form_proxy.html', context)
