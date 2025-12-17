"""
Workflow models for the customer-and-subsidy-provisioning business domain.
"""
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from attrs import define, field, validators
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django_countries import countries

from enterprise_access.apps.customer_billing.constants import CheckoutIntentState
from enterprise_access.apps.customer_billing.models import (
    CheckoutIntent,
    SelfServiceSubscriptionRenewal,
    StripeEventSummary
)
from enterprise_access.apps.customer_billing.tasks import send_enterprise_provision_signup_confirmation_email
from enterprise_access.apps.workflow.exceptions import UnitOfWorkException
from enterprise_access.apps.workflow.models import AbstractWorkflow, AbstractWorkflowStep
from enterprise_access.apps.workflow.serialization import BaseInputOutput

from .api import (
    get_or_create_customer_agreement,
    get_or_create_enterprise_admin_users,
    get_or_create_enterprise_catalog,
    get_or_create_enterprise_customer,
    get_or_create_subscription_plan,
    get_or_create_subscription_plan_renewal
)
from .utils import attrs_validate_email, is_bool, is_datetime, is_int, is_list_of_type, is_str, is_uuid

logger = logging.getLogger(__name__)

# Business rule: For non-trial plans, default the subscription duration to 1 year from the start
# date if no expiration_date was specified under `create_first_paid_subscription_plan_input`.
FIRST_PAID_SUBSCRIPTION_PERIOD_DURATION_FALLBACK = relativedelta(years=1)


class CheckoutIntentStepMixin:
    """
    Centralized logic for interfacing with CheckoutIntent records from workflow steps.
    """

    def get_workflow_record(self) -> 'ProvisionNewCustomerWorkflow':
        """
        Implemented by base class.
        """
        raise NotImplementedError()

    def get_fulfillable_checkout_intent_via_slug(self) -> CheckoutIntent:
        """
        Helper to get the checkout intent (related via the enterprise customer slug).
        """
        workflow = self.get_workflow_record()
        enterprise_slug = workflow.input_object.create_customer_input.slug
        checkout_intent = CheckoutIntent.filter_by_name_and_slug(
            slug=enterprise_slug,
        ).filter(
            state__in=CheckoutIntent.FULFILLABLE_STATES(),
        ).first()
        if not checkout_intent:
            raise CheckoutIntent.DoesNotExist('No fulfillable CheckoutIntent records for the given slug were found.')
        return checkout_intent

    def get_linked_checkout_intent(self) -> CheckoutIntent:
        """
        Helper to get the linked checkout intent (depends on link_checkout_intent() having been called).

        Raises:
            - CheckoutIntent.DoesNotExist: If there is no linked checkout intent.
        """
        workflow = self.get_workflow_record()
        checkout_intent = CheckoutIntent.objects.get(workflow=workflow)
        return checkout_intent

    def link_checkout_intent(self, enterprise_customer_uuid: UUID) -> None:
        """
        Links the parent workflow to the related CheckoutIntent, if any.
        """
        workflow = self.get_workflow_record()
        checkout_intent = self.get_fulfillable_checkout_intent_via_slug()
        checkout_intent.workflow = workflow
        checkout_intent.enterprise_uuid = enterprise_customer_uuid
        checkout_intent.save(update_fields=['workflow', 'enterprise_uuid'])

    def error_checkout_intent(self, exc: Exception) -> None:
        """
        Set the checkout intent to an errored state.

        Raises:
            - CheckoutIntent.DoesNotExist: If there is no linked checkout intent.
        """
        checkout_intent = self.get_linked_checkout_intent()
        checkout_intent.mark_provisioning_error(str(exc))

    def fulfill_checkout_intent(self) -> None:
        """
        Set the checkout intent to a fulfilled state.

        Raises:
            - CheckoutIntent.DoesNotExist: If there is no linked checkout intent.
        """
        checkout_intent = self.get_linked_checkout_intent()
        checkout_intent.mark_as_fulfilled()


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


class GetCreateCustomerStep(CheckoutIntentStepMixin, AbstractWorkflowStep):
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
        self.link_checkout_intent(result_dict['uuid'])
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

        subsidy_type = getattr(workflow_input.create_trial_subscription_plan_input, 'SUBSIDY_TYPE', '')
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
        product_id = str(workflow_input.create_trial_subscription_plan_input.product_id)

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


class GetCreateTrialSubscriptionPlanException(UnitOfWorkException):
    """
    Exception raised when a Subscription Plan could not be fetched or created.
    """


@define
class GetCreateTrialSubscriptionPlanStepInput(BaseInputOutput):
    """
    The input object to be used for the business logic of get-or-creating
    a Subscription Plan
    """
    KEY = 'create_trial_subscription_plan_input'
    SUBSIDY_TYPE = 'Subscription'

    title: str = field(validator=is_str)
    salesforce_opportunity_line_item: str = field(validator=is_str)
    start_date: datetime = field(validator=is_datetime)
    expiration_date: datetime = field(validator=is_datetime)
    desired_num_licenses: int = field(validator=is_int)
    product_id: int = field(validator=is_int)
    enterprise_catalog_uuid: Optional[UUID] = field(default=None, validator=validators.optional(is_uuid))


@define
class GetCreateTrialSubscriptionPlanStepOutput(BaseInputOutput):
    """
    The output object to be used for the business logic of get-or-creating
    a Subscription Plan
    """
    KEY = 'create_trial_subscription_plan_output'

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
    product: Optional[int] = field(default=None, validator=validators.optional(is_int))


class GetCreateTrialSubscriptionPlanStep(CheckoutIntentStepMixin, AbstractWorkflowStep):
    """
    Workflow step for creating a new Subscription Plan, or returning an existing record
    based on matching customer agreement uuid and opportunity_line_item.
    """
    exception_class = GetCreateTrialSubscriptionPlanException
    input_class = GetCreateTrialSubscriptionPlanStepInput
    output_class = GetCreateTrialSubscriptionPlanStepOutput

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

        customer_agreement_uuid = str(accumulated_output.create_customer_agreement_output.uuid)

        try:
            result_dict = get_or_create_subscription_plan(
                customer_agreement_uuid=customer_agreement_uuid,
                existing_subscription_list=accumulated_output.create_customer_agreement_output.subscriptions,
                plan_title=self.input_object.title,
                catalog_uuid=catalog_uuid,
                opp_line_item=self.input_object.salesforce_opportunity_line_item,
                start_date=self.input_object.start_date.isoformat(),
                expiration_date=self.input_object.expiration_date.isoformat(),
                desired_num_licenses=self.input_object.desired_num_licenses,
                product_id=self.input_object.product_id,
            )
        except Exception as exc:
            try:
                self.error_checkout_intent(exc=exc)
            except CheckoutIntent.DoesNotExist:
                logger.exception(
                    "Could not error CheckoutIntent because no linked CheckoutIntent found for this workflow."
                )
            raise self.exception_class(
                f'Failed to get/create subscription plan for customer agreement uuid {customer_agreement_uuid}'
            ) from exc

        return self.output_class.from_dict(result_dict)

    def get_workflow_record(self) -> 'ProvisionNewCustomerWorkflow':
        return ProvisionNewCustomerWorkflow.objects.filter(
            uuid=self.workflow_record_uuid,
        ).first()

    def get_preceding_step_record(self):
        return GetCreateCustomerAgreementStep.objects.filter(
            uuid=self.preceding_step_uuid,
        ).first()


class GetCreateFirstPaidSubscriptionPlanException(UnitOfWorkException):
    """
    Exception raised when a Subscription Plan (first paid) could not be fetched or created.
    """


@define
class GetCreateFirstPaidSubscriptionPlanStepInput(BaseInputOutput):
    """
    The input object to be used for the business logic of get-or-creating
    a Subscription Plan
    """
    KEY = 'create_first_paid_subscription_plan_input'
    SUBSIDY_TYPE = 'Subscription'

    title: str = field(validator=is_str)
    product_id: int = field(validator=is_int)
    start_date: Optional[datetime] = field(default=None, validator=validators.optional(is_datetime))
    expiration_date: Optional[datetime] = field(default=None, validator=validators.optional(is_datetime))
    salesforce_opportunity_line_item: Optional[str] = field(default=None, validator=validators.optional(is_str))
    enterprise_catalog_uuid: Optional[UUID] = field(default=None, validator=validators.optional(is_uuid))


@define
class GetCreateFirstPaidSubscriptionPlanStepOutput(BaseInputOutput):
    """
    The output object to be used for the business logic of get-or-creating
    a Subscription Plan
    """
    KEY = 'create_first_paid_subscription_plan_output'

    uuid: UUID = field(validator=is_uuid)
    title: str = field(validator=is_str)
    created: datetime = field(validator=is_datetime)
    start_date: datetime = field(validator=is_datetime)
    expiration_date: datetime = field(validator=is_datetime)
    is_active: bool = field(validator=is_bool)
    is_current: bool = field(validator=is_bool)
    plan_type: str = field(validator=is_str)
    enterprise_catalog_uuid: UUID = field(validator=is_uuid)
    salesforce_opportunity_line_item: Optional[str] = field(default=None, validator=validators.optional(is_str))
    product: Optional[int] = field(default=None, validator=validators.optional(is_int))


class GetCreateFirstPaidSubscriptionPlanStep(CheckoutIntentStepMixin, AbstractWorkflowStep):
    """
    Workflow step for creating a new Subscription Plan (first paid), or returning an existing
    record based on matching customer agreement uuid and opportunity_line_item.
    """
    exception_class = GetCreateFirstPaidSubscriptionPlanException
    input_class = GetCreateFirstPaidSubscriptionPlanStepInput
    output_class = GetCreateFirstPaidSubscriptionPlanStepOutput

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

        customer_agreement_uuid = str(accumulated_output.create_customer_agreement_output.uuid)

        if self.input_object.start_date:
            start_date = self.input_object.start_date
        else:
            start_date = accumulated_output.create_trial_subscription_plan_output.expiration_date

        if self.input_object.expiration_date:
            expiration_date = self.input_object.expiration_date
        else:
            expiration_date = start_date + FIRST_PAID_SUBSCRIPTION_PERIOD_DURATION_FALLBACK

        # Inherit the num licenses from the trial plan.
        workflow = self.get_workflow_record()
        desired_num_licenses = workflow.input_object.create_trial_subscription_plan_input.desired_num_licenses

        try:
            result_dict = get_or_create_subscription_plan(
                customer_agreement_uuid=customer_agreement_uuid,
                existing_subscription_list=accumulated_output.create_customer_agreement_output.subscriptions,
                plan_title=self.input_object.title,
                catalog_uuid=catalog_uuid,
                desired_num_licenses=desired_num_licenses,
                opp_line_item=self.input_object.salesforce_opportunity_line_item,
                start_date=start_date.isoformat(),
                expiration_date=expiration_date.isoformat(),
                product_id=self.input_object.product_id,
            )
        except Exception as exc:
            try:
                self.error_checkout_intent(exc=exc)
            except CheckoutIntent.DoesNotExist:
                logger.exception(
                    "Could not error CheckoutIntent because no linked CheckoutIntent found for this workflow."
                )
            raise self.exception_class(
                f'Failed to get/create subscription plan for customer agreement uuid {customer_agreement_uuid}'
            ) from exc

        return self.output_class.from_dict(result_dict)

    def get_workflow_record(self):
        return ProvisionNewCustomerWorkflow.objects.filter(
            uuid=self.workflow_record_uuid,
        ).first()

    def get_preceding_step_record(self):
        return GetCreateTrialSubscriptionPlanStep.objects.filter(
            uuid=self.preceding_step_uuid,
        ).first()


class GetCreateSubscriptionPlanRenewalStepException(UnitOfWorkException):
    """
    Exception raised when a Subscription Plan Renewal could not be fetched or created.
    """


@define
class GetCreateSubscriptionPlanRenewalStepInput(BaseInputOutput):
    """
    The input object to be used for the business logic of get-or-creating
    a Subscription Plan
    """
    KEY = 'create_subscription_plan_renewal_input'


@define
class GetCreateSubscriptionPlanRenewalStepOutput(BaseInputOutput):
    """
    The output object to be used for the business logic of get-or-creating
    a Subscription Plan
    """
    KEY = 'create_subscription_plan_renewal_output'

    id: int = field(validator=is_int)
    prior_subscription_plan: UUID = field(validator=is_uuid)
    renewed_subscription_plan: UUID = field(validator=is_uuid)
    number_of_licenses: int = field(validator=is_int)
    effective_date: datetime = field(validator=is_datetime)
    renewed_expiration_date: datetime = field(validator=is_datetime)
    salesforce_opportunity_line_item_id: Optional[str] = field(default=None, validator=validators.optional(is_str))


class GetCreateSubscriptionPlanRenewalStep(CheckoutIntentStepMixin, AbstractWorkflowStep):
    """
    Workflow step for creating a new Subscription Plan renewal, or returning an existing
    renewal record based on matching customer agreement UUID and opportunity_line_item.
    """
    exception_class = GetCreateSubscriptionPlanRenewalStepException
    input_class = GetCreateSubscriptionPlanRenewalStepInput
    output_class = GetCreateSubscriptionPlanRenewalStepOutput

    def process_input(self, accumulated_output=None, **kwargs):
        """
        Gets or creates a SubscriptionPlanRenewal record.

        The renewal links the trial plan to the first paid plan, using the trial plan's
        expiration date as the effective date for the renewal.

        Params:
          accumulated_output (obj): An optional accumulator object in which
            the resulting output is persisted (this action is performed by the containing workflow).

        Returns:
          An instance of ``self.output_class``.
        """
        trial_plan_uuid = str(accumulated_output.create_trial_subscription_plan_output.uuid)
        first_paid_plan_uuid = str(accumulated_output.create_first_paid_subscription_plan_output.uuid)

        workflow = self.get_workflow_record()
        desired_num_licenses = workflow.input_object.create_trial_subscription_plan_input.desired_num_licenses

        try:
            result_dict = get_or_create_subscription_plan_renewal(
                prior_subscription_plan_uuid=trial_plan_uuid,
                renewed_subscription_plan_uuid=first_paid_plan_uuid,
                # salesforce_opportunity_id is intentionally None and will be populated outside of this workflow.
                salesforce_opportunity_line_item_id=None,
                effective_date=accumulated_output.create_trial_subscription_plan_output.expiration_date.isoformat(),
                renewed_expiration_date=(
                    accumulated_output.create_first_paid_subscription_plan_output.expiration_date.isoformat()
                ),
                # All licenses should be transferred.
                number_of_licenses=desired_num_licenses,
            )
            logger.info(
                'Provisioning: created/found subscription plan renewal with id %s linking plan %s to renewed plan %s',
                result_dict.get('id'),
                result_dict.get('prior_subscription_plan'),
                result_dict.get('renewed_subscription_plan'),
            )
        except Exception as exc:
            try:
                self.error_checkout_intent(exc=exc)
            except CheckoutIntent.DoesNotExist:
                logger.exception(
                    "Could not error CheckoutIntent because no linked CheckoutIntent found for this workflow."
                )
            raise self.exception_class(
                f'Failed to get/create subscription plan renewal from trial plan {trial_plan_uuid} '
                f'to paid plan {first_paid_plan_uuid}'
            ) from exc

        # Create SelfServiceSubscriptionRenewal record to track this renewal
        try:
            checkout_intent = self.get_linked_checkout_intent()
            latest_summary = StripeEventSummary.get_latest_for_checkout_intent(
                checkout_intent,
                stripe_subscription_id__isnull=False,
            )
            if not latest_summary:
                raise self.exception_class(f'No summary for {checkout_intent}')

            renewal_tracking_record, created = SelfServiceSubscriptionRenewal.objects.update_or_create(
                checkout_intent=checkout_intent,
                subscription_plan_renewal_id=result_dict['id'],
                defaults={
                    'stripe_subscription_id': latest_summary.stripe_subscription_id,
                    'stripe_event_data': latest_summary.stripe_event_data,
                    'prior_subscription_plan_uuid': result_dict.get('prior_subscription_plan'),
                    'renewed_subscription_plan_uuid': result_dict.get('renewed_subscription_plan'),
                }
            )
        except Exception as exc:
            logger.exception(
                'Failed to create SelfServiceSubscriptionRenewal tracking record for renewal %s: %s',
                result_dict.get('id'), exc
            )
            raise

        logger.info(
            'SelfServiceSubscriptionRenewal record %s for renewal %s, was created=%s',
            renewal_tracking_record.id, result_dict['id'], created,
        )
        return self.output_class.from_dict(result_dict)

    def get_workflow_record(self):
        return ProvisionNewCustomerWorkflow.objects.filter(
            uuid=self.workflow_record_uuid,
        ).first()

    def get_preceding_step_record(self):
        return GetCreateFirstPaidSubscriptionPlanStep.objects.filter(
            uuid=self.preceding_step_uuid,
        ).first()


class NotificationStepException(UnitOfWorkException):
    """
    Exception raised if there was an issue with NotificationStep.
    """


@define
class NotificationStepInput(BaseInputOutput):
    """
    Empty input object.
    """
    KEY = 'notification_input'


@define
class NotificationStepOutput(BaseInputOutput):
    """
    Empty output object.
    """
    KEY = 'notification_output'


class NotificationStep(CheckoutIntentStepMixin, AbstractWorkflowStep):
    """
    Workflow step for marking the CheckoutIntent as fulfilled and notifying the customer admin.
    """
    exception_class = NotificationStepException
    input_class = NotificationStepInput
    output_class = NotificationStepOutput

    def process_input(self, accumulated_output=None, **kwargs):
        """
        Mark the CheckoutIntent as fulfilled and notify the customer admin.

        Params:
          accumulated_output (obj): An optional accumulator object in which
            the resulting output is persisted (this action is performed by the containing workflow).

        Returns:
          An instance of ``self.output_class``.
        """
        # Mark the checkout intent as fulfilled.
        try:
            self.fulfill_checkout_intent()
        except CheckoutIntent.DoesNotExist as exc:
            raise self.exception_class("Unexpectedly, no linked CheckoutIntent found for this workflow step.") from exc

        workflow = self.get_workflow_record()
        desired_num_licenses = workflow.input_object.create_trial_subscription_plan_input.desired_num_licenses

        # Notify the customer admin via email.
        send_enterprise_provision_signup_confirmation_email.delay(
            # The email campaign will be specifically designed around the trial plan parameters.
            accumulated_output.create_trial_subscription_plan_output.start_date,
            accumulated_output.create_trial_subscription_plan_output.expiration_date,
            desired_num_licenses,
            # Remaining campaign params.
            accumulated_output.create_customer_output.name,
            accumulated_output.create_customer_output.slug
        )

        # TODO: Is there a better way than to just send an empty dict?
        return self.output_class.from_dict({})

    def get_workflow_record(self):
        return ProvisionNewCustomerWorkflow.objects.filter(
            uuid=self.workflow_record_uuid,
        ).first()

    def get_preceding_step_record(self):
        return GetCreateSubscriptionPlanRenewalStep.objects.filter(
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
        GetCreateTrialSubscriptionPlanStep,
        GetCreateFirstPaidSubscriptionPlanStep,
        GetCreateSubscriptionPlanRenewalStep,
        NotificationStep,
    ]

    @classmethod
    def generate_input_dict(
        cls,
        customer_request_dict,
        admin_email_list,
        catalog_request_dict,
        customer_agreement_request_dict,
        trial_subscription_plan_request_dict,
        first_paid_subscription_plan_request_dict,
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
            GetCreateTrialSubscriptionPlanStepInput.KEY: trial_subscription_plan_request_dict,
            GetCreateFirstPaidSubscriptionPlanStepInput.KEY: first_paid_subscription_plan_request_dict,
            GetCreateSubscriptionPlanRenewalStepInput.KEY: {},
            NotificationStepInput.KEY: {},
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

    def get_create_trial_subscription_plan_step(self):
        return GetCreateTrialSubscriptionPlanStep.objects.filter(
            workflow_record_uuid=self.uuid,
        ).first()

    def get_create_first_paid_subscription_plan_step(self):
        return GetCreateFirstPaidSubscriptionPlanStep.objects.filter(
            workflow_record_uuid=self.uuid,
        ).first()

    def get_create_subscription_plan_renewal_step(self):
        return GetCreateSubscriptionPlanRenewalStep.objects.filter(
            workflow_record_uuid=self.uuid,
        ).first()

    def get_create_notification_step(self):
        return NotificationStep.objects.filter(
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

    def trial_subscription_plan_output_dict(self):
        return self.output_data[GetCreateTrialSubscriptionPlanStepOutput.KEY]

    def first_paid_subscription_plan_output_dict(self):
        return self.output_data[GetCreateFirstPaidSubscriptionPlanStepOutput.KEY]

    def subscription_plan_renewal_output_dict(self):
        return self.output_data[GetCreateSubscriptionPlanRenewalStepOutput.KEY]

    def notification_output_dict(self):
        return self.output_data[NotificationStepOutput.KEY]


class TriggerProvisionSubscriptionTrialCustomerWorkflow(ProvisionNewCustomerWorkflow):
    """
    A proxy model for ProvisionNewCustomerWorkflow, used specifically to provide
    an admin interface for triggering new subscription trial provisioning workflows.
    """
    class Meta:
        proxy = True
        verbose_name = "Trigger Subscription Trial Provisioning Workflow"
        verbose_name_plural = "Trigger Subscription Trial Provisioning Workflows"
