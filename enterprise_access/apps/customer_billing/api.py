"""
Python API for customer_billing app.
"""
import logging
from collections.abc import Mapping
from typing import TypedDict, Unpack, cast

import stripe
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import validate_email, validate_slug
from requests.exceptions import HTTPError

from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.customer_billing.constants import CHECKOUT_SESSION_ERROR_CODES
from enterprise_access.apps.customer_billing.models import (
    CheckoutIntent,
    FailedCheckoutIntentConflict,
    SlugReservationConflict
)
from enterprise_access.apps.customer_billing.pricing_api import get_ssp_product_pricing
from enterprise_access.apps.customer_billing.stripe_api import create_subscription_checkout_session

User = get_user_model()
logger = logging.getLogger(__name__)


class CheckoutSessionInputValidatorData(TypedDict, total=False):
    """
    `total=False` so that validation does not require all fields to be provided.
    """
    user: AbstractUser | None
    admin_email: str
    full_name: str
    enterprise_slug: str
    quantity: int
    stripe_price_id: str


class CheckoutSessionInputData(TypedDict, total=True):
    """
    Input parameters for checkout session creation.
    """
    user: AbstractUser | None
    admin_email: str
    enterprise_slug: str
    company_name: str
    quantity: int
    stripe_price_id: str


class FieldValidationResult(TypedDict):
    error_code: str | None
    developer_message: str | None


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

    def get_lms_user_id(self, email):
        if not hasattr(self, '_cached_lms_user_id'):
            self._cached_lms_user_id = _get_lms_user_id(email)  # pylint: disable=attribute-defined-outside-init
        return self._cached_lms_user_id

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
        lms_user_id = self.get_lms_user_id(email=admin_email)
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
        user = input_data.get('user')

        # We need multiple form fields to validate enterprise_slug.
        if not all([admin_email, enterprise_slug]):
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['common']['INCOMPLETE_DATA']
            logger.info(f'enterprise_slug invalid: {developer_message}')
            return {'error_code': error_code, 'developer_message': developer_message}

        slug_error_codes = CHECKOUT_SESSION_ERROR_CODES['enterprise_slug']
        # Basic slug format validation inherited from django core.
        try:
            validate_slug(enterprise_slug)
        except ValidationError:
            error_code, developer_message = slug_error_codes['INVALID_FORMAT']
            logger.info(f'enterprise_slug invalid: "{enterprise_slug}". {developer_message}')
            return {'error_code': error_code, 'developer_message': developer_message}

        # Check if slug is available (considering user's own reservation)
        if not CheckoutIntent.can_reserve(slug=enterprise_slug, exclude_user=user):
            error_code, developer_message = slug_error_codes['SLUG_RESERVED']
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
        Validate the `quantity` field using Stripe price data.
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

        try:
            # Get the SSP product pricing data (includes quantity ranges)
            ssp_pricing = get_ssp_product_pricing()

            # Find the SSP product that matches this stripe_price_id
            matching_product = None
            for _, price_data in ssp_pricing.items():
                if price_data.get('id') == stripe_price_id:
                    matching_product = price_data
                    break

            if not matching_product:
                error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['common']['INCOMPLETE_DATA']
                return {'error_code': error_code, 'developer_message': developer_message}

            quantity_range = matching_product.get('quantity_range')
            if not quantity_range:
                error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['common']['INCOMPLETE_DATA']
                return {'error_code': error_code, 'developer_message': developer_message}

            min_quantity, max_quantity = quantity_range
            if quantity < min_quantity or quantity > max_quantity:
                error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['quantity']['RANGE_EXCEEDED']
                return {'error_code': error_code, 'developer_message': developer_message}

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error(f'Error validating quantity for stripe_price_id {stripe_price_id}: {exc}')
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['common']['INCOMPLETE_DATA']
            return {'error_code': error_code, 'developer_message': developer_message}

        return {'error_code': None, 'developer_message': None}

    def handle_stripe_price_id(self, input_data: CheckoutSessionInputValidatorData) -> FieldValidationResult:
        """
        Validate the `stripe_price_id` field against active Stripe prices.
        """
        stripe_price_id = input_data.get('stripe_price_id')

        # "Invalid format" if empty, missing, or not a str.
        if not isinstance(stripe_price_id, str) or not stripe_price_id:
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['stripe_price_id']['INVALID_FORMAT']
            return {'error_code': error_code, 'developer_message': developer_message}

        try:
            # Get SSP product pricing data (validates lookup_keys against Stripe)
            ssp_pricing = get_ssp_product_pricing()

            # Check if the price_id exists in any of the configured SSP products
            price_exists = any(
                price_data.get('id') == stripe_price_id
                for price_data in ssp_pricing.values()
            )

            if not price_exists:
                error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['stripe_price_id']['DOES_NOT_EXIST']
                return {'error_code': error_code, 'developer_message': developer_message}

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error(f'Error validating stripe_price_id {stripe_price_id}: {exc}')
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['stripe_price_id']['DOES_NOT_EXIST']
            return {'error_code': error_code, 'developer_message': developer_message}

        return {'error_code': None, 'developer_message': None}

    def handle_user(self, input_data: CheckoutSessionInputValidatorData) -> FieldValidationResult:
        """
        Validates the User record for the Checkout Session.

        Side effect: adds a User record to input_data['user'] if not already present
          **and** the lms_user_id is known and present in the User table.
        """
        if not (user_record := input_data.get('user')):
            lms_user_id = self.get_lms_user_id(input_data.get('admin_email'))
            user_record = User.objects.filter(lms_user_id=lms_user_id).first()
            if not user_record:
                error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['user']['IS_NULL']
                return {'error_code': error_code, 'developer_message': developer_message}
            else:
                if user_record.email.lower() != input_data.get('admin_email').lower():
                    error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['user']['ADMIN_EMAIL_MISMATCH']
                    return {'error_code': error_code, 'developer_message': developer_message}
                else:
                    input_data['user'] = user_record

        return {'error_code': None, 'developer_message': None}

    def handle_full_name(self, input_data: CheckoutSessionInputValidatorData) -> FieldValidationResult:
        """
        Validates the provided full name of the proposed admin user.
        """
        if not input_data['full_name']:
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['full_name']['IS_NULL']
            return {'error_code': error_code, 'developer_message': developer_message}
        return {'error_code': None, 'developer_message': None}

    def handle_company_name(self, input_data: CheckoutSessionInputValidatorData) -> FieldValidationResult:
        """
        Validates the company name to ensure it's not null/empty and doesn't conflict
        with existing enterprise customers.

        TODO: when we implement the CheckoutIntent model in the future, we'll
        also want to check for any reserved customer names that match the provided company name.
        """
        company_name = input_data.get('company_name')
        user = input_data.get('user')

        # Check if company_name is provided
        if not company_name:
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['company_name']['IS_NULL']
            logger.info(f'company_name invalid. {developer_message}')
            return {'error_code': error_code, 'developer_message': developer_message}

        # Check if this name is already reserved
        if not CheckoutIntent.can_reserve(name=company_name, exclude_user=user):
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['company_name']['EXISTING_ENTERPRISE_CUSTOMER']
            return {'error_code': error_code, 'developer_message': developer_message}

        # Check for existing customers with the same name
        lms_client = LmsApiClient()
        try:
            existing_customer = lms_client.get_enterprise_customer_data(
                enterprise_customer_name=company_name,
            )
        except HTTPError as exc:
            if exc.response.status_code == 404:
                existing_customer = None
            else:
                # If we get an unexpected error, let's fail safely
                error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['common']['API_ERROR']
                logger.error(f'Error checking company name: {exc}')
                return {'error_code': error_code, 'developer_message': developer_message}

        if existing_customer:
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['company_name']['EXISTING_ENTERPRISE_CUSTOMER']
            return {'error_code': error_code, 'developer_message': developer_message}

        return {'error_code': None, 'developer_message': None}

    validation_handlers = {
        'admin_email': handle_admin_email,
        'full_name': handle_full_name,
        'company_name': handle_company_name,
        'enterprise_slug': handle_enterprise_slug,
        'quantity': handle_quantity,
        'stripe_price_id': handle_stripe_price_id,
        'user': handle_user,
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


class CreateCheckoutSessionSlugReservationConflict(Exception):
    def __init__(self):
        super().__init__()
        self.non_field_errors = [{
            'error_code': 'checkout_intent_conflict_slug_reserved',
            'developer_message': 'enterprise_slug or enterprise_name has already been reserved.',
        }]


class CreateCheckoutSessionFailedConflict(Exception):
    def __init__(self, non_field_errors=None):  # pylint: disable=unused-argument
        super().__init__()
        self.non_field_errors = [{
            'error_code': 'checkout_intent_conflict_failed',
            'developer_message': 'A failed checkout intent already exists for user.',
        }]


def create_free_trial_checkout_session(
    **input_data: Unpack[CheckoutSessionInputData],
) -> stripe.checkout.Session:
    """
    Create a Stripe "Checkout Session" for a free trial subscription plan.
    """
    validator = CheckoutSessionInputValidator()
    validation_errors = validator.validate(
        input_data=cast(CheckoutSessionInputValidatorData, input_data)
    )
    if validation_errors:
        raise CreateCheckoutSessionValidationError(validation_errors_by_field=validation_errors)

    user = input_data['user']

    # Create checkout intent, which reserves the enterprise name & slug.
    try:
        intent = CheckoutIntent.create_intent(
            user=user,
            quantity=input_data.get('quantity'),
            slug=input_data.get('enterprise_slug'),
            name=input_data.get('company_name'),
        )
    except SlugReservationConflict as exc:
        raise CreateCheckoutSessionSlugReservationConflict() from exc
    except FailedCheckoutIntentConflict as exc:
        raise CreateCheckoutSessionFailedConflict() from exc

    lms_user_id = user.lms_user_id
    checkout_session = create_subscription_checkout_session(
        input_data=input_data,
        lms_user_id=lms_user_id,
        checkout_intent=intent,
    )

    intent.update_stripe_identifiers(
        session_id=checkout_session['id'],
        customer_id=checkout_session.get('customer'),
    )
    logger.info(f'Updated checkout intent {intent.id} with Stripe session {checkout_session["id"]}')

    return checkout_session


def create_stripe_billing_portal_session(
    checkout_intent: CheckoutIntent, return_url: str
) -> stripe.billing_portal.Session:
    """
    Create a Stripe billing portal session for a given CheckoutIntent.

    Args:
        checkout_intent: The CheckoutIntent record containing the stripe customer ID
        return_url: The URL to redirect the user to after they're done in the portal

    Returns:
        A Stripe billing portal Session object with a 'url' attribute

    Raises:
        ValueError: If the checkout intent has no stripe_customer_id
        stripe.StripeError: If the Stripe API call fails
    """
    if not checkout_intent.stripe_customer_id:
        raise ValueError(
            f"CheckoutIntent {checkout_intent.id} has no stripe_customer_id. "
            "Cannot create billing portal session."
        )

    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=checkout_intent.stripe_customer_id,
            return_url=return_url,
        )
        logger.info(
            f"Created Stripe billing portal session {portal_session.id} "
            f"for CheckoutIntent {checkout_intent.id}, customer {checkout_intent.stripe_customer_id}"
        )
        return portal_session
    except stripe.StripeError as exc:
        logger.exception(
            f"StripeError creating billing portal session for CheckoutIntent {checkout_intent.id}: {exc}"
        )
        raise
