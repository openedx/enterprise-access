"""
Tests for Checkout BFF response builders.
"""

from decimal import Decimal
from unittest import mock

import ddt
from django.test import RequestFactory
from rest_framework import status

from enterprise_access.apps.bffs.checkout.context import CheckoutContext, CheckoutValidationContext
from enterprise_access.apps.bffs.checkout.response_builder import (
    CheckoutContextResponseBuilder,
    CheckoutValidationResponseBuilder
)
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


@ddt.ddt
class TestCheckoutValidationResponseBuilder(APITest):
    """
    Tests for the CheckoutValidationResponseBuilder.
    """

    def setUp(self):
        super().setUp()
        self.request_factory = RequestFactory()
        self.request = self.request_factory.post('/api/v1/bffs/checkout/validation')
        self.request.user = self.user
        self.context = CheckoutValidationContext(self.request)

    def test_build_empty_validation_decisions(self):
        """
        Test building a response with empty validation decisions.
        """
        # Create context with empty validation results
        self.context.validation_decisions = {}
        self.context.user_authn = {'user_exists_for_email': None}

        # Create and build response
        builder = CheckoutValidationResponseBuilder(self.context)
        builder.build()

        # Verify response structure
        self.assertIn('validation_decisions', builder.response_data)
        self.assertIn('user_authn', builder.response_data)

        # Check all fields are included with null values
        validation_decisions = builder.response_data['validation_decisions']
        for field in builder.ALL_VALIDATION_FIELDS:
            self.assertIn(field, validation_decisions)
            self.assertIsNone(validation_decisions[field])

        # Verify user_authn
        self.assertIsNone(builder.response_data['user_authn']['user_exists_for_email'])

    def test_build_with_validation_errors(self):
        """
        Test building a response with validation errors.
        """
        # Create context with validation errors
        self.context.validation_decisions = {
            'company_name': {
                'error_code': 'existing_enterprise_customer',
                'developer_message': 'An enterprise customer with this name already exists.'
            },
            'quantity': {
                'error_code': 'range_exceeded',
                'developer_message': 'Quantity 50 exceeds allowed range [5, 30]'
            }
        }
        self.context.user_authn = {'user_exists_for_email': True}

        # Create and build response
        builder = CheckoutValidationResponseBuilder(self.context)
        builder.build()

        # Verify response structure
        validation_decisions = builder.response_data['validation_decisions']

        # Check error fields have correct values
        self.assertEqual(validation_decisions['company_name']['error_code'], 'existing_enterprise_customer')
        self.assertEqual(
            validation_decisions['company_name']['developer_message'],
            'An enterprise customer with this name already exists.'
        )
        self.assertEqual(validation_decisions['quantity']['error_code'], 'range_exceeded')

        # Check other fields are included with null values
        for field in ['full_name', 'admin_email', 'enterprise_slug', 'stripe_price_id']:
            self.assertIn(field, validation_decisions)
            self.assertIsNone(validation_decisions[field])

        # Verify user_authn
        self.assertTrue(builder.response_data['user_authn']['user_exists_for_email'])

    def test_build_with_mixed_validations(self):
        """
        Test building a response with a mix of valid and invalid fields.
        """
        # Some fields pass validation (null error_code), others fail
        self.context.validation_decisions = {
            'admin_email': None,  # Valid
            'full_name': None,  # Valid
            'company_name': {  # Invalid
                'error_code': 'existing_enterprise_customer',
                'developer_message': 'An enterprise customer with this name already exists.'
            },
            'enterprise_slug': None,  # Valid
        }
        self.context.user_authn = {'user_exists_for_email': False}

        # Create and build response
        builder = CheckoutValidationResponseBuilder(self.context)
        builder.build()

        # Verify response structure
        validation_decisions = builder.response_data['validation_decisions']

        # Check validated fields
        self.assertIsNone(validation_decisions['admin_email'])
        self.assertIsNone(validation_decisions['full_name'])
        self.assertEqual(validation_decisions['company_name']['error_code'], 'existing_enterprise_customer')
        self.assertIsNone(validation_decisions['enterprise_slug'])

        # Check fields not in input are included
        self.assertIsNone(validation_decisions['quantity'])
        self.assertIsNone(validation_decisions['stripe_price_id'])

        # Verify user_authn
        self.assertFalse(builder.response_data['user_authn']['user_exists_for_email'])

    def test_serialize_response_data(self):
        """
        Test that serialize() method correctly formats response data.
        """
        # Setup context with validation results
        self.context.validation_decisions = {
            'admin_email': {
                'error_code': 'not_registered',
                'developer_message': 'Given email address does not correspond to an existing user.'
            },
            'company_name': {
                'error_code': 'is_null',
                'developer_message': 'Company name cannot be empty.'
            },
        }
        self.context.user_authn = {'user_exists_for_email': False}

        # Create builder and build response
        builder = CheckoutValidationResponseBuilder(self.context)
        builder.build()

        # Test serialize method
        data, status_code = builder.serialize()

        # Assertions
        self.assertEqual(status_code, status.HTTP_200_OK)

        # Check the structure of the serialized data
        self.assertIn('validation_decisions', data)
        self.assertIn('user_authn', data)

        # Check validation decisions
        validation_decisions = data['validation_decisions']
        self.assertEqual(validation_decisions['admin_email']['error_code'], 'not_registered')
        self.assertEqual(validation_decisions['company_name']['error_code'], 'is_null')

        # Check user authentication info
        self.assertFalse(data['user_authn']['user_exists_for_email'])

    def test_build_with_missing_user_authn(self):
        """
        Test building a response when user_authn is not in context.
        """
        # Create context without user_authn
        self.context.validation_decisions = {}
        # Intentionally don't set user_authn

        # Create and build response
        builder = CheckoutValidationResponseBuilder(self.context)
        builder.build()

        # Verify default user_authn was created
        self.assertIn('user_authn', builder.response_data)
        self.assertIsNone(builder.response_data['user_authn']['user_exists_for_email'])

    def test_build_preserves_null_decisions(self):
        """
        Test that null validation decisions are preserved.
        """
        # Create context with explicit null decisions
        self.context.validation_decisions = {
            'admin_email': None,
            'company_name': None,
        }
        self.context.user_authn = {'user_exists_for_email': True}

        # Create and build response
        builder = CheckoutValidationResponseBuilder(self.context)
        builder.build()

        # Verify nulls are preserved
        validation_decisions = builder.response_data['validation_decisions']
        self.assertIsNone(validation_decisions['admin_email'])
        self.assertIsNone(validation_decisions['company_name'])
