"""
Python API for customer_billing app.
"""
import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import TypedDict, Unpack, cast
from uuid import UUID

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import validate_email, validate_slug
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from requests.exceptions import HTTPError

from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.customer_billing.constants import (
    CHECKOUT_SESSION_ERROR_CODES,
    TOP_UP_INELIGIBILITY_REASONS,
    TOP_UP_MAX_SUBSIDY_FETCH_ATTEMPTS,
    TOP_UP_MIN_DAYS_BEFORE_EXPIRY
)
from enterprise_access.apps.customer_billing.models import (
    CheckoutIntent,
    EnterpriseStripeCustomer,
    FailedCheckoutIntentConflict,
    SlugReservationConflict,
    SspProduct
)
from enterprise_access.apps.customer_billing.pricing_api import get_ssp_product_pricing
from enterprise_access.apps.customer_billing.stripe_api import (
    create_enterprise_stripe_customer,
    create_subscription_checkout_session
)
from enterprise_access.apps.subsidy_access_policy.exceptions import SubsidyAPIHTTPError
from enterprise_access.apps.subsidy_access_policy.subsidy_api import UNREACHABLE_SUBSIDY_STATUS_CODES

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
    ssp_product_slug: str
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
    ssp_product_slug: str
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
        ssp_product_slug = input_data.get('ssp_product_slug')
        stripe_price_id = input_data.get('stripe_price_id')

        # We need multiple form fields to validate quantity.
        if not quantity or (not ssp_product_slug and not stripe_price_id):
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['common']['INCOMPLETE_DATA']
            return {'error_code': error_code, 'developer_message': developer_message}

        # Positive integers only.
        if not isinstance(quantity, int) or quantity < 1:
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['quantity']['INVALID_FORMAT']
            return {'error_code': error_code, 'developer_message': developer_message}

        try:
            # Get the SSP product pricing data (includes quantity ranges)
            ssp_pricing = get_ssp_product_pricing()

            matching_product = None
            if ssp_product_slug:
                matching_product = ssp_pricing.get(ssp_product_slug)
            elif stripe_price_id:
                for price_data in ssp_pricing.values():
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
            logger.error(
                f'Error validating quantity for ssp_product_slug {ssp_product_slug} '
                f'or stripe_price_id {stripe_price_id}: {exc}'
            )
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['common']['INCOMPLETE_DATA']
            return {'error_code': error_code, 'developer_message': developer_message}

        return {'error_code': None, 'developer_message': None}

    def handle_ssp_product_slug(self, input_data: CheckoutSessionInputValidatorData) -> FieldValidationResult:
        """
        Validate the `ssp_product_slug` field against active SSP products.
        """
        ssp_product_slug = input_data.get('ssp_product_slug')

        # "Invalid format" if empty, missing, or not a str.
        if not isinstance(ssp_product_slug, str) or not ssp_product_slug:
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['ssp_product_slug']['INVALID_FORMAT']
            return {'error_code': error_code, 'developer_message': developer_message}

        try:
            # Get SSP product pricing data (validates lookup_keys against Stripe)
            ssp_pricing = get_ssp_product_pricing()

            if ssp_product_slug not in ssp_pricing:
                error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['ssp_product_slug']['DOES_NOT_EXIST']
                return {'error_code': error_code, 'developer_message': developer_message}

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error(f'Error validating ssp_product_slug {ssp_product_slug}: {exc}')
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['ssp_product_slug']['DOES_NOT_EXIST']
            return {'error_code': error_code, 'developer_message': developer_message}

        return {'error_code': None, 'developer_message': None}

    def handle_stripe_price_id(self, input_data: CheckoutSessionInputValidatorData) -> FieldValidationResult:
        """
        Validate the `stripe_price_id` field against active SSP products.
        """
        stripe_price_id = input_data.get('stripe_price_id')

        if not isinstance(stripe_price_id, str) or not stripe_price_id:
            error_code, developer_message = CHECKOUT_SESSION_ERROR_CODES['stripe_price_id']['INVALID_FORMAT']
            return {'error_code': error_code, 'developer_message': developer_message}

        try:
            ssp_pricing = get_ssp_product_pricing()
            if not any(price_data.get('id') == stripe_price_id for price_data in ssp_pricing.values()):
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
        'ssp_product_slug': handle_ssp_product_slug,
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
) -> dict:
    """
    Create a Stripe "Checkout Session" for a free trial subscription plan.
    """
    logger.info(
        'create_free_trial_checkout_session called for enterprise_slug=%s, ssp_product_slug=%s',
        input_data.get('enterprise_slug'), input_data.get('ssp_product_slug'),
    )
    validator = CheckoutSessionInputValidator()
    validation_errors = validator.validate(
        input_data=cast(CheckoutSessionInputValidatorData, input_data)
    )
    if validation_errors:
        logger.info(
            'create_free_trial_checkout_session validation failed for enterprise_slug=%s: %s',
            input_data.get('enterprise_slug'), validation_errors,
        )
        raise CreateCheckoutSessionValidationError(validation_errors_by_field=validation_errors)

    user = input_data['user']
    ssp_product_slug = input_data.get('ssp_product_slug') or settings.SSP_DEFAULT_PRODUCT_SLUG
    ssp_product_instance = SspProduct.objects.get(slug=ssp_product_slug, is_active=True)
    stripe_price_id = input_data.get('stripe_price_id')
    if not stripe_price_id:
        raise ValueError('stripe_price_id is required to create a checkout session.')
    # Create checkout intent, which reserves the enterprise name & slug.
    try:
        intent = CheckoutIntent.create_intent(
            user=user,
            quantity=input_data.get('quantity'),
            slug=input_data.get('enterprise_slug'),
            name=input_data.get('company_name'),
            ssp_product=ssp_product_instance,
        )
        logger.info(
            'Created/reserved CheckoutIntent %s for user_id=%s, enterprise_slug=%s',
            intent.id, user.id, input_data.get('enterprise_slug'),
        )
    except SlugReservationConflict as exc:
        logger.warning(
            'Slug reservation conflict creating CheckoutIntent for user_id=%s, enterprise_slug=%s: %s',
            user.id, input_data.get('enterprise_slug'), exc,
        )
        raise CreateCheckoutSessionSlugReservationConflict() from exc
    except FailedCheckoutIntentConflict as exc:
        logger.warning(
            'Failed CheckoutIntent conflict for user_id=%s, enterprise_slug=%s: %s',
            user.id, input_data.get('enterprise_slug'), exc,
        )
        raise CreateCheckoutSessionFailedConflict() from exc

    lms_user_id = user.lms_user_id
    enterprise_catalog_metadata = None
    try:
        enterprise_catalog_metadata = ssp_product_instance.enterprise_catalog_metadata
    except (HTTPError, ValueError) as exc:
        logger.warning(
            'Could not resolve enterprise_catalog metadata for ssp_product_slug=%s: %s',
            ssp_product_slug,
            exc,
        )
    checkout_session = create_subscription_checkout_session(
        input_data={**input_data, 'ssp_product_slug': ssp_product_slug, 'stripe_price_id': stripe_price_id},
        lms_user_id=lms_user_id,
        checkout_intent=intent,
        enterprise_catalog_metadata=enterprise_catalog_metadata,
    )

    intent.update_stripe_identifiers(
        session_id=checkout_session['id'],
        customer_id=checkout_session['customer'],
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


# Enterprise Stripe customer (ENT-12509).

def get_stripe_customer_id_for_enterprise(enterprise_customer_uuid: UUID | str) -> str | None:
    """
    Return the enterprise's Stripe customer ID, or None. Never calls Stripe and never writes.

    Reads EnterpriseStripeCustomer first, then the newest CheckoutIntent with a Stripe customer (a self-service
    checkout paid after the backfill has no mapping yet).
    """
    stripe_customer_id = EnterpriseStripeCustomer.objects.filter(
        enterprise_customer_uuid=enterprise_customer_uuid,
    ).values_list('stripe_customer_id', flat=True).first()
    if stripe_customer_id:
        return stripe_customer_id

    checkout_intent = CheckoutIntent.get_latest_for_customer(
        customer_uuid=enterprise_customer_uuid,
        require_stripe_customer=True,
    )
    return checkout_intent.stripe_customer_id if checkout_intent else None


def get_or_create_enterprise_stripe_customer(enterprise_customer_uuid: UUID | str) -> EnterpriseStripeCustomer:
    """
    Return the enterprise's EnterpriseStripeCustomer, recording one first if it has none: the newest CheckoutIntent's
    Stripe customer if there is one, so a self-service enterprise keeps a single customer, else a new Stripe customer.

    Concurrent calls return the same row: Stripe replays the customer for the repeated idempotency key, and the
    unique enterprise_customer_uuid lets one insert win while the others read it back.

    Raises:
        ValueError: ``enterprise_customer_uuid`` is not a UUID.
        stripe.StripeError: Creating the Stripe customer failed. Nothing is recorded; a later call tries again.
    """
    # The idempotency key is built from this value, so every spelling of the UUID must give the same string.
    enterprise_customer_uuid = UUID(str(enterprise_customer_uuid))

    existing = EnterpriseStripeCustomer.objects.filter(enterprise_customer_uuid=enterprise_customer_uuid).first()
    if existing:
        return existing

    checkout_intent = CheckoutIntent.get_latest_for_customer(
        customer_uuid=enterprise_customer_uuid,
        require_stripe_customer=True,
    )
    if checkout_intent:
        stripe_customer_id = checkout_intent.stripe_customer_id
        source = EnterpriseStripeCustomer.SourceChoices.CHECKOUT_INTENT_BACKFILL
    else:
        stripe_customer_id = create_enterprise_stripe_customer(enterprise_customer_uuid)['id']
        source = EnterpriseStripeCustomer.SourceChoices.CREATED_FOR_TOP_UP

    try:
        # A savepoint, so that losing the race doesn't break a transaction the caller has open.
        with transaction.atomic():
            mapping = EnterpriseStripeCustomer.objects.create(
                enterprise_customer_uuid=enterprise_customer_uuid,
                stripe_customer_id=stripe_customer_id,
                source=source,
            )
    except IntegrityError:
        mapping = EnterpriseStripeCustomer.objects.filter(enterprise_customer_uuid=enterprise_customer_uuid).first()
        if mapping is None:
            raise
        if mapping.stripe_customer_id != stripe_customer_id:
            logger.error(
                'Enterprise %s already maps to Stripe customer %s; Stripe customer %s was not recorded',
                enterprise_customer_uuid, mapping.stripe_customer_id, stripe_customer_id,
            )
        return mapping

    logger.info(
        'Recorded Stripe customer %s for enterprise %s (source=%s)',
        stripe_customer_id, enterprise_customer_uuid, source,
    )
    return mapping


# Learner Credit top-up eligibility (ENT-12356). The single implementation of the eligibility rules: the eligibility
# endpoint calls it today, and the quote (chunk 7) and pay (chunk 11) endpoints must call the same function so the
# portal and the backend always agree.

def is_top_up_enabled():
    """
    Whether the Learner Credit top-up endpoints are enabled (the ``FEATURE_TOP_UP_LEARNER_CREDIT`` setting).

    The one place the setting is read, on every request, so chunks 7 and 11 reuse it. Declared off in
    ``settings/base.py`` and on in ``settings/devstack.py``; stage and prod set it in edx-internal. Open decision
    KD-6: today it is a single global setting.
    """
    return settings.FEATURE_TOP_UP_LEARNER_CREDIT


def is_account_in_good_standing(policy):  # pylint: disable=unused-argument
    """
    Whether the policy's enterprise account (standing / agreement type) may be topped up.

    Stand-in until OQ-8 is answered (chunk 19 replaces it with the real source): there is no account-standing data
    source yet, so every account passes. Until then, ``FEATURE_TOP_UP_LEARNER_CREDIT`` is what keeps top-ups off
    where they must not be offered.
    """
    return True


def _reason(key):
    error_code, message = TOP_UP_INELIGIBILITY_REASONS[key]
    return {'error_code': error_code, 'message': message}


def evaluate_top_up_eligibility(policy, subsidy, now=None):
    """
    Evaluate whether a budget (``SubsidyAccessPolicy``) is eligible for a Learner Credit top-up.

    ``subsidy`` is the policy's subsidy record (``{}`` when that subsidy is unreachable, which reports
    ``budget_not_active``). Callers should use ``evaluate_top_up_eligibility_for_policies()``, which fetches it.

    Returns a dict: ``policy_uuid``, ``is_eligible``, ``reasons`` (list of ``error_code`` /
    ``message``) and ``subsidy_expiration_datetime`` (a datetime, or None).

    Rules:
      * Active: the policy is active and not retired, and the subsidy is active.
      * Expiry: ineligible only when fewer than ``TOP_UP_MIN_DAYS_BEFORE_EXPIRY`` (14) days remain.
        Exactly 14 days still passes.
      * Account standing: ``is_account_in_good_standing()``.
    """
    now = now or timezone.now()
    expiration = subsidy.get('expiration_datetime')
    expiration_datetime = parse_datetime(expiration) if isinstance(expiration, str) else expiration

    reasons = []
    if not policy.is_redemption_enabled or not subsidy.get('is_active'):
        reasons.append(_reason('BUDGET_NOT_ACTIVE'))
    if expiration_datetime and expiration_datetime - now < timedelta(days=TOP_UP_MIN_DAYS_BEFORE_EXPIRY):
        reasons.append(_reason('BUDGET_EXPIRING_SOON'))
    if not is_account_in_good_standing(policy):
        reasons.append(_reason('ACCOUNT_NOT_ELIGIBLE'))

    return {
        'policy_uuid': policy.uuid,
        'is_eligible': not reasons,
        'reasons': reasons,
        'subsidy_expiration_datetime': expiration_datetime,
    }


class SubsidyUnreachableError(SubsidyAPIHTTPError):
    """
    A budget's subsidy answered 403/404 on every one of ``TOP_UP_MAX_SUBSIDY_FETCH_ATTEMPTS`` attempts.

    Subclasses ``SubsidyAPIHTTPError``, so ``error_response`` still gives the subsidy's last response.
    """


def _fetch_top_up_subsidy_record(policy):
    """
    Fetch the policy's subsidy record, trying up to ``TOP_UP_MAX_SUBSIDY_FETCH_ATTEMPTS`` (3) times while the
    subsidy answers 403/404. Any other failure is raised immediately.

    Raises:
        SubsidyUnreachableError: if every attempt answered 403/404.
        SubsidyAPIHTTPError: for any other HTTP failure (e.g. a 5xx).
    """
    for attempt in range(1, TOP_UP_MAX_SUBSIDY_FETCH_ATTEMPTS + 1):
        try:
            return policy.subsidy_record(raise_on_error=True)
        except SubsidyAPIHTTPError as exc:
            status_code = getattr(exc.error_response, 'status_code', None)
            if status_code not in UNREACHABLE_SUBSIDY_STATUS_CODES:
                logger.error(
                    'Top-up eligibility: failed to fetch subsidy %s for policy %s (subsidy_status_code=%s).',
                    policy.subsidy_uuid, policy.uuid, status_code,
                )
                raise
            if attempt == TOP_UP_MAX_SUBSIDY_FETCH_ATTEMPTS:
                raise SubsidyUnreachableError(str(exc)) from exc.__cause__
            logger.info(
                'Top-up eligibility: subsidy %s for policy %s answered %s on attempt %s of %s; retrying.',
                policy.subsidy_uuid, policy.uuid, status_code, attempt, TOP_UP_MAX_SUBSIDY_FETCH_ATTEMPTS,
            )
    return {}  # never reached: the last attempt either returns or raises


def evaluate_top_up_eligibility_for_policies(policies, raise_if_all_unreachable=True, now=None):
    """
    Evaluate top-up eligibility for each of the given budgets, fetching their subsidy records.

    Same shape as ``get_policies_accepting_requests()`` (docs/architecture-patterns.md §18): a budget whose subsidy
    answers 403/404 (e.g. soft-deleted) on every one of ``TOP_UP_MAX_SUBSIDY_FETCH_ATTEMPTS`` (3) attempts is
    reported as ``budget_not_active`` and the other budgets are still evaluated. With ``raise_if_all_unreachable``,
    every subsidy being unreachable is re-raised instead, since that looks the same as our own credentials losing
    their role. Inactive or retired budgets are already ``budget_not_active``, so their subsidy isn't fetched (they
    report ``subsidy_expiration_datetime: None``).

    Raises:
        SubsidyUnreachableError: if every fetched subsidy is unreachable (with ``raise_if_all_unreachable``).
        SubsidyAPIHTTPError: for any other HTTP failure (e.g. a 5xx).
        requests.exceptions.RequestException: if enterprise-subsidy can't be reached at all.
    """
    now = now or timezone.now()
    results = []
    last_unreachable_error = None
    unreachable_count = 0
    fetched_count = 0

    for policy in policies:
        if not policy.is_redemption_enabled:
            # Already budget_not_active: skip the fetch, as get_policies_accepting_requests() does, so a retired
            # budget whose subsidy was soft-deleted can't count toward "every subsidy unreachable".
            results.append(evaluate_top_up_eligibility(policy, {}, now=now))
            continue
        fetched_count += 1
        try:
            subsidy = _fetch_top_up_subsidy_record(policy)
        except SubsidyUnreachableError as exc:
            last_unreachable_error = exc
            unreachable_count += 1
            logger.warning(
                'Top-up eligibility: could not fetch subsidy %s after %s attempts (subsidy_status_code=%s). '
                'Policy %s is reported as budget_not_active.',
                policy.subsidy_uuid, TOP_UP_MAX_SUBSIDY_FETCH_ATTEMPTS,
                getattr(exc.error_response, 'status_code', None), policy.uuid,
            )
            subsidy = {}
        results.append(evaluate_top_up_eligibility(policy, subsidy, now=now))

    if raise_if_all_unreachable and last_unreachable_error and unreachable_count == fetched_count:
        raise last_unreachable_error
    return results
