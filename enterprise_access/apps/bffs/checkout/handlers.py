"""
Handlers for the Checkout BFF endpoints.
"""
import logging
from typing import Dict, List, Optional

from django.conf import settings
from django.urls import reverse

from enterprise_access.apps.api_client.lms_client import LmsApiClient, LmsUserApiClient
from enterprise_access.apps.bffs.api import (
    get_and_cache_enterprise_customer_users,
    transform_enterprise_customer_users_data
)
from enterprise_access.apps.bffs.handlers import BaseHandler
from enterprise_access.apps.customer_billing.api import validate_free_trial_checkout_session
from enterprise_access.apps.customer_billing.models import CheckoutIntent
from enterprise_access.apps.customer_billing.pricing_api import get_ssp_product_pricing

logger = logging.getLogger(__name__)


class CheckoutContextHandler(BaseHandler):
    """
    Handler for the checkout context endpoint.

    Responsible for gathering:
    - Enterprise customer information for authenticated users
    - Pricing options for self-service subscriptions
    - Field constraints for the checkout form
    """

    def __init__(self, context):
        """
        Initialize with the request context.

        Args:
            context: The handler context object containing request information
        """
        self.context = context
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
            self.context.checkout_intent = CheckoutIntent.for_user(self.context.user)
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
        except Exception as exc:
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
            for product_key, price_data in pricing_data.items():
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
    def __init__(self, context):
        self.context = context
        self.user = getattr(context.request, 'user', None)
        self.authenticated_user = self.user if self.user.is_authenticated else None

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

        validation_data = {k: v for k, v in request_data.items()}
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
