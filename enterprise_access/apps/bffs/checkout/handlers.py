"""
Handlers for the Checkout BFF endpoints.
"""
import logging
from datetime import datetime
from typing import Dict

import stripe
from django.conf import settings
from pytz import UTC

from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.bffs.api import (
    get_and_cache_enterprise_customer_users,
    transform_enterprise_customer_users_data
)
from enterprise_access.apps.bffs.checkout.context import (
    CheckoutContext,
    CheckoutSuccessContext,
    CheckoutValidationContext
)
from enterprise_access.apps.bffs.checkout.serializers import CheckoutIntentModelSerializer
from enterprise_access.apps.bffs.handlers import BaseHandler
from enterprise_access.apps.customer_billing.api import validate_free_trial_checkout_session
from enterprise_access.apps.customer_billing.models import CheckoutIntent
from enterprise_access.apps.customer_billing.pricing_api import get_ssp_product_pricing
from enterprise_access.apps.customer_billing.stripe_api import (
    get_stripe_checkout_session,
    get_stripe_customer,
    get_stripe_invoice,
    get_stripe_payment_intent,
    get_stripe_payment_method,
    get_stripe_subscription
)
from enterprise_access.utils import cents_to_dollars

logger = logging.getLogger(__name__)


class CheckoutIntentAwareHandlerMixin:
    """
    Mixin to help fetch CheckoutIntents for the requesting user.
    """
    def _get_checkout_intent(self) -> Dict | None:
        """
        Load checkout intent data (from database) for the given user.
        """
        checkout_intent_instance = CheckoutIntent.for_user(self.context.user)
        checkout_intent_data = None
        if checkout_intent_instance:
            checkout_intent_data = CheckoutIntentModelSerializer(checkout_intent_instance).data
        return checkout_intent_data


class CheckoutContextHandler(CheckoutIntentAwareHandlerMixin, BaseHandler):
    """
    Handler for the checkout context endpoint.

    Responsible for gathering:
    - Enterprise customer information for authenticated users
    - Pricing options for self-service subscriptions
    - Field constraints for the checkout form
    """
    context: CheckoutContext

    def __init__(self, context: CheckoutContext):
        """
        Initialize with the request context.

        Args:
            context: The handler context object containing request information
        """
        super().__init__(context)
        self.lms_client = LmsApiClient()

    def load_and_process(self):
        """
        Load data and process it for the response.

        This method:
        1. Checks if the user is authenticated
        2. If authenticated, fetches associated enterprise customers
        3. Fetches pricing options from Stripe
        4. Gathers field constraints from settings
        5. Populates the context with all data
        """
        try:
            self.context.pricing = self._get_pricing_data()
            self.context.field_constraints = self._get_field_constraints()
            self.context.checkout_intent = self._get_checkout_intent()
            if self.context.user:
                self._load_enterprise_customers()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Error loading/processing checkout context handler for request user %s",
                self.context.user,
            )
            self.add_error(
                user_message="Could not load and/or process checkout context data",
                developer_message=f"Unable to load and/or process checkout context data: {exc}",
            )

    def _load_enterprise_customers(self):
        """
        Load enterprise customer information for the authenticated user.
        """
        try:
            # Check if the user is authenticated
            if not self.context.user.is_authenticated:
                logger.debug("User is not authenticated, skipping enterprise customer lookup")
                return

            # Get enterprise customer users for the authenticated user
            enterprise_customer_users_data = get_and_cache_enterprise_customer_users(
                self.context.request,
                traverse_pagination=True,
            )

            # Transform the data
            transformed_data = transform_enterprise_customer_users_data(
                enterprise_customer_users_data,
                self.context.request,
                enterprise_customer_slug=None,
                enterprise_customer_uuid=None,
            )

            # Format data according to our API contract
            formatted_customers = []

            for customer_user in transformed_data.get('all_linked_enterprise_customer_users', []):
                customer = customer_user.get('enterprise_customer', {})
                if customer:
                    slug = customer.get('slug')
                    admin_portal_url = f'{settings.ENTERPRISE_ADMIN_PORTAL_URL}/{slug}' if slug else ''
                    formatted_customers.append({
                        'customer_uuid': customer.get('uuid'),
                        'customer_name': customer.get('name'),
                        'customer_slug': slug,
                        'stripe_customer_id': customer.get('stripe_customer_id', ''),
                        'is_self_service': customer.get('is_self_service', False),
                        'admin_portal_url': admin_portal_url,
                    })

            self.context.existing_customers_for_authenticated_user = formatted_customers
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Error loading enterprise customers for user: %s",
                exc
            )
            self.add_error(
                user_message="Could not fetch existing customer data for user",
                developer_message=f"Unable to load customer data for user: {exc}",
            )

    def _get_pricing_data(self) -> Dict:
        """
        Get pricing data from Stripe for self-service subscription plans.

        Returns:
            Dict containing default lookup key and list of price objects
        """
        try:
            # remember that this function eventually invokes price schema
            # validation, it may raise a StripePricingError
            pricing_data = get_ssp_product_pricing()

            # Format the pricing data according to our API response schema
            prices = []
            for _, price_data in pricing_data.items():
                prices.append({
                    'id': price_data.get('id'),
                    'product': price_data.get('product', {}).get('id'),
                    'lookup_key': price_data.get('lookup_key'),
                    'recurring': price_data.get('recurring', {}),
                    'currency': price_data.get('currency'),
                    'unit_amount': price_data.get('unit_amount'),
                    'unit_amount_decimal': str(price_data.get('unit_amount_decimal'))
                })

            return {
                'default_by_lookup_key': settings.DEFAULT_SSP_PRICE_LOOKUP_KEY,
                'prices': prices
            }
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Error fetching pricing data: %s", exc)
            self.add_error(
                user_message="Could not load pricing data.",
                developer_message=f"Could not load pricing data: {exc}",
            )
            return {
                'default_by_lookup_key': settings.DEFAULT_SSP_PRICE_LOOKUP_KEY,
                'prices': []
            }

    def _get_field_constraints(self) -> Dict:
        """
        Get field constraints from settings.

        Returns:
            Dict containing constraints for form fields
        """
        # Get quantity constraints from SSP_PRODUCTS setting
        quantity_constraints = {'min': 5, 'max': 30}  # Default values
        for product_config in settings.SSP_PRODUCTS.values():
            if 'quantity_range' in product_config:
                min_val, max_val = product_config['quantity_range']
                quantity_constraints = {'min': min_val, 'max': max_val}
                break

        return {
            'quantity': quantity_constraints,
            'enterprise_slug': {
                'min_length': 3,
                'max_length': 30,
                'pattern': '^[a-z0-9-]+$'
            }
        }


class CheckoutValidationHandler(BaseHandler):
    """
    Handler for validating checkout form fields.
    """
    context: CheckoutValidationContext

    def __init__(self, context: CheckoutValidationContext):
        super().__init__(context)
        self.user = getattr(context.request, 'user', None)
        self.authenticated_user = self.user if self.user and self.user.is_authenticated else None

    def load_and_process(self):
        """
        Process the validation request.
        """
        request_data = self.context.request.data

        # Check if admin_email is provided to check user existence
        # We intentionally initialize this to None,
        # which has the semantics of "we don't know if this user exists or not"
        user_exists_for_email = None
        if (admin_email := request_data.get('admin_email')):
            user_exists_for_email = self._check_user_existence(admin_email)

        # Create a mutable copy of the request data.
        validation_data = dict(request_data.items())
        validation_decisions = {}

        # Only validate enterprise_slug if authenticated
        if not self.authenticated_user and 'enterprise_slug' in request_data:
            validation_decisions['enterprise_slug'] = {
                'error_code': 'authentication_required',
                'developer_message': 'Authentication required to validate enterprise slugs.'
            }
            validation_data.pop('enterprise_slug')

        if validation_data:
            validation_results = validate_free_trial_checkout_session(
                user=self.authenticated_user,
                **validation_data
            )
            validation_decisions.update(validation_results)

        self.context.validation_decisions = validation_decisions
        self.context.user_authn = {
            'user_exists_for_email': user_exists_for_email
        }

    def _check_user_existence(self, email):
        """
        Check if a user exists for the given email.
        """
        try:
            lms_client = LmsApiClient()
            user_data = lms_client.get_lms_user_account(email=email)
            return bool(user_data)
        except Exception:  # pylint: disable=broad-except
            # In case of error, we don't know if the user exists
            return None


class CheckoutSuccessHandler(CheckoutContextHandler):
    """
    Handler for checkout success operations. Builds on the ``CheckoutContextHandler``
    to enhance the checkout intent record with addtional data from the stripe API.
    """
    context: CheckoutSuccessContext

    def load_and_process(self):
        """
        Loads base checkout context data, then enhances
        the checkout intent record with more data from the Stripe API.
        """
        super().load_and_process()
        if self.context.checkout_intent is None:
            return

        self.context.checkout_intent['first_billable_invoice'] = None

        try:
            self.enhance_with_stripe_data()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Error loading checkout success handler Stripe data for request user %s",
                self.context.user,
            )
            self.add_error(
                user_message="Could not load and/or process checkout success data",
                developer_message=f"Unable to load and/or process checkout success data: {exc}",
            )

    def enhance_with_stripe_data(self):
        """
        Enhance checkout intent data with Stripe API data. Called for side effect.

        Returns:
            None (called for side effect)
        """
        checkout_intent_data = self.context.checkout_intent

        session_id = checkout_intent_data.get('stripe_checkout_session_id')
        if not session_id:
            logger.warning(
                "No Stripe checkout session id for checkout intent: "
                f"{checkout_intent_data.get('id')}"
            )
            return

        try:
            session = get_stripe_checkout_session(session_id)
        except stripe.error.StripeError:
            logger.exception("Error retrieving Stripe checkout session: %s", session_id)
            return

        first_billable_invoice = {
            'start_time': None,
            'end_time': None,
            'last4': None,
            'quantity': None,
            'unit_amount_decimal': None,
            'customer_phone': None,
            'customer_name': None,
            'billing_address': None,
        }
        # THIS IS THE SIDE-EFFECT INITIALIZATION
        checkout_intent_data['first_billable_invoice'] = first_billable_invoice

        payment_method = self._get_payment_method(session)
        if payment_method:
            first_billable_invoice.update(self._get_card_billing_details(payment_method))

        invoice_id = session.get('invoice')
        subscription_id = session.get('subscription')

        invoice = self._get_invoice_record(invoice_id, subscription_id)
        if not invoice:
            return

        subscription_item = self._get_subscription_item(invoice)
        if not subscription_item:
            return

        first_billable_invoice['quantity'] = subscription_item.get('quantity')

        if unit_amount := subscription_item.get('price', {}).get('unit_amount_decimal'):
            first_billable_invoice['unit_amount_decimal'] = cents_to_dollars(unit_amount)

        first_billable_invoice.update(self._get_subscription_start_end(subscription_item))
        first_billable_invoice.update(self._get_customer_info(invoice))

    @staticmethod
    def _get_payment_method(session):
        """ Helper to fetch payment method record from Stripe. """
        if not (payment_intent_id := session.get('payment_intent')):
            logger.warning('No payment intent on stripe session %s', session.get('id'))
            return None

        try:
            payment_intent = get_stripe_payment_intent(payment_intent_id)
        except stripe.error.StripeError:
            logger.exception("Error retrieving Stripe payment intent: %s", payment_intent_id)
            return None

        if not (payment_method_id := payment_intent.get('payment_method')):
            logger.warning('No payment method on stripe payment intent %s', payment_intent_id)
            return None

        payment_method = None
        try:
            payment_method = get_stripe_payment_method(payment_method_id)
        except stripe.error.StripeError:
            logger.exception("Error retrieving Stripe payment method: %s", payment_method_id)
        return payment_method

    @staticmethod
    def _get_card_billing_details(payment_method):
        """ Helper to fetch card last 4 and billing address. """
        result = {}
        if (card_metadata := payment_method.get('card', {})):
            result['last4'] = card_metadata.get('last4')
        if (billing_details := payment_method.get('billing_details', {})):
            result['billing_address'] = billing_details.get('address')
        return result

    @staticmethod
    def _get_invoice_record(invoice_id, subscription_id):
        """ Helper to fetch invoice record via Stripe API. """
        if not invoice_id and subscription_id:
            # If there's no invoice directly on the session, try to get it from the subscription
            try:
                subscription = get_stripe_subscription(subscription_id)
                invoice_id = subscription.get('latest_invoice')
            except stripe.error.StripeError:
                logger.exception("Error retrieving Stripe subscription: %s", subscription_id)
                return None

        if not invoice_id:
            logger.warning(
                'Could not find invoice in Stripe subscription %s', subscription_id,
            )
            return None

        try:
            return get_stripe_invoice(invoice_id)
        except stripe.error.StripeError:
            logger.exception("Error retrieving Stripe invoice: %s", invoice_id)
            return None

    @staticmethod
    def _get_subscription_item(invoice):
        """
        Helper to fetch a Stripe subscription item record from an invoice.
        """
        if not (lines_data := invoice.get('lines', {}).get('data', [])):
            logger.warning('No lines on invoice %s', invoice.get('id'))
            return None
        if not (subscription_item := lines_data[0]):
            logger.warning('No subscription items in invoice %s', invoice.get('id'))
            return None
        return subscription_item

    @staticmethod
    def _get_subscription_start_end(subscription_item):
        """ Returns a dict with formatted subscription item start/end time. """
        result = {}
        if (period := subscription_item.get('period', {})):
            if (start_timestamp := period.get('start')):
                result['start_time'] = datetime.fromtimestamp(start_timestamp).replace(tzinfo=UTC)
            if (end_timestamp := period.get('end')):
                result['end_time'] = datetime.fromtimestamp(end_timestamp).replace(tzinfo=UTC)
        return result

    @staticmethod
    def _get_customer_info(invoice):
        """ Helper to get dict of customer info from an invoice. """
        result = {}
        if not (customer_id := invoice.get('customer')):
            logger.warning('No customer available on invoice %s', invoice.get('id'))
            return result

        try:
            customer = get_stripe_customer(customer_id)
            result['customer_name'] = customer.get('name')
            result['customer_phone'] = customer.get('phone')
        except stripe.error.StripeError:
            logger.exception("Error retrieving Stripe customer: %s", customer_id)
        return result
