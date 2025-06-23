"""
Python API for customer_billing app.
"""
import logging
from collections.abc import Mapping
from functools import cache
from typing import TypedDict, Unpack, cast

import stripe
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email, validate_slug
from requests.exceptions import HTTPError

from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.customer_billing.constants import CHECKOUT_SESSION_ERROR_CODES

stripe.api_key = settings.STRIPE_API_KEY
logger = logging.getLogger(__name__)


class CheckoutSessionInputValidatorData(TypedDict, total=False):
    """
    `total=False` so that validation does not require all fields to be provided.
    """
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
    developer_message: str | None


# Basic in-memory cache to prevent multiple API calls within a single request.
@cache
def _get_lms_user_id(email: str | None) -> int | None:
    """
    Return the LMS user ID for an existing user with a specific email, or None if no user with that email exists.
    """
    if not email:
        return None
    lms_client = LmsApiClient()
    try:
        user_data = lms_client.get_lms_user_account(email=email)
    except HTTPError:
        return None
    if not user_data:
        return None
    return user_data[0].get('id')


class CheckoutSessionInputValidator():
    """
    Loosely modeled after RegistrationValidationView:
    https://github.com/openedx/edx-platform/blob/f90e59e5/openedx/core/djangoapps/user_authn/views/register.py#L727
    """

    def handle_admin_email(self, input_data: CheckoutSessionInputValidatorData) -> FieldValidationResult:
        """
        Ensure the provided email is registered.
        """
        admin_email = input_data.get('admin_email')

        if not admin_email:
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['admin_email']['INVALID_FORMAT']
            logger.info(f'admin_email invalid. {developer_message}')
            return {'error_code': error_code, 'developer_message': developer_message}

        # Basic email format validation inherited from django core.
        #
        # NOTE: Having both format validation AND registration validation avoids a network call when
        # format validation would fail, and prevents sending garbage via query parameter.
        try:
            validate_email(admin_email)
        except ValidationError:
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['admin_email']['INVALID_FORMAT']
            logger.info(f'admin_email invalid: "{admin_email}". {developer_message}')
            return {'error_code': error_code, 'developer_message': developer_message}

        # Given email must be registered.
        lms_user_id = _get_lms_user_id(email=admin_email)
        if not lms_user_id:
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['admin_email']['NOT_REGISTERED']
            logger.info(f'admin_email invalid: "{admin_email}". {developer_message}')
            return {'error_code': error_code, 'developer_message': developer_message}

        logger.info('admin_email valid and registered.')
        return {'error_code': None, 'developer_message': None}

    def handle_enterprise_slug(self, input_data: CheckoutSessionInputValidatorData) -> FieldValidationResult:
        """
        Ensure the provided customer slug is correctly formatted and does not conflict with existing customers.

        +-----------+-----------------+--------+----------------------------------------+
        |   Slug    | Email Is Admin  |  Slug  |                                        |
        | Conflict? | For Matching EC | Valid? |             Error Code Key             |
        +-----------+-----------------+--------+----------------------------------------+
        |        No |             N/A |    Yes | N/A                                    |
        |       Yes |              No |     No | EXISTING_ENTERPRISE_CUSTOMER           |
        |       Yes |             Yes |     No | EXISTING_ENTERPRISE_CUSTOMER_FOR_ADMIN |
        +-----------+-----------------+--------+----------------------------------------+

        NOTE: This rough approach is not vetted by product, just an initial
        approach that isn't too crazy and can be refined later.
        """
        admin_email = input_data.get('admin_email')
        enterprise_slug = input_data.get('enterprise_slug')

        # We need multiple form fields to validate enterprise_slug.
        if not all([admin_email, enterprise_slug]):
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['common']['INCOMPLETE_DATA']
            logger.info(f'enterprise_slug invalid: {developer_message}')
            return {'error_code': error_code, 'developer_message': developer_message}

        # Basic slug format validation inherited from django core.
        try:
            validate_slug(enterprise_slug)
        except ValidationError:
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['enterprise_slug']['INVALID_FORMAT']
            logger.info(f'enterprise_slug invalid: "{enterprise_slug}". {developer_message}')
            return {'error_code': error_code, 'developer_message': developer_message}

        # Fetch any existing customers with the same slug, and make a distinction
        # if the given email is already an admin of any found customer.
        lms_client = LmsApiClient()
        try:
            existing_customer_for_slug = lms_client.get_enterprise_customer_data(
                enterprise_customer_slug=enterprise_slug
            )
        except HTTPError as exc:
            if exc.response.status_code == 404:
                existing_customer_for_slug = None
            else:
                raise
        if existing_customer_for_slug:
            admin_emails_for_existing_customer = [
                admin['email']
                for admin in existing_customer_for_slug.get('admin_users', [])
            ]
            email_is_admin_for_customer = admin_email in admin_emails_for_existing_customer
            if email_is_admin_for_customer:
                error_code, developer_message = (
                    CHECKOUT_SESSION_ERROR_CODES['enterprise_slug']['EXISTING_ENTERPRISE_CUSTOMER_FOR_ADMIN']
                )
                logger.info(
                    f'enterprise_slug invalid (requested email is admin): "{enterprise_slug}". {developer_message}'
                )
            else:
                error_code, developer_message = (
                    CHECKOUT_SESSION_ERROR_CODES['enterprise_slug']['EXISTING_ENTERPRISE_CUSTOMER']
                )
                logger.info(f'enterprise_slug invalid: "{enterprise_slug}". {developer_message}')
            return {'error_code': error_code, 'developer_message': developer_message}

        logger.info(f'enterprise_slug valid: "{enterprise_slug}". No existing customer found for slug.')
        return {'error_code': None, 'developer_message': None}

    def handle_quantity(self, input_data: CheckoutSessionInputValidatorData) -> FieldValidationResult:
        """
        Validate the `quantity` field.
        """
        quantity = input_data.get('quantity')
        stripe_price_id = input_data.get('stripe_price_id')

        # We need multiple form fields to validate quantity.
        if not all([quantity, stripe_price_id]):
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['common']['INCOMPLETE_DATA']
            return {'error_code': error_code, 'developer_message': developer_message}

        # Positive integers only.
        if not isinstance(quantity, int) or quantity < 1:
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['quantity']['INVALID_FORMAT']
            return {'error_code': error_code, 'developer_message': developer_message}

        # Specific range allowed, e.g. "between 5 and 30 licenses".
        valid_quantity_range_by_stripe_price = {
            product['stripe_price_id']: product['quantity_range']
            for product in settings.SSP_PRODUCTS.values()
        }
        if stripe_price_id not in valid_quantity_range_by_stripe_price:
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['common']['INCOMPLETE_DATA']
            return {'error_code': error_code, 'developer_message': developer_message}
        min_quantity, max_quantity = valid_quantity_range_by_stripe_price[stripe_price_id]
        if quantity < min_quantity or quantity > max_quantity:
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['quantity']['RANGE_EXCEEDED']
            return {'error_code': error_code, 'developer_message': developer_message}

        return {'error_code': None, 'developer_message': None}

    def handle_stripe_price_id(self, input_data: CheckoutSessionInputValidatorData) -> FieldValidationResult:
        """
        Validate the `stripe_price_id` field.
        """
        stripe_price_id = input_data.get('stripe_price_id')

        # "Invalid format" if empty, missing, or not a str.
        if not isinstance(stripe_price_id, str) or not stripe_price_id:
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['stripe_price_id']['INVALID_FORMAT']
            return {'error_code': error_code, 'developer_message': developer_message}

        # Check that the Stripe product has actually been configured in settings.
        valid_stripe_prices = [product['stripe_price_id'] for product in settings.SSP_PRODUCTS.values()]
        if stripe_price_id not in valid_stripe_prices:
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['stripe_price_id']['DOES_NOT_EXIST']
            return {'error_code': error_code, 'developer_message': developer_message}

        return {'error_code': None, 'developer_message': None}

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
        # Remove valid responses.
        validation_decisions = {
            field_name: decision for field_name, decision in validation_decisions.items()
            if decision['error_code']
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

    lms_user_id = _get_lms_user_id(email=input_data['admin_email'])

    create_kwargs: stripe.checkout.Session.CreateParams = {
        'mode': 'subscription',
        # Intended UI will be a custom react component.
        'ui_mode': 'custom',
        # Specify the type and quantity of what is being purchased.  Units for `quantity` depends on
        # the price specified, and the product associated with the price.
        'line_items': [{
            'price': input_data['stripe_price_id'],
            'quantity': input_data['quantity'],
        }],
        # Defer payment collection until the last moment, then cancel
        # the subscription if payment info has not been submitted.
        'subscription_data': {
            'trial_period_days': settings.SSP_TRIAL_PERIOD_DAYS,
            'trial_settings': {
                # Just in case the admin removes the payment method via their stripe billing portal, cancel the trial
                # after it ends.
                'end_behavior': {'missing_payment_method': 'cancel'},
            },
            'metadata': {
                # Communicate to downstream services what the admin intends the enterprise customer slug to be.
                'enterprise_customer_slug': input_data['enterprise_slug'],
                # Store the lms_user_id for improved debugging experience.
                'lms_user_id': str(lms_user_id),
            }
        },
        # Always collect payment method, not just when the amount is greater than zero.  This is influential for
        # creating a free trial plan because the amount is always zero.
        'payment_method_collection': 'always',

        # `return_url` is not required because we won't use any "redirect-based" payment methods,
        # including: iDEAL, Bancontact, SOFORT, Apple Pay, Google Pay, etc.. We only support the
        # `card` payment method which is not redirect-based.
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
