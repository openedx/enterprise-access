"""
Python API for customer_billing app.
"""
import logging
from collections.abc import Mapping
from typing import TypedDict, Unpack, cast

import stripe
from django.conf import settings  # type: ignore

from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.customer_billing.constants import CHECKOUT_SESSION_ERROR_CODES

stripe.api_key = settings.STRIPE_API_KEY
logger = logging.getLogger(__name__)


class CheckoutSessionInputValidatorData(TypedDict, total=False):
    admin_email: str
    enterprise_slug: str
    quantity: int
    stripe_price_id: str


class CheckoutSessionInputData(TypedDict, total=True):
    admin_email: str
    enterprise_slug: str
    quantity: int
    stripe_price_id: str


class FieldValidationResult(TypedDict):
    error_code: str | None
    system_message: str | None


def _get_lms_user_id(user_email: str) -> int:
    """
    TODO: add caching?
    """
    return 1

class CheckoutSessionInputValidator():
    """
    Modeled after RegistrationValidationView:
    https://github.com/openedx/edx-platform/blob/f90e59e5/openedx/core/djangoapps/user_authn/views/register.py#L727
    """

    def handle_admin_email(self, input_data: CheckoutSessionInputValidatorData) -> FieldValidationResult:
        """
        Ensure the provided email is registered.
        """
        # TODO
        admin_email = input_data.get('admin_email')
        return {'error_code': None, 'system_message': admin_email}

    def handle_enterprise_slug(self, input_data: CheckoutSessionInputValidatorData) -> FieldValidationResult:
        """
        Ensure the provided customer slug does not conflict with existing customers.

        +-----------+-----------------+--------+
        |   Slug    | Email Is Admin  |  Slug  |
        | Conflict? | For Matching EC | Valid? |
        +-----------+-----------------+--------+
        |         0 |             N/A |      1 |
        |         1 |               0 |      0 |
        |         1 |               1 |      1 |
        +-----------+-----------------+--------+

        NOTE: This rough approach is not vetted by product, just an initial
        approach that isn't too crazy and can be refined later.
        """
        admin_email = input_data.get('admin_email')
        enterprise_slug = input_data.get('enterprise_slug')

        # Fetch any existing customers with the same slug.
        lms_client = LmsApiClient()
        existing_customer_for_slug = lms_client.get_enterprise_customer_data(
            enterprise_customer_slug=enterprise_slug
        )
        admin_emails_for_existing_customer = [
            admin['email']
            for admin in existing_customer_for_slug.get('admin_users', [])
        ]
        email_is_admin_for_customer = admin_email in admin_emails_for_existing_customer

        if existing_customer_for_slug:
            if email_is_admin_for_customer:
                error_code = CHECKOUT_SESSION_ERROR_CODES['enterprise_slug']['EXISTING_ENTERPRISE_CUSTOMER_FOR_ADMIN']
                system_message = f'Slug invalid: Admin belongs to existing customer found for slug "{enterprise_slug}".'
                logger.info(system_message)
            else:
                error_code = CHECKOUT_SESSION_ERROR_CODES['enterprise_slug']['EXISTING_ENTERPRISE_CUSTOMER']
                system_message = f'Slug invalid: Existing customer found for slug "{enterprise_slug}".'
                logger.warning(system_message)
        else:
            error_code = None
            system_message = f'Slug valid: No existing customer found for slug "{enterprise_slug}".'
            logger.info(system_message)

        return {'error_code': error_code, 'system_message': system_message}

    def handle_quantity(self, input_data: CheckoutSessionInputValidatorData) -> FieldValidationResult:
        """
        Validate the `quantity` field.
        """
        # TODO
        quantity = input_data.get('quantity')
        return {'error_code': None, 'system_message': f'{quantity}'}

    def handle_stripe_price_id(self, input_data: CheckoutSessionInputValidatorData) -> FieldValidationResult:
        """
        Validate the `stripe_price_id` field.
        """
        # TODO
        stripe_price_id = input_data.get('stripe_price_id')
        return {'error_code': None, 'system_message': stripe_price_id}

    validation_handlers = {
        'admin_email': handle_admin_email,
        'enterprise_slug': handle_enterprise_slug,
        'quantity': handle_quantity,
        'stripe_price_id': handle_stripe_price_id,
    }

    def validate(self, input_data: CheckoutSessionInputValidatorData) -> Mapping[str, FieldValidationResult]:
        """
        Run all relevant validation handlers against input data.
        """
        validation_decisions = {
            field_name: self.validation_handlers[field_name](self, input_data)
            for field_name in input_data.keys()
        }
        return validation_decisions


def validate_free_trial_checkout_session(
    **input_data: Unpack[CheckoutSessionInputValidatorData],
) -> Mapping[str, FieldValidationResult]:
    """
    Validate parameters used for creating a free trial checkout session.

    Returns:
        A dict mapping fields which failed validation to a dict representing the validation error.
        If no fields failed validation, the resulting dict will be empty.
    """
    validator = CheckoutSessionInputValidator()
    return validator.validate(input_data=input_data)


class CreateCheckoutSessionValidationError(Exception):
    def __init__(self, validation_errors_by_field=None):
        super().__init__()
        self.validation_errors_by_field = validation_errors_by_field


def create_free_trial_checkout_session(
    **input_data: Unpack[CheckoutSessionInputData],
) -> stripe.checkout.Session:
    """
    Create a Stripe "Checkout Session" for a free trial subscription plan.
    """
    validator = CheckoutSessionInputValidator()
    validation_errors = validator.validate(input_data=cast(CheckoutSessionInputValidatorData, input_data))
    if validation_errors:
        raise CreateCheckoutSessionValidationError(validation_errors_by_field=validation_errors)

    lms_user_id = _get_lms_user_id(input_data['admin_email'])

    create_kwargs: stripe.checkout.Session.CreateParams = {
        'mode': 'subscription',
        # Intended UI will be a custom react component.
        'ui_mode': 'custom',
        # This normally wouldn't work because the customer doesn't exist yet --- I'd propose we modify the admin
        # portal to support an empty state with a message like "turning cogs, check back later." if there's no
        # Enterprise Customer but there is a Stripe Customer.
        'return_url': f"https://portal.edx.org/{input_data['enterprise_slug']}",
        'line_items': [{
            'price': input_data['stripe_price_id'],
            'quantity': input_data['quantity'],
        }],
        # Defer payment collection until the last moment, then cancel
        # the subscription if payment info has not been submitted.
        'subscription_data': {
            'trial_period_days': settings.SSP_TRIAL_PERIOD_DAYS,
            'trial_settings': {
                # Just in case the admin removes the paymet method via their stripe billing portal, cancel the trial
                # after it ends.
                'end_behavior': {'missing_payment_method': 'cancel'},
            },
            'metadata': {
                # Communicate to downstream services what the admin intends the enterprise customer slug to be.
                'enterprise_customer_slug': input_data['enterprise_slug'],
                # Store the lms_user_id for improved debugging experience.
                'lms_user_id': lms_user_id,
            }
        },
        # Always collect payment method, not just when the amount is greater than zero.  This is influential for
        # creating a free trial plan because the amount is always zero.
        'payment_method_collection': 'always',
    }
    # Eagerly find an existing Stripe customer if one already exists with the same email, otherwise excluding it from
    # the request will cause Stripe to generate a new one.
    #
    # NOTE: The Stripe "customer" is more closely representative of the human admin than the "enterprise customer".
    stripe_customer_search_result = stripe.Customer.search(query=f"email: '{input_data['admin_email']}'")
    found_stripe_customer_by_email = (
        stripe_customer_search_result.data[0] if stripe_customer_search_result.data else None
    )
    if found_stripe_customer_by_email:
        create_kwargs['customer'] = found_stripe_customer_by_email['id']

    # Finally, call the stripe API endpoint to create a checkout session.
    return stripe.checkout.Session.create(**create_kwargs)
