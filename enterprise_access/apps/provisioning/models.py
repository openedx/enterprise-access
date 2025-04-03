"""
Workflow models for the customer-and-subsidy-provisioning business domain.
"""
from uuid import UUID

from attrs import define, field, validators
from django_countries import countries

from enterprise_access.apps.workflow.exceptions import UnitOfWorkException
from enterprise_access.apps.workflow.models import AbstractWorkflow, AbstractWorkflowStep
from enterprise_access.apps.workflow.serialization import BaseInputOutput

from .api import get_or_create_enterprise_admin_users, get_or_create_enterprise_customer


@define
class GetCreateCustomerStepInput(BaseInputOutput):
    """
    The input object to be used for the business logic of get-or-creating
    an EnterpriseCustomer.
    """
    KEY = 'create_customer_input'

    name: str = field(validator=validators.instance_of(str))
    slug: str = field(validator=validators.instance_of(str))
    country: str = field(validator=validators.in_(dict(countries)))


@define
class GetCreateCustomerStepOutput(BaseInputOutput):
    """
    The output object that stores the result of get-or-creating
    an EnterpriseCustomer.
    """
    KEY = 'create_customer_output'

    uuid: UUID = field(validator=validators.instance_of(UUID))
    name: str = field(validator=validators.instance_of(str))
    slug: str = field(validator=validators.instance_of(str))
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

    # enterprise_customer_uuid: UUID = field(validator=validators.instance_of(UUID))
    user_emails: list[str] = field(validator=validators.instance_of(list))


@define
class UserEmailRecord:
    """
    An object that stores the email address of a user.
    """
    user_email: str = field(validator=validators.instance_of(str))


@define
class GetCreateEnterpriseAdminUsersOutput(BaseInputOutput):
    """
    The output object that stores the result of fetching or creating
    enterprise admin users.
    """
    KEY = 'create_enterprise_admin_users_output'

    enterprise_customer_uuid: UUID = field(validator=validators.instance_of(UUID))
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


class ProvisionNewCustomerWorkflow(AbstractWorkflow):
    """
    A workflow for get/creating an EnterpriseCustomer and fetching/creating
    admin users associated with that customer record.
    """
    steps = [
        GetCreateCustomerStep,
        GetCreateEnterpriseAdminUsersStep,
    ]

    @classmethod
    def generate_input_dict(cls, customer_request_dict, admin_email_list):
        """
        Generates a dictionary to use as ``input_data`` for instances of this workflow.
        """
        return {
            GetCreateCustomerStepInput.KEY: customer_request_dict,
            GetCreateEnterpriseAdminUsersInput.KEY: {
                'user_emails': admin_email_list,
            }
        }

    def get_create_customer_step(self):
        return GetCreateCustomerStep.objects.filter(
            workflow_record_uuid=self.uuid,
        ).first()

    def get_create_enterprise_admin_users_step(self):
        return GetCreateEnterpriseAdminUsersStep.objects.filter(
            workflow_record_uuid=self.uuid,
        ).first()

    def customer_output_dict(self):
        return self.output_data[GetCreateCustomerStepOutput.KEY]

    def admin_users_output_dict(self):
        return self.output_data[GetCreateEnterpriseAdminUsersOutput.KEY]
