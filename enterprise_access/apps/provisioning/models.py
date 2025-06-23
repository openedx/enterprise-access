"""
Workflow models for the customer-and-subsidy-provisioning business domain.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from attrs import define, field, validators
from django.conf import settings
from django_countries import countries

from enterprise_access.apps.workflow.exceptions import UnitOfWorkException
from enterprise_access.apps.workflow.models import AbstractWorkflow, AbstractWorkflowStep
from enterprise_access.apps.workflow.serialization import BaseInputOutput

from .api import (
    get_or_create_customer_agreement,
    get_or_create_enterprise_admin_users,
    get_or_create_enterprise_catalog,
    get_or_create_enterprise_customer,
    get_or_create_subscription_plan
)
from .utils import attrs_validate_email, is_bool, is_datetime, is_int, is_list_of_type, is_str, is_uuid


@define
class GetCreateCustomerStepInput(BaseInputOutput):
    """
    The input object to be used for the business logic of get-or-creating
    an EnterpriseCustomer.
    """
    KEY = 'create_customer_input'

    name: str = field(validator=is_str)
    slug: str = field(validator=is_str)
    country: str = field(validator=validators.in_(dict(countries)))


@define
class GetCreateCustomerStepOutput(BaseInputOutput):
    """
    The output object that stores the result of get-or-creating
    an EnterpriseCustomer.
    """
    KEY = 'create_customer_output'

    uuid: UUID = field(validator=is_uuid)
    name: str = field(validator=is_str)
    slug: str = field(validator=is_str)
    country: str = field(validator=validators.in_(dict(countries)))


class CreateCustomerStepException(UnitOfWorkException):
    """
    Exception raised when an EnterpriseCustomer could not be created or fetched.
    """


class GetCreateCustomerStep(AbstractWorkflowStep):
    """
    Workflow step for creating a new customer, or returning an existing record
    based on matching title value.
    """
    exception_class = CreateCustomerStepException
    input_class = GetCreateCustomerStepInput
    output_class = GetCreateCustomerStepOutput

    def process_input(self, accumulated_output=None, **kwargs):
        """
        Gets or creates an enterprise customer record.

        Params:
          accumulated_output (obj): An optional accumulator object to which
            the resulting output can be added.

        Returns:
          An instance of ``self.output_class``.
        """
        input_object = self.input_object
        result_dict = get_or_create_enterprise_customer(
            name=input_object.name,
            slug=input_object.slug,
            country=input_object.country,
        )
        return self.output_class.from_dict(result_dict)

    def get_workflow_record(self):
        return ProvisionNewCustomerWorkflow.objects.filter(
            uuid=self.workflow_record_uuid,
        ).first()

    def get_preceding_step_record(self):
        return None


@define
class GetCreateEnterpriseAdminUsersInput(BaseInputOutput):
    """
    The input object to be used for the fetching or creating
    of enterprise admin users.
    """
    KEY = 'create_enterprise_admin_users_input'

    # enterprise_customer_uuid: UUID = field(validator=is_uuid)
    user_emails: list[str] = field(
        validator=is_list_of_type(str, extra_member_validators=[attrs_validate_email])
    )


@define
class UserEmailRecord:
    """
    An object that stores the email address of a user.
    """
    user_email: str = field(validator=is_str)


@define
class GetCreateEnterpriseAdminUsersOutput(BaseInputOutput):
    """
    The output object that stores the result of fetching or creating
    enterprise admin users.
    """
    KEY = 'create_enterprise_admin_users_output'

    enterprise_customer_uuid: UUID = field(validator=is_uuid)
    created_admins: list[UserEmailRecord]
    existing_admins: list[UserEmailRecord]


class CreateAdminsStepException(UnitOfWorkException):
    """
    Exception raised when enterprise admin user records could not be created or fetched.
    """


class GetCreateEnterpriseAdminUsersStep(AbstractWorkflowStep):
    """
    Workflow step for fetching or creating enterprise admin users.
    """
    exception_class = CreateAdminsStepException
    input_class = GetCreateEnterpriseAdminUsersInput
    output_class = GetCreateEnterpriseAdminUsersOutput

    def process_input(self, accumulated_output=None, **kwargs):
        """
        Gets or creates enterprise admin users.
        """
        input_object = self.input_object
        customer_uuid_str = str(accumulated_output.create_customer_output.uuid)
        result_dict = get_or_create_enterprise_admin_users(
            enterprise_customer_uuid=customer_uuid_str,
            user_emails=input_object.user_emails,
        )
        result_dict['enterprise_customer_uuid'] = customer_uuid_str
        return self.output_class.from_dict(result_dict)

    def get_workflow_record(self):
        return ProvisionNewCustomerWorkflow.objects.filter(
            uuid=self.workflow_record_uuid,
        ).first()

    def get_preceding_step_record(self):
        return GetCreateCustomerStep.objects.filter(uuid=self.preceding_step_uuid).first()


@define
class GetCreateCatalogStepInput(BaseInputOutput):
    """
    The input object to be used for the business logic of get-or-creating
    an Enterprise Catalog.
    """
    KEY = 'create_catalog_input'

    title: Optional[str] = field(default=None, validator=validators.optional(is_str))
    catalog_query_id: Optional[int] = field(default=None, validator=validators.optional(is_int))


@define
class GetCreateCatalogStepOutput(BaseInputOutput):
    """
    The output object that stores the result of get-or-creating
    an EnterpriseCustomer.
    """
    KEY = 'create_catalog_output'

    uuid: UUID = field(validator=is_uuid)
    enterprise_customer_uuid: UUID = field(validator=is_uuid)
    title: str = field(validator=is_str)
    catalog_query_id: int = field(validator=is_int)


class CreateCatalogStepException(UnitOfWorkException):
    """
    Exception raised when an Enterprise Catalog could not be created or fetched.
    """


class GetCreateCatalogStep(AbstractWorkflowStep):
    """
    Workflow step for creating a new catalog, or returning an existing record
    based on matching (customer, catalog query id).
    """
    exception_class = CreateCatalogStepException
    input_class = GetCreateCatalogStepInput
    output_class = GetCreateCatalogStepOutput

    def process_input(self, accumulated_output=None, **kwargs):
        """
        Gets or creates an enterprise catalog record.

        Params:
          accumulated_output (obj): An optional accumulator object in which
            the resulting output is persisted (this action is performed by the containing workflow).

        Returns:
          An instance of ``self.output_class``.
        """
        customer_uuid_str = str(accumulated_output.create_customer_output.uuid)
        customer_name = accumulated_output.create_customer_output.name

        workflow_input = self.get_workflow_record().input_object

        result_dict = get_or_create_enterprise_catalog(
            enterprise_customer_uuid=customer_uuid_str,
            catalog_title=self._get_catalog_title(workflow_input, customer_name),
            catalog_query_id=self._get_catalog_query_id(workflow_input),
        )

        result_dict['enterprise_customer_uuid'] = result_dict['enterprise_customer']
        result_dict['catalog_query_id'] = result_dict['enterprise_catalog_query']
        return self.output_class.from_dict(result_dict)

    def _get_catalog_title(self, workflow_input, customer_name):
        """
        Generate a title if not provided in workflow input.
        """
        if (title_input := self.input_object.title):
            return title_input

        subsidy_type = getattr(workflow_input.create_subscription_plan_input, 'SUBSIDY_TYPE', '')
        if subsidy_type:
            subsidy_type = ' ' + subsidy_type
        return f"{customer_name}{subsidy_type} Catalog"

    def _get_catalog_query_id(self, workflow_input):
        """
        If not provided in the workflow input, helps infer the catalog_query_id
        based on subscription plan product id.
        """
        # Determine catalog_query_id
        if (catalog_query_id_input := self.input_object.catalog_query_id):
            return catalog_query_id_input

        # Need to get product_id from subscription plan input to infer catalog_query_id
        product_id = str(workflow_input.create_subscription_plan_input.product_id)

        if product_id and product_id in settings.PRODUCT_ID_TO_CATALOG_QUERY_ID_MAPPING:
            return settings.PRODUCT_ID_TO_CATALOG_QUERY_ID_MAPPING[product_id]
        else:
            raise CreateCatalogStepException(
                f"Cannot infer catalog_query_id: product_id {product_id} "
                f"not found in mapping: {settings.PRODUCT_ID_TO_CATALOG_QUERY_ID_MAPPING}"
            )

    def get_workflow_record(self):
        return ProvisionNewCustomerWorkflow.objects.filter(
            uuid=self.workflow_record_uuid,
        ).first()

    def get_preceding_step_record(self):
        return GetCreateEnterpriseAdminUsersStep.objects.filter(
            uuid=self.preceding_step_uuid,
        ).first()


class CreateCustomerAgreementStepException(UnitOfWorkException):
    """
    Exception raised when a Customer Agreement could not be created or fetched.
    """


@define
class GetCreateCustomerAgreementStepInput(BaseInputOutput):
    """
    The input object to be used for the business logic of get-or-creating
    a Customer Agreement
    """
    KEY = 'create_customer_agreement_input'

    default_catalog_uuid: Optional[UUID] = field(default=None, validator=validators.optional(is_uuid))


@define
class GetCreateCustomerAgreementStepOutput(BaseInputOutput):
    """
    The output object used for the business logic of get-or-creating
    a Customer Agreement.
    """
    KEY = 'create_customer_agreement_output'

    uuid: UUID = field(validator=is_uuid)
    enterprise_customer_uuid: UUID = field(validator=is_uuid)
    subscriptions: list[dict] = field(default=[])
    default_catalog_uuid: UUID = field(default=None, validator=validators.optional(is_uuid))


class GetCreateCustomerAgreementStep(AbstractWorkflowStep):
    """
    Workflow step for creating a new Customer Agreement, or returning an existing record
    based on matching customer uuid.
    """
    exception_class = CreateCustomerAgreementStepException
    input_class = GetCreateCustomerAgreementStepInput
    output_class = GetCreateCustomerAgreementStepOutput

    def process_input(self, accumulated_output=None, **kwargs):
        """
        Gets or creates a Customer Agreement record.

        Params:
          accumulated_output (obj): An optional accumulator object in which
            the resulting output is persisted (this action is performed by the containing workflow).

        Returns:
          An instance of ``self.output_class``.
        """
        customer_uuid_str = str(accumulated_output.create_customer_output.uuid)
        customer_slug = accumulated_output.create_customer_output.slug
        default_catalog_uuid = self.input_object.default_catalog_uuid

        result_dict = get_or_create_customer_agreement(
            enterprise_customer_uuid=customer_uuid_str,
            customer_slug=customer_slug,
            default_catalog_uuid=str(default_catalog_uuid) if default_catalog_uuid else None,
        )
        return self.output_class.from_dict(result_dict)

    def get_workflow_record(self):
        return ProvisionNewCustomerWorkflow.objects.filter(
            uuid=self.workflow_record_uuid,
        ).first()

    def get_preceding_step_record(self):
        return GetCreateCatalogStep.objects.filter(
            uuid=self.preceding_step_uuid,
        ).first()


class GetCreateSubscriptionPlanException(UnitOfWorkException):
    """
    Exception raised when a Subscription Plan could not be fetched or created.
    """


@define
class GetCreateSubscriptionPlanStepInput(BaseInputOutput):
    """
    The input object to be used for the business logic of get-or-creating
    a Subscription Plan
    """
    KEY = 'create_subscription_plan_input'
    SUBSIDY_TYPE = 'Subscription'

    title: str = field(validator=is_str)
    salesforce_opportunity_line_item: str = field(validator=is_str)
    start_date: datetime = field(validator=is_datetime)
    expiration_date: datetime = field(validator=is_datetime)
    desired_num_licenses: int = field(validator=is_int)
    product_id: int = field(validator=is_int)
    enterprise_catalog_uuid: UUID = field(default=None, validator=validators.optional(is_uuid))


@define
class GetCreateSubscriptionPlanStepOutput(BaseInputOutput):
    """
    The output object to be used for the business logic of get-or-creating
    a Subscription Plan
    """
    KEY = 'create_subscription_plan_output'

    uuid: UUID = field(validator=is_uuid)
    title: str = field(validator=is_str)
    salesforce_opportunity_line_item: str = field(validator=is_str)
    created: datetime = field(validator=is_datetime)
    start_date: datetime = field(validator=is_datetime)
    expiration_date: datetime = field(validator=is_datetime)
    is_active: bool = field(validator=is_bool)
    is_current: bool = field(validator=is_bool)
    plan_type: str = field(validator=is_str)
    enterprise_catalog_uuid: UUID = field(validator=is_uuid)


class GetCreateSubscriptionPlanStep(AbstractWorkflowStep):
    """
    Workflow step for creating a new Subscription Plan, or returning an existing record
    based on matching customer agreement uuid and opportunity_line_item.
    """
    exception_class = GetCreateSubscriptionPlanException
    input_class = GetCreateSubscriptionPlanStepInput
    output_class = GetCreateSubscriptionPlanStepOutput

    def process_input(self, accumulated_output=None, **kwargs):
        """
        Gets or creates a Subscription Plan record.

        Params:
          accumulated_output (obj): An optional accumulator object in which
            the resulting output is persisted (this action is performed by the containing workflow).

        Returns:
          An instance of ``self.output_class``.
        """
        if self.input_object.enterprise_catalog_uuid:
            catalog_uuid = str(self.input_object.enterprise_catalog_uuid)
        else:
            catalog_uuid = str(accumulated_output.create_catalog_output.uuid)

        result_dict = get_or_create_subscription_plan(
            customer_agreement_uuid=str(accumulated_output.create_customer_agreement_output.uuid),
            existing_subscription_list=accumulated_output.create_customer_agreement_output.subscriptions,
            plan_title=self.input_object.title,
            catalog_uuid=catalog_uuid,
            opp_line_item=self.input_object.salesforce_opportunity_line_item,
            start_date=self.input_object.start_date.isoformat(),
            expiration_date=self.input_object.expiration_date.isoformat(),
            desired_num_licenses=self.input_object.desired_num_licenses,
            product_id=self.input_object.product_id,
        )
        return self.output_class.from_dict(result_dict)

    def get_workflow_record(self):
        return ProvisionNewCustomerWorkflow.objects.filter(
            uuid=self.workflow_record_uuid,
        ).first()

    def get_preceding_step_record(self):
        return GetCreateCustomerAgreementStep.objects.filter(
            uuid=self.preceding_step_uuid,
        ).first()


class ProvisionNewCustomerWorkflow(AbstractWorkflow):
    """
    A workflow for get/creating an EnterpriseCustomer and fetching/creating
    admin users associated with that customer record.
    """
    steps = [
        GetCreateCustomerStep,
        GetCreateEnterpriseAdminUsersStep,
        GetCreateCatalogStep,
        GetCreateCustomerAgreementStep,
        GetCreateSubscriptionPlanStep,
    ]

    @classmethod
    def generate_input_dict(
        cls, customer_request_dict, admin_email_list, catalog_request_dict,
        customer_agreement_request_dict, subscription_plan_request_dict
    ):
        """
        Generates a dictionary to use as ``input_data`` for instances of this workflow.
        """
        return {
            GetCreateCustomerStepInput.KEY: customer_request_dict,
            GetCreateEnterpriseAdminUsersInput.KEY: {
                'user_emails': admin_email_list,
            },
            GetCreateCatalogStepInput.KEY: catalog_request_dict or {},
            GetCreateCustomerAgreementStepInput.KEY: customer_agreement_request_dict or {},
            GetCreateSubscriptionPlanStepInput.KEY: subscription_plan_request_dict,
        }

    def get_create_customer_step(self):
        return GetCreateCustomerStep.objects.filter(
            workflow_record_uuid=self.uuid,
        ).first()

    def get_create_enterprise_admin_users_step(self):
        return GetCreateEnterpriseAdminUsersStep.objects.filter(
            workflow_record_uuid=self.uuid,
        ).first()

    def get_create_catalog_step(self):
        return GetCreateCatalogStep.objects.filter(
            workflow_record_uuid=self.uuid,
        ).first()

    def get_create_customer_agreement_step(self):
        return GetCreateCustomerAgreementStep.objects.filter(
            workflow_record_uuid=self.uuid,
        ).first()

    def get_create_subscription_plan_step(self):
        return GetCreateSubscriptionPlanStep.objects.filter(
            workflow_record_uuid=self.uuid,
        ).first()

    def customer_output_dict(self):
        return self.output_data[GetCreateCustomerStepOutput.KEY]

    def admin_users_output_dict(self):
        return self.output_data[GetCreateEnterpriseAdminUsersOutput.KEY]

    def catalog_output_dict(self):
        return self.output_data[GetCreateCatalogStepOutput.KEY]

    def customer_agreement_output_dict(self):
        return self.output_data[GetCreateCustomerAgreementStepOutput.KEY]

    def subscription_plan_output_dict(self):
        return self.output_data[GetCreateSubscriptionPlanStepOutput.KEY]


class TriggerProvisionSubscriptionTrialCustomerWorkflow(ProvisionNewCustomerWorkflow):
    """
    A proxy model for ProvisionNewCustomerWorkflow, used specifically to provide
    an admin interface for triggering new subscription trial provisioning workflows.
    """
    class Meta:
        proxy = True
        verbose_name = "Trigger Subscription Trial Provisioning Workflow"
        verbose_name_plural = "Trigger Subscription Trial Provisioning Workflows"
