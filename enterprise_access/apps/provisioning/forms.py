"""
Admin form to help trigger manual customer provisioning workflow.
"""
from crispy_forms.helper import FormHelper
from crispy_forms.layout import ButtonHolder, Fieldset, Layout, Submit
from django import forms
from django.conf import settings
from django_countries.fields import CountryField


class ProvisionSubscriptionTrialWorkflowAdminForm(forms.Form):
    """
    A form used to gather manual customer provisioning workflow inputs.
    """
    # Customer inputs
    customer_name = forms.CharField(label="Customer Name", max_length=255, required=True)
    customer_slug = forms.SlugField(label="Customer Slug", required=True)
    customer_country = CountryField(blank_label="Customer Country").formfield()

    # Admin user inputs
    admin_email_1 = forms.EmailField(label="Admin Email", required=True)
    admin_email_2 = forms.EmailField(label="Admin Email 2", required=False)
    admin_email_3 = forms.EmailField(label="Admin Email 3", required=False)
    admin_email_4 = forms.EmailField(label="Admin Email 4", required=False)
    admin_email_5 = forms.EmailField(label="Admin Email 5", required=False)

    # catalog inputs
    catalog_title = forms.CharField(label='Catalog title', max_length=255, required=True)
    catalog_query_id = forms.ChoiceField(
        label='Catalog query',
        required=True,
        choices=settings.PROVISIONING_DEFAULTS['subscription']['trial_catalog_query_choices'],
    )

    # customer agreement input
    agreement_default_catalog_uuid = forms.UUIDField(
        label='Default Catalog UUID for subscriptions w/in this agreement', required=False,
    )

    # subscription plan input
    plan_title = forms.CharField(label='Subscription Plan Title', required=True)
    plan_salesforce_opportunity_line_item = forms.CharField(label='Opp Line Item ID', required=True)
    plan_start_date = forms.DateTimeField(label='Plan Start Date', required=True)
    plan_expiration_date = forms.DateTimeField(label='Plan Expiration Date', required=True)
    plan_product_id = forms.ChoiceField(
        label='Product ID',
        required=True,
        choices=settings.PROVISIONING_DEFAULTS['subscription']['trial_product_choices'],
    )
    plan_desired_num_licenses = forms.IntegerField(label='Number of licenses in plan', required=True)
    plan_enterprise_catalog_uuid = forms.UUIDField(label='Catalog UUID (optional)', required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = FormHelper()
        self.helper.form_id = 'id-provision-new-customer-form'
        self.helper.form_method = 'post'
        self.helper.form_action = ''
        self.helper.layout = Layout(
            Fieldset(
                'Enterprise Customer Details',
                'customer_name',
                'customer_slug',
                'customer_country',
                css_class='module aligned',
            ),
            Fieldset(
                'Admin User Email Addresses',
                'admin_email_1',
                'admin_email_2',
                'admin_email_3',
                'admin_email_4',
                'admin_email_5',
                css_class='module aligned',
            ),
            Fieldset(
                'Catalog Details',
                'catalog_title',
                'catalog_query_id',
                css_class='module aligned',
            ),
            Fieldset(
                'Customer Agreement Details',
                'agreement_default_catalog_uuid',
                css_class='module aligned',
            ),
            Fieldset(
                'Subscription Plan Details',
                'plan_title',
                'plan_salesforce_opportunity_line_item',
                'plan_start_date',
                'plan_expiration_date',
                'plan_product_id',
                'plan_desired_num_licenses',
                'plan_enterprise_catalog_uuid',
                css_class='module aligned',
            ),
            ButtonHolder(
                Submit('submit', 'Submit'),
            ),
        )
