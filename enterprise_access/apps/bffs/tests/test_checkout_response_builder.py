"""
Tests for Checkout BFF response builders.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock
from uuid import uuid4

import ddt
from django.conf import settings
from django.test import RequestFactory
from django.utils import timezone
from rest_framework import status

from enterprise_access.apps.bffs.checkout.context import (
    CheckoutContext,
    CheckoutSuccessContext,
    CheckoutValidationContext
)
from enterprise_access.apps.bffs.checkout.response_builder import (
    CheckoutContextResponseBuilder,
    CheckoutSuccessResponseBuilder,
    CheckoutValidationResponseBuilder
)
from enterprise_access.apps.bffs.checkout.serializers import CheckoutSuccessResponseSerializer
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

        self.mock_checkout_intent = {
            'id': 123,
            'state': 'created',
            'enterprise_name': 'Test Enterprise',
            'enterprise_slug': 'test-enterprise',
            'expires_at': timezone.now() + timedelta(hours=24),
            'stripe_checkout_session_id': 'cs_test_123abc',
            'last_checkout_error': '',
            'last_provisioning_error': '',
            'workflow': None,
            'admin_portal_url': 'https://portal.edx.org/test-enterprise',
        }

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

    def test_build_complete_context_with_checkout_intent(self):
        """
        Test building a response with a complete context including checkout intent.
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
        context.checkout_intent = self.mock_checkout_intent

        # Create and build response
        builder = CheckoutContextResponseBuilder(context)
        builder.build()

        # Serialize to get final output
        data, status_code = builder.serialize()

        # Assertions
        self.assertEqual(status_code, status.HTTP_200_OK)

        # Check that checkout_intent was included
        self.assertIn('checkout_intent', data)
        intent_data = data['checkout_intent']
        self.assertEqual(intent_data['id'], 123)
        self.assertEqual(intent_data['state'], 'created')
        self.assertEqual(intent_data['enterprise_name'], 'Test Enterprise')
        self.assertEqual(intent_data['enterprise_slug'], 'test-enterprise')
        self.assertEqual(intent_data['stripe_checkout_session_id'], 'cs_test_123abc')
        self.assertEqual(intent_data['admin_portal_url'], 'https://portal.edx.org/test-enterprise')

    def test_build_context_with_no_checkout_intent(self):
        """
        Test building a response when user has no checkout intent.
        """
        # Setup context without a checkout intent
        context = self._create_minimal_valid_context()
        context.checkout_intent = None

        # Create and build response
        builder = CheckoutContextResponseBuilder(context)
        builder.build()

        # Serialize to get final output
        data, status_code = builder.serialize()

        # Assertions
        self.assertEqual(status_code, status.HTTP_200_OK)
        self.assertIn('checkout_intent', data)
        self.assertIsNone(data['checkout_intent'])

    def test_build_context_with_paid_checkout_intent(self):
        """
        Test building a response with a checkout intent in PAID state.
        """
        # Setup context with a PAID checkout intent
        context = self._create_minimal_valid_context()
        paid_intent = self.mock_checkout_intent
        paid_intent['state'] = 'paid'
        context.checkout_intent = paid_intent

        # Create and build response
        builder = CheckoutContextResponseBuilder(context)
        builder.build()

        # Serialize to get final output
        data, status_code = builder.serialize()

        # Assertions
        self.assertEqual(status_code, status.HTTP_200_OK)
        self.assertIn('checkout_intent', data)
        self.assertEqual(data['checkout_intent']['state'], 'paid')

    def test_build_context_with_error_checkout_intent(self):
        """
        Test building a response with a checkout intent in error state.
        """
        # Setup context with a checkout intent that has errors
        context = self._create_minimal_valid_context()
        error_intent = self.mock_checkout_intent
        error_intent['state'] = 'errored_stripe_checkout'
        error_intent['last_checkout_error'] = 'Payment processing failed'
        context.checkout_intent = error_intent

        # Create and build response
        builder = CheckoutContextResponseBuilder(context)
        builder.build()

        # Serialize to get final output
        data, status_code = builder.serialize()

        # Assertions
        self.assertEqual(status_code, status.HTTP_200_OK)
        self.assertIn('checkout_intent', data)
        self.assertEqual(data['checkout_intent']['state'], 'errored_stripe_checkout')
        self.assertEqual(data['checkout_intent']['last_checkout_error'], 'Payment processing failed')


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


class TestCheckoutSuccessResponseBuilder(APITest):
    """Tests for the CheckoutSuccessResponseBuilder."""

    def setUp(self):
        """Set up test data before each test."""
        self.request_factory = RequestFactory()
        self.request = self.request_factory.post('/api/v1/bffs/checkout/context')
        self.request.user = self.user

        # Create a context
        self.context = CheckoutSuccessContext(self.request)
        # We don't really care about the values of these - that's covered
        # by the base checkout context BFF tests above. We just care that they're
        # included in the context and built response.
        self.context.existing_customers_for_enterprise_user = []
        self.context.pricing = {}
        self.context.field_constraints = {}

        # Sample checkout intent data with first_billable_invoice
        self.checkout_intent_data = {
            'uuid': str(uuid4()),
            'state': 'created',
            'enterprise_name': 'Test Enterprise',
            'enterprise_slug': 'test-enterprise',
            'stripe_checkout_session_id': 'cs_test_123',
            'last_checkout_error': '',
            'last_provisioning_error': '',
            'workflow_id': str(uuid4()),
            'expires_at': timezone.now().isoformat(),
            'admin_portal_url': 'https://portal.edx.org/test-enterprise',
            'first_billable_invoice': {
                'start_time': timezone.now().isoformat(),
                'end_time': timezone.now().isoformat(),
                'last4': 4242,
                'quantity': 35,
                'unit_amount_decimal': 396.00,
                'customer_phone': '+15551234567',
                'customer_name': 'Test Customer',
                'billing_address': {
                    'city': 'New York',
                    'country': 'US',
                    'line1': '123 Main St',
                    'line2': 'Apt 4B',
                    'postal_code': '10001',
                    'state': 'NY',
                },
            },
        }

        self.expected_sample_response_data = {
            'existing_customers_for_authenticated_user': self.context.existing_customers_for_authenticated_user,
            'pricing': self.context.pricing,
            'field_constraints': self.context.field_constraints,
            'checkout_intent': self.checkout_intent_data,
        }

        # Create the builder
        self.builder = CheckoutSuccessResponseBuilder(self.context)

    def test_serializer_class(self):
        """Test that the builder uses the correct serializer class."""
        self.assertEqual(self.builder.serializer_class, CheckoutSuccessResponseSerializer)

    def test_build_empty_context(self):
        """Test build with empty context."""
        # Context has no checkout_intent
        self.builder.build()

        # Response should be null in this case (which is allowed by the response serializer)
        self.assertIsNone(self.builder.response_data['checkout_intent'])

    def test_build_with_checkout_intent(self):
        """Test build with a populated checkout_intent in the context."""
        # Set the checkout_intent in the context
        self.context.checkout_intent = self.checkout_intent_data

        self.builder.build()

        # Response should contain the checkout_intent data, along with the
        # enterprise, pricing, and validation keys.
        self.assertEqual(self.builder.response_data, self.expected_sample_response_data)

    def test_build_with_partial_data(self):
        """Test build with partial checkout_intent data."""
        # Create partial data (no first_billable_invoice)
        partial_data = {
            'uuid': str(uuid4()),
            'state': 'created',
            'enterprise_name': 'Test Enterprise',
            'enterprise_slug': 'test-enterprise',
            'stripe_checkout_session_id': 'cs_test_123',
        }
        self.context.checkout_intent = partial_data

        self.builder.build()

        # Response should contain the partial data
        self.assertEqual(self.builder.response_data['checkout_intent'], partial_data)

    def test_null_first_billable_invoice(self):
        """Test build with null first_billable_invoice."""
        # Create data with null first_billable_invoice
        data_with_null = {**self.checkout_intent_data}
        data_with_null['first_billable_invoice'] = None

        self.context.checkout_intent = data_with_null

        self.builder.build()

        # Response should contain the data with null first_billable_invoice
        self.assertEqual(self.builder.response_data['checkout_intent'], data_with_null)
        self.assertIsNone(self.builder.response_data['checkout_intent']['first_billable_invoice'])

    def test_partial_first_billable_invoice(self):
        """Test build with partial first_billable_invoice data."""
        # Create data with partial first_billable_invoice
        data_with_partial = {**self.checkout_intent_data}
        data_with_partial['first_billable_invoice'] = {
            'last4': 4242,
            'quantity': 35,
            # Missing other fields
        }
        self.context.checkout_intent = data_with_partial

        self.builder.build()

        # Response should contain the data with partial first_billable_invoice
        self.assertEqual(self.builder.response_data['checkout_intent'], data_with_partial)
        self.assertEqual(self.builder.response_data['checkout_intent']['first_billable_invoice']['last4'], 4242)
        self.assertEqual(self.builder.response_data['checkout_intent']['first_billable_invoice']['quantity'], 35)
