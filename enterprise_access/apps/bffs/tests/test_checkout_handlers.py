"""
Tests for Checkout BFF handlers.
"""

from decimal import Decimal
from unittest import mock

import ddt
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from enterprise_access.apps.bffs.checkout.context import CheckoutContext
from enterprise_access.apps.bffs.checkout.handlers import CheckoutContextHandler
from enterprise_access.apps.core.tests.factories import UserFactory
from test_utils import APITest


@ddt.ddt
class TestCheckoutContextHandler(APITest):
    """
    Tests for the CheckoutContextHandler.
    """

    def setUp(self):
        super().setUp()
        # APITest already creates self.user
        self.request_factory = RequestFactory()
        self.request = self.request_factory.post('/api/v1/bffs/checkout/context')
        self.request.user = self.user

    def _create_context(self, user=None):
        """
        Helper to create a context with an optional user.
        """
        if user:
            self.request.user = user
        context = CheckoutContext(self.request)
        return context

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_ssp_product_pricing')
    def test_load_and_process_unauthenticated_user(self, mock_get_pricing):
        """
        Test that load_and_process works for unauthenticated users.
        """
        # Setup
        mock_get_pricing.return_value = {}
        context = self._create_context(user=AnonymousUser())
        handler = CheckoutContextHandler(context)

        # Execute
        handler.load_and_process()

        # Assert
        self.assertEqual(context.existing_customers_for_authenticated_user, [])
        self.assertIn('default_by_lookup_key', context.pricing)
        self.assertIn('prices', context.pricing)
        self.assertIn('quantity', context.field_constraints)

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_ssp_product_pricing')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_and_cache_enterprise_customer_users')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.transform_enterprise_customer_users_data')
    def test_load_enterprise_customers_success(self, mock_transform, mock_get_customers, mock_get_pricing):
        """
        Test that _load_enterprise_customers correctly loads customer data.
        """
        # Setup
        mock_get_pricing.return_value = {}
        mock_get_customers.return_value = {'results': [{'uuid': '123'}]}
        mock_transform.return_value = {
            'all_linked_enterprise_customer_users': [
                {'enterprise_customer': {
                    'uuid': '123',
                    'name': 'Test Enterprise',
                    'slug': 'test-enterprise',
                    'stripe_customer_id': 'cus_123',
                    'is_self_service': True,
                }}
            ]
        }

        context = self._create_context()
        handler = CheckoutContextHandler(context)

        # Execute
        handler._load_enterprise_customers()

        # Assert
        self.assertEqual(len(context.existing_customers_for_authenticated_user), 1)
        customer = context.existing_customers_for_authenticated_user[0]
        self.assertEqual(customer['customer_uuid'], '123')
        self.assertEqual(customer['customer_name'], 'Test Enterprise')
        self.assertEqual(
            customer['admin_portal_url'],
            f'{settings.ENTERPRISE_ADMIN_PORTAL_URL}/test-enterprise',
        )

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_ssp_product_pricing')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_and_cache_enterprise_customer_users')
    def test_load_enterprise_customers_no_results(self, mock_get_customers, mock_get_pricing):
        """
        Test that _load_enterprise_customers handles empty results.
        """
        # Setup
        mock_get_pricing.return_value = {}
        mock_get_customers.return_value = {'results': []}

        context = self._create_context()
        handler = CheckoutContextHandler(context)

        # Execute
        handler._load_enterprise_customers()

        # Assert
        self.assertEqual(context.existing_customers_for_authenticated_user, [])

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_ssp_product_pricing')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_and_cache_enterprise_customer_users')
    def test_load_enterprise_customers_api_error(self, mock_get_customers, mock_get_pricing):
        """
        Test that _load_enterprise_customers gracefully handles API errors.
        """
        mock_get_pricing.return_value = {}
        mock_get_customers.side_effect = Exception("API Error")

        context = self._create_context()
        handler = CheckoutContextHandler(context)

        handler._load_enterprise_customers()

        # Should not fail and should have empty list
        self.assertEqual(context.existing_customers_for_authenticated_user, [])

        # Error should be related to fetching enterprise customer data
        self.assertEqual(len(context.errors), 1)
        error_messages = [error.get('developer_message', '') for error in context.errors]
        self.assertTrue(any('customer data' in msg.lower() for msg in error_messages))

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_ssp_product_pricing')
    def test_get_pricing_data_with_multiple_products(self, mock_get_pricing):
        """
        Test that _get_pricing_data correctly filters and formats multiple products.
        """
        mock_get_pricing.return_value = {
            'valid_product': {
                'id': 'price_123',
                'product': {'id': 'prod_123', 'active': True},
                'billing_scheme': 'per_unit',
                'type': 'recurring',
                'recurring': {'usage_type': 'licensed'},
                'currency': 'usd',
                'unit_amount': 10000,
                'unit_amount_decimal': Decimal('100.00'),
                'lookup_key': 'valid_key',
            },
            'other_valid_product': {
                'id': 'price_456',
                'product': {'id': 'prod_456', 'active': True},
                'billing_scheme': 'per_unit',
                'type': 'recurring',
                'recurring': {'usage_type': 'licensed'},
                'currency': 'usd',
                'unit_amount': 20000,
                'unit_amount_decimal': Decimal('200.00'),
                'lookup_key': 'other_key',
            },
        }

        context = self._create_context()
        handler = CheckoutContextHandler(context)

        pricing = handler._get_pricing_data()

        self.assertIn('default_by_lookup_key', pricing)
        self.assertIn('prices', pricing)
        self.assertEqual(len(pricing['prices']), 2)

    def test_get_field_constraints_default_values(self):
        """
        Test that _get_field_constraints returns expected defaults.
        """
        # Setup
        context = self._create_context()
        handler = CheckoutContextHandler(context)

        # Execute
        constraints = handler._get_field_constraints()

        # Assert
        self.assertIn('quantity', constraints)
        self.assertEqual(constraints['quantity']['min'], 5)
        self.assertEqual(constraints['quantity']['max'], 30)
        self.assertIn('enterprise_slug', constraints)
        self.assertIn('pattern', constraints['enterprise_slug'])
        self.assertTrue(constraints['enterprise_slug']['pattern'].startswith('^'))

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_ssp_product_pricing')
    def test_handler_adds_error_on_pricing_failure(self, mock_get_pricing):
        """
        Test that the handler adds an error to the context when pricing data fetch fails.
        """
        # Setup - make pricing API throw an error
        mock_get_pricing.side_effect = Exception("Pricing API failed")

        context = self._create_context()
        handler = CheckoutContextHandler(context)

        handler.load_and_process()

        # Assert - should add error to context
        self.assertTrue(hasattr(context, 'errors'))
        self.assertTrue(context.errors)  # Should have at least one error

        # Error should be related to pricing
        error_messages = [error.get('developer_message', '') for error in context.errors]
        self.assertTrue(any('pricing' in msg.lower() for msg in error_messages))
