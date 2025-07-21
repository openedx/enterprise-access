"""
Tests for Checkout BFF response builders.
"""

from decimal import Decimal
from unittest import mock

import ddt
from django.test import RequestFactory
from rest_framework import status

from enterprise_access.apps.bffs.checkout.context import CheckoutContext
from enterprise_access.apps.bffs.checkout.response_builder import CheckoutContextResponseBuilder
from test_utils import APITest


@ddt.ddt
class TestCheckoutContextResponseBuilder(APITest):
    """
    Tests for the Checkout Context Response Builder.
    """

    def setUp(self):
        super().setUp()
        self.request_factory = RequestFactory()
        self.request = self.request_factory.post('/api/v1/bffs/checkout/context')
        self.request.user = self.user

    def _create_context(self):
        """
        Helper to create an empty context.
        """
        context = CheckoutContext(self.request)
        return context

    def _create_minimal_valid_context(self):
        """
        Helper to create a minimally-valid context.
        """
        context = CheckoutContext(self.request)
        context.pricing = {
            'default_by_lookup_key': 'subscription_licenses_yearly',
            'prices': []
        }
        context.field_constraints = {
            'quantity': {'min': 5, 'max': 30},
            'enterprise_slug': {
                'min_length': 3,
                'max_length': 30,
                'pattern': '^[a-z0-9-]+$'
            }
        }
        return context

    def test_build_complete_context(self):
        """
        Test building a response with a complete context containing all fields.
        """
        # Setup a complete context
        context = self._create_context()
        context.existing_customers_for_authenticated_user = [
            {
                'customer_uuid': '123',
                'customer_name': 'Test Enterprise',
                'customer_slug': 'test-enterprise',
                'stripe_customer_id': 'cus_123',
                'is_self_service': True,
                'admin_portal_url': '/enterprise/test-enterprise/admin',
            }
        ]
        context.pricing = {
            'default_by_lookup_key': 'subscription_licenses_yearly',
            'prices': [
                {
                    'id': 'price_123',
                    'product': 'prod_123',
                    'lookup_key': 'subscription_licenses_yearly',
                    'recurring': {'interval': 'year', 'interval_count': 1},
                    'currency': 'usd',
                    'unit_amount': 10000,
                    'unit_amount_decimal': Decimal('100.00'),
                }
            ]
        }
        context.field_constraints = {
            'quantity': {'min': 5, 'max': 30},
            'enterprise_slug': {
                'min_length': 3,
                'max_length': 30,
                'pattern': '^[a-z0-9-]+$'
            }
        }

        # Create and build response
        builder = CheckoutContextResponseBuilder(context)
        builder.build()

        # Serialize to get final output
        data, status_code = builder.serialize()

        # Assertions
        self.assertEqual(status_code, status.HTTP_200_OK)

        # Check exact customer data
        customers = data['existing_customers_for_authenticated_user']
        self.assertEqual(len(customers), 1)
        self.assertEqual(customers[0]['customer_uuid'], '123')
        self.assertEqual(customers[0]['customer_name'], 'Test Enterprise')
        self.assertEqual(customers[0]['customer_slug'], 'test-enterprise')
        self.assertEqual(customers[0]['stripe_customer_id'], 'cus_123')
        self.assertEqual(customers[0]['is_self_service'], True)
        self.assertEqual(customers[0]['admin_portal_url'], '/enterprise/test-enterprise/admin')

        # Check exact pricing data
        pricing = data['pricing']
        self.assertEqual(pricing['default_by_lookup_key'], 'subscription_licenses_yearly')
        self.assertEqual(len(pricing['prices']), 1)
        price = pricing['prices'][0]
        self.assertEqual(price['id'], 'price_123')
        self.assertEqual(price['product'], 'prod_123')
        self.assertEqual(price['lookup_key'], 'subscription_licenses_yearly')
        self.assertEqual(price['recurring']['interval'], 'year')
        self.assertEqual(price['recurring']['interval_count'], 1)
        self.assertEqual(price['currency'], 'usd')
        self.assertEqual(price['unit_amount'], 10000)
        self.assertEqual(price['unit_amount_decimal'], '100.00')  # Note: decimal gets serialized to string

        # Check exact constraint data
        constraints = data['field_constraints']
        self.assertEqual(constraints['quantity']['min'], 5)
        self.assertEqual(constraints['quantity']['max'], 30)
        self.assertEqual(constraints['enterprise_slug']['min_length'], 3)
        self.assertEqual(constraints['enterprise_slug']['max_length'], 30)
        self.assertEqual(constraints['enterprise_slug']['pattern'], '^[a-z0-9-]+$')

    def test_build_minimal_context(self):
        """
        Test building a response with a minimal context.
        """
        # Setup minimal context (missing some fields)
        context = self._create_minimal_valid_context()

        # Create and build response - should use defaults
        builder = CheckoutContextResponseBuilder(context)
        builder.build()

        # Serialize to get final output
        data, status_code = builder.serialize()

        # Assertions
        self.assertEqual(status_code, status.HTTP_200_OK)

        # Check that missing fields are populated with defaults from settings
        self.assertEqual(data['existing_customers_for_authenticated_user'], [])
        self.assertIn('pricing', data)
        self.assertIn('default_by_lookup_key', data['pricing'])
        # This should match whatever is in settings.DEFAULT_SSP_PRICE_LOOKUP_KEY
        self.assertIsNotNone(data['pricing']['default_by_lookup_key'])
        self.assertEqual(data['pricing']['prices'], [])
        self.assertIn('quantity', data['field_constraints'])
        self.assertIn('enterprise_slug', data['field_constraints'])

    def test_serialize_response_data(self):
        """
        Test that serialize() method correctly formats response data with exact values.
        """
        # Setup context with specific data
        context = self._create_context()
        context.existing_customers_for_authenticated_user = [
            {
                'customer_uuid': 'abc123',
                'customer_name': 'Test Corp',
                'customer_slug': 'test-corp',
                'stripe_customer_id': 'cus_xyz789',
                'is_self_service': False,
                'admin_portal_url': '/enterprise/test-corp/admin',
            }
        ]
        context.pricing = {
            'default_by_lookup_key': 'subscription_licenses_yearly',
            'prices': [
                {
                    'id': 'price_123abc',
                    'product': 'prod_456def',
                    'lookup_key': 'subscription_licenses_yearly',
                    'recurring': {'interval': 'year', 'interval_count': 1},
                    'currency': 'usd',
                    'unit_amount': 15000,
                    'unit_amount_decimal': Decimal('150.00'),
                }
            ]
        }
        context.field_constraints = {
            'quantity': {'min': 5, 'max': 30},
            'enterprise_slug': {
                'min_length': 3,
                'max_length': 30,
                'pattern': '^[a-z0-9-]+$'
            }
        }

        # Build response
        builder = CheckoutContextResponseBuilder(context)
        builder.build()

        # Test serialize method
        data, status_code = builder.serialize()

        # Assertions
        self.assertEqual(status_code, status.HTTP_200_OK)

        # Check exact customer data
        customers = data['existing_customers_for_authenticated_user']
        self.assertEqual(len(customers), 1)
        self.assertEqual(customers[0]['customer_uuid'], 'abc123')
        self.assertEqual(customers[0]['customer_name'], 'Test Corp')
        self.assertEqual(customers[0]['customer_slug'], 'test-corp')
        self.assertEqual(customers[0]['stripe_customer_id'], 'cus_xyz789')
        self.assertEqual(customers[0]['is_self_service'], False)
        self.assertEqual(customers[0]['admin_portal_url'], '/enterprise/test-corp/admin')

        # Check exact pricing data
        pricing = data['pricing']
        self.assertEqual(pricing['default_by_lookup_key'], 'subscription_licenses_yearly')
        self.assertEqual(len(pricing['prices']), 1)
        price = pricing['prices'][0]
        self.assertEqual(price['id'], 'price_123abc')
        self.assertEqual(price['product'], 'prod_456def')
        self.assertEqual(price['lookup_key'], 'subscription_licenses_yearly')
        self.assertEqual(price['recurring']['interval'], 'year')
        self.assertEqual(price['recurring']['interval_count'], 1)
        self.assertEqual(price['currency'], 'usd')
        self.assertEqual(price['unit_amount'], 15000)
        self.assertEqual(price['unit_amount_decimal'], '150.00')  # Note: decimal gets serialized to string

        # Check exact constraint data
        constraints = data['field_constraints']
        self.assertEqual(constraints['quantity']['min'], 5)
        self.assertEqual(constraints['quantity']['max'], 30)
        self.assertEqual(constraints['enterprise_slug']['min_length'], 3)
        self.assertEqual(constraints['enterprise_slug']['max_length'], 30)
        self.assertEqual(constraints['enterprise_slug']['pattern'], '^[a-z0-9-]+$')

    def test_serializer_validation_error(self):
        """
        Test handling of serializer validation errors with invalid data structure.
        """
        # Setup context with valid structure
        context = self._create_context()

        # Make pricing valid
        context.pricing = {
            'default_by_lookup_key': 'something',
            'prices': []
        }

        # But provide invalid field constraints - missing required fields
        context.field_constraints = {
            'quantity': {
                # Missing 'max' which is required
                'min': 5
            },
            # Missing 'enterprise_slug' which is required
        }

        # Create builder
        builder = CheckoutContextResponseBuilder(context)
        builder.build()

        with self.assertRaises(Exception):
            # Should raise validation error due to incomplete data structure
            builder.serializer()
