"""
Tests for Checkout BFF handlers.
"""
import random
from datetime import datetime
from decimal import Decimal
from unittest import mock
from uuid import uuid4

import ddt
import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.utils import timezone
from pytz import UTC

from enterprise_access.apps.bffs.checkout.context import (
    CheckoutContext,
    CheckoutSuccessContext,
    CheckoutValidationContext
)
from enterprise_access.apps.bffs.checkout.handlers import (
    CheckoutContextHandler,
    CheckoutSuccessHandler,
    CheckoutValidationHandler
)
from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.models import CheckoutIntent
from test_utils import APITest

User = get_user_model()


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

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_ssp_product_pricing')
    @mock.patch('enterprise_access.apps.customer_billing.models.CheckoutIntent.objects.filter')
    def test_load_checkout_intent_for_authenticated_user(self, mock_filter, mock_get_pricing):
        """
        Test that load_and_process correctly adds checkout intent for authenticated users.
        """
        # Setup
        mock_get_pricing.return_value = {}
        mock_intent_data = {
            'state': 'created',
            'enterprise_name': 'Test Enterprise',
            'enterprise_slug': 'test-slug',
            'admin_portal_url': 'https://portal.edx.org/test-slug',
        }
        mock_intent = mock.MagicMock(**mock_intent_data)  # type: ignore
        mock_filter.return_value.first.return_value = mock_intent

        context = self._create_context()
        handler = CheckoutContextHandler(context)

        # Execute
        handler.load_and_process()

        # Assert
        self.assertEqual(context.checkout_intent, context.checkout_intent or {} | mock_intent_data)
        mock_filter.assert_called_once_with(user=self.user)

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_ssp_product_pricing')
    @mock.patch('enterprise_access.apps.customer_billing.models.CheckoutIntent.objects.filter')
    def test_load_checkout_intent_no_intent_exists(self, mock_filter, mock_get_pricing):
        """
        Test that load_and_process handles case where authenticated user has no checkout intent.
        """
        # Setup
        mock_get_pricing.return_value = {}
        mock_filter.return_value.first.return_value = None

        context = self._create_context()
        handler = CheckoutContextHandler(context)

        # Execute
        handler.load_and_process()

        # Assert
        self.assertIsNone(context.checkout_intent)
        mock_filter.assert_called_once_with(user=self.user)

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_ssp_product_pricing')
    @mock.patch('enterprise_access.apps.customer_billing.models.CheckoutIntent.objects.filter')
    def test_load_checkout_intent_for_unauthenticated_user(self, mock_filter, mock_get_pricing):
        """
        Test that load_and_process doesn't look for checkout intent for unauthenticated users.
        """
        # Setup
        mock_get_pricing.return_value = {}
        context = self._create_context(user=AnonymousUser())
        handler = CheckoutContextHandler(context)

        # Execute
        handler.load_and_process()

        # Assert
        self.assertIsNone(context.checkout_intent)
        mock_filter.assert_not_called()

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_ssp_product_pricing')
    @mock.patch('enterprise_access.apps.customer_billing.models.CheckoutIntent.objects.filter')
    def test_load_checkout_intent_error_handling(self, mock_filter, mock_get_pricing):
        """
        Test that load_and_process handles exceptions when fetching checkout intent.
        """
        # Setup
        mock_get_pricing.return_value = {}
        mock_filter.side_effect = Exception("Database error")

        context = self._create_context()
        handler = CheckoutContextHandler(context)

        # Execute
        handler.load_and_process()

        # Assert
        self.assertIsNone(context.checkout_intent)
        self.assertEqual(len(context.errors), 1)
        error_messages = [error.get('developer_message', '') for error in context.errors]
        self.assertTrue(any('database error' in msg.lower() for msg in error_messages))


class TestCheckoutValidationHandler(APITest):
    """
    Tests for the CheckoutValidationHandler.
    """

    def setUp(self):
        super().setUp()
        self.request_factory = RequestFactory()
        self.request = self.request_factory.post('/api/v1/bffs/checkout/validation')
        self.request.user = self.user

        # Create a context for testing
        self.context = CheckoutValidationContext(self.request)

        # Create an anonymous request/context for unauthenticated testing
        self.anon_request = self.request_factory.post('/api/v1/bffs/checkout/validation')
        self.anon_request.user = mock.MagicMock(is_authenticated=False)
        self.anon_context = CheckoutValidationContext(self.anon_request)

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.validate_free_trial_checkout_session')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.LmsApiClient')
    def test_load_and_process_authenticated(self, mock_lms_client_class, mock_validate):
        """
        Test load_and_process with an authenticated user.
        """
        # Setup mock responses
        mock_validate.return_value = {}

        # Setup request data
        self.request.data = {
            'admin_email': 'test@example.com',
            'full_name': 'Test User',
            'company_name': 'Test Company',
            'enterprise_slug': 'test-slug',
            'quantity': 10,
            'stripe_price_id': 'price_123'
        }

        # Create and process handler
        handler = CheckoutValidationHandler(self.context)
        handler.load_and_process()

        # Verify validate was called with all fields including enterprise_slug
        mock_validate.assert_called_once_with(
            user=self.user,
            admin_email='test@example.com',
            full_name='Test User',
            company_name='Test Company',
            enterprise_slug='test-slug',
            quantity=10,
            stripe_price_id='price_123'
        )

        # Check context was updated
        self.assertEqual(self.context.validation_decisions, {})
        self.assertIn('user_exists_for_email', self.context.user_authn)

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.validate_free_trial_checkout_session')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.LmsApiClient')
    def test_load_and_process_unauthenticated(self, mock_lms_client_class, mock_validate):
        """
        Test load_and_process with an unauthenticated user.
        """
        # Setup mock responses
        mock_validate.return_value = {}

        # Setup request data
        self.anon_request.data = {
            'admin_email': 'test@example.com',
            'full_name': 'Test User',
            'company_name': 'Test Company',
            'enterprise_slug': 'test-slug',  # This should be excluded for unauthenticated users
            'quantity': 10,
            'stripe_price_id': 'price_123'
        }

        # Create and process handler
        handler = CheckoutValidationHandler(self.anon_context)
        handler.load_and_process()

        # Verify validate was called without enterprise_slug
        mock_validate.assert_called_once_with(
            user=None,
            admin_email='test@example.com',
            full_name='Test User',
            company_name='Test Company',
            quantity=10,
            stripe_price_id='price_123'
        )

        # Check context was updated with authentication_required error for enterprise_slug
        self.assertIn('enterprise_slug', self.anon_context.validation_decisions)
        self.assertEqual(
            self.anon_context.validation_decisions['enterprise_slug']['error_code'],
            'authentication_required'
        )

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.validate_free_trial_checkout_session')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.LmsApiClient')
    def test_user_existence_check_success(self, mock_lms_client_class, mock_validate):
        """
        Test user existence check when user exists.
        """
        # Setup mock responses
        mock_validate.return_value = {}
        mock_lms_client = mock_lms_client_class.return_value
        mock_lms_client.get_lms_user_account.return_value = [{'id': 123}]

        # Setup request data
        self.request.data = {
            'admin_email': 'existing@example.com',
        }

        # Create and process handler
        handler = CheckoutValidationHandler(self.context)
        handler.load_and_process()

        # Verify user existence check was made
        mock_lms_client.get_lms_user_account.assert_called_once_with(email='existing@example.com')

        # Check context was updated
        self.assertTrue(self.context.user_authn['user_exists_for_email'])

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.validate_free_trial_checkout_session')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.LmsApiClient')
    def test_user_existence_check_not_found(self, mock_lms_client_class, mock_validate):
        """
        Test user existence check when user doesn't exist.
        """
        # Setup mock responses
        mock_validate.return_value = {}
        mock_lms_client = mock_lms_client_class.return_value
        mock_lms_client.get_lms_user_account.return_value = []  # Empty list means no user

        # Setup request data
        self.request.data = {
            'admin_email': 'nonexistent@example.com',
        }

        # Create and process handler
        handler = CheckoutValidationHandler(self.context)
        handler.load_and_process()

        # Verify user existence check was made
        mock_lms_client.get_lms_user_account.assert_called_once_with(email='nonexistent@example.com')

        # Check context was updated
        self.assertFalse(self.context.user_authn['user_exists_for_email'])

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.validate_free_trial_checkout_session')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.LmsApiClient')
    def test_user_existence_check_error(self, mock_lms_client_class, mock_validate):
        """
        Test user existence check when API call fails.
        """
        # Setup mock responses
        mock_validate.return_value = {}
        mock_lms_client = mock_lms_client_class.return_value
        mock_lms_client.get_lms_user_account.side_effect = Exception('API Error')

        # Setup request data
        self.request.data = {
            'admin_email': 'error@example.com',
        }

        # Create and process handler
        handler = CheckoutValidationHandler(self.context)
        handler.load_and_process()

        # Verify user existence check was attempted
        mock_lms_client.get_lms_user_account.assert_called_once_with(email='error@example.com')

        # Check context was updated with None (we don't know if user exists)
        self.assertIsNone(self.context.user_authn['user_exists_for_email'])

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.validate_free_trial_checkout_session')
    def test_validation_errors_propagated(self, mock_validate):
        """
        Test that validation errors are correctly propagated to the context.
        """
        # Setup mock responses
        mock_validate.return_value = {
            'company_name': {
                'error_code': 'existing_enterprise_customer',
                'developer_message': 'An enterprise customer with this name already exists.'
            },
            'quantity': {
                'error_code': 'range_exceeded',
                'developer_message': 'Quantity 50 exceeds allowed range [5, 30]'
            }
        }

        # Setup request data
        self.request.data = {
            'company_name': 'Existing Company',
            'quantity': 50,
        }

        # Create and process handler
        handler = CheckoutValidationHandler(self.context)
        handler.load_and_process()

        # Verify validation errors were passed to context
        self.assertEqual(len(self.context.validation_decisions), 2)
        self.assertEqual(
            self.context.validation_decisions['company_name']['error_code'],
            'existing_enterprise_customer'
        )
        self.assertEqual(
            self.context.validation_decisions['quantity']['error_code'],
            'range_exceeded'
        )

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.validate_free_trial_checkout_session')
    def test_no_admin_email(self, mock_validate):
        """
        Test handling when no admin_email is provided.
        """
        # Setup request data without admin_email
        self.request.data = {
            'full_name': 'Test User',
        }

        # Create and process handler
        handler = CheckoutValidationHandler(self.context)
        handler.load_and_process()

        # Check that user_exists_for_email is None
        self.assertIsNone(self.context.user_authn['user_exists_for_email'])


class TestCheckoutSuccessHandler(APITest):
    """Tests for the CheckoutSuccessHandler."""

    def setUp(self):
        """Set up test data before each test."""
        super().setUp()

        self.request_factory = RequestFactory()
        self.request = self.request_factory.post('/api/v1/bffs/checkout/success')
        self.request.user = self.user

        # Sample checkout intent data
        self.checkout_intent_data = {
            'id': random.randint(1, 10000),
            'state': 'created',
            'enterprise_name': 'Test Enterprise',
            'enterprise_slug': 'test-enterprise',
            'stripe_checkout_session_id': 'cs_test_123',
            'last_checkout_error': '',
            'last_provisioning_error': '',
            'workflow_id': str(uuid4()),
            'expires_at': timezone.now().isoformat(),
            'admin_portal_url': 'https://portal.edx.org/test-enterprise',
        }

        # Sample Stripe API responses
        self.stripe_session = {
            'id': 'cs_test_123',
            'payment_intent': 'pi_test_123',
            'subscription': 'sub_test_123',
        }

        self.stripe_payment_intent = {
            'id': 'pi_test_123',
            'payment_method': 'pm_test_123',
        }

        self.stripe_payment_method = {
            'id': 'pm_test_123',
            'card': {
                'last4': '4242',
            },
            'billing_details': {
                'address': {
                    'city': 'New York',
                    'country': 'US',
                    'line1': '123 Main St',
                    'line2': 'Apt 4B',
                    'postal_code': '10001',
                    'state': 'NY',
                }
            }
        }

        self.stripe_subscription = {
            'id': 'sub_test_123',
            'latest_invoice': 'in_test_123',
        }

        self.stripe_invoice = {
            'id': 'in_test_123',
            'customer': 'cus_test_123',
            'lines': {
                'data': [
                    {
                        'quantity': 35,
                        'price': {
                            'unit_amount_decimal': '39600',
                        },
                        'period': {
                            'start': 1657843200,  # 2022-07-15
                            'end': 1689379200,    # 2023-07-15
                        }
                    }
                ]
            }
        }

        self.stripe_customer = {
            'id': 'cus_test_123',
            'name': 'Test Customer',
            'phone': '+15551234567',
        }

        self.context = CheckoutSuccessContext(self.request)
        self.handler = CheckoutSuccessHandler(self.context)

    @mock.patch(
        'enterprise_access.apps.bffs.checkout.handlers.get_ssp_product_pricing',
        return_value={},
        autospec=True,
    )
    @mock.patch(
        'enterprise_access.apps.bffs.checkout.handlers.get_and_cache_enterprise_customer_users',
        return_value={},
        autospec=True,
    )
    @mock.patch(
        'enterprise_access.apps.bffs.checkout.handlers.CheckoutSuccessHandler._get_checkout_intent',
        return_value=None,
        autospec=True,
    )
    def test_load_and_process_no_checkout_intent(self, mock_get_checkout_intent, mock_get_customers, mock_get_price):
        """Test load_and_process when no checkout intent exists."""
        self.handler.load_and_process()

        self.assertIsNone(self.context.checkout_intent)
        self.assertEqual(mock_get_customers.call_count, 1)
        self.assertEqual(mock_get_price.call_count, 1)

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.CheckoutSuccessHandler._get_checkout_intent')
    def test_load_and_process_no_session_id(self, mock_get_checkout_intent):
        """Test load_and_process when no session ID exists."""
        checkout_intent_no_session = self.checkout_intent_data.copy()
        checkout_intent_no_session['stripe_checkout_session_id'] = ''
        mock_get_checkout_intent.return_value = checkout_intent_no_session

        self.handler.load_and_process()

        self.assertEqual(self.context.checkout_intent, checkout_intent_no_session)

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.CheckoutSuccessHandler._get_checkout_intent')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_checkout_session')
    def test_enhance_with_stripe_data_session_error(self, mock_session, mock_get_checkout_intent):
        """Test when there's an error retrieving the Stripe session."""
        mock_get_checkout_intent.return_value = self.checkout_intent_data
        mock_session.side_effect = stripe.error.StripeError("API Error")

        self.handler.load_and_process()

        self.assertEqual(self.context.checkout_intent, self.checkout_intent_data)

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.CheckoutSuccessHandler._get_checkout_intent')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_checkout_session')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_payment_intent')
    def test_enhance_with_stripe_data_payment_intent_error(
        self, mock_payment_intent, mock_session, mock_get_checkout_intent,
    ):
        """Test when there's an error retrieving the payment intent."""
        mock_get_checkout_intent.return_value = self.checkout_intent_data
        mock_session.return_value = self.stripe_session
        mock_payment_intent.side_effect = stripe.error.StripeError("API Error")

        self.handler.load_and_process()

        self.assertIn('first_billable_invoice', self.context.checkout_intent)
        self.assertIsNotNone(self.context.checkout_intent['first_billable_invoice'])
        self.assertIsNone(self.context.checkout_intent['first_billable_invoice']['last4'])

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.CheckoutSuccessHandler._get_checkout_intent')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_checkout_session')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_payment_intent')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_payment_method')
    def test_enhance_with_stripe_data_payment_method_error(
        self, mock_payment_method, mock_payment_intent, mock_session, mock_get_checkout_intent,
    ):
        """Test when there's an error retrieving the payment method."""
        mock_get_checkout_intent.return_value = self.checkout_intent_data
        mock_session.return_value = self.stripe_session
        mock_payment_intent.return_value = self.stripe_payment_intent
        mock_payment_method.side_effect = stripe.error.StripeError("API Error")

        self.handler.load_and_process()

        # Assert first_billable_invoice is initialized but not populated
        self.assertIn('first_billable_invoice', self.context.checkout_intent)
        self.assertIsNotNone(self.context.checkout_intent['first_billable_invoice'])
        self.assertIsNone(self.context.checkout_intent['first_billable_invoice']['last4'])

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.CheckoutSuccessHandler._get_checkout_intent')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_checkout_session')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_payment_intent')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_payment_method')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_subscription')
    def test_enhance_with_stripe_data_subscription_error(
        self, mock_subscription, mock_payment_method, mock_payment_intent, mock_session, mock_get_checkout_intent,
    ):
        """Test when there's an error retrieving the subscription."""
        mock_get_checkout_intent.return_value = self.checkout_intent_data
        mock_session.return_value = {**self.stripe_session, 'invoice': None}  # Force subscription path
        mock_payment_intent.return_value = self.stripe_payment_intent
        mock_payment_method.return_value = self.stripe_payment_method
        mock_subscription.side_effect = stripe.error.StripeError("API Error")

        self.handler.load_and_process()

        # Assert card data is populated but invoice data is not
        self.assertEqual(self.context.checkout_intent['first_billable_invoice']['last4'], '4242')
        self.assertIsNone(self.context.checkout_intent['first_billable_invoice']['quantity'])

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.CheckoutSuccessHandler._get_checkout_intent')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_checkout_session')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_payment_intent')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_payment_method')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_subscription')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_invoice')
    def test_enhance_with_stripe_data_invoice_error(
        self, mock_invoice, mock_subscription, mock_payment_method,
        mock_payment_intent, mock_session, mock_get_checkout_intent,
    ):
        """Test when there's an error retrieving the invoice."""
        mock_get_checkout_intent.return_value = self.checkout_intent_data
        mock_session.return_value = {**self.stripe_session, 'invoice': None}  # Force subscription path
        mock_payment_intent.return_value = self.stripe_payment_intent
        mock_payment_method.return_value = self.stripe_payment_method
        mock_subscription.return_value = self.stripe_subscription
        mock_invoice.side_effect = stripe.error.StripeError("API Error")

        self.handler.load_and_process()

        # Assert card data is populated but invoice data is not
        self.assertEqual(self.context.checkout_intent['first_billable_invoice']['last4'], '4242')
        self.assertIsNone(self.context.checkout_intent['first_billable_invoice']['quantity'])

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.CheckoutSuccessHandler._get_checkout_intent')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_checkout_session')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_payment_intent')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_payment_method')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_subscription')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_invoice')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_customer')
    def test_enhance_with_stripe_data_full_success(
        self, mock_customer, mock_invoice, mock_subscription, mock_payment_method,
        mock_payment_intent, mock_session, mock_get_checkout_intent,
    ):
        """Test full successful flow."""
        mock_get_checkout_intent.return_value = self.checkout_intent_data
        mock_session.return_value = {**self.stripe_session, 'invoice': None}  # Force subscription path
        mock_payment_intent.return_value = self.stripe_payment_intent
        mock_payment_method.return_value = self.stripe_payment_method
        mock_subscription.return_value = self.stripe_subscription
        mock_invoice.return_value = self.stripe_invoice
        mock_customer.return_value = self.stripe_customer

        self.handler.load_and_process()

        # Assert all data is populated correctly
        first_billable_invoice = self.context.checkout_intent['first_billable_invoice']
        self.assertEqual(first_billable_invoice['last4'], '4242')
        self.assertEqual(first_billable_invoice['quantity'], 35)
        self.assertEqual(first_billable_invoice['unit_amount_decimal'], 396.00)
        self.assertEqual(first_billable_invoice['customer_name'], 'Test Customer')
        self.assertEqual(first_billable_invoice['customer_phone'], '+15551234567')

        address = first_billable_invoice['billing_address']
        self.assertEqual(address['city'], 'New York')
        self.assertEqual(address['country'], 'US')
        self.assertEqual(address['line1'], '123 Main St')
        self.assertEqual(address['line2'], 'Apt 4B')
        self.assertEqual(address['postal_code'], '10001')
        self.assertEqual(address['state'], 'NY')

        # Check date fields (convert timestamps to expected datetime objects)
        expected_start = datetime.fromtimestamp(1657843200).replace(tzinfo=UTC)
        expected_end = datetime.fromtimestamp(1689379200).replace(tzinfo=UTC)
        self.assertEqual(first_billable_invoice['start_time'], expected_start)
        self.assertEqual(first_billable_invoice['end_time'], expected_end)

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.CheckoutSuccessHandler._get_checkout_intent')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_checkout_session')
    def test_no_payment_intent_in_session(self, mock_session, mock_get_checkout_intent):
        """Test when session has no payment intent."""
        mock_get_checkout_intent.return_value = self.checkout_intent_data
        session_no_payment = {**self.stripe_session}
        session_no_payment.pop('payment_intent')
        mock_session.return_value = session_no_payment

        self.handler.load_and_process()

        # Assert first_billable_invoice is initialized but empty
        self.assertIn('first_billable_invoice', self.context.checkout_intent)
        self.assertIsNone(self.context.checkout_intent['first_billable_invoice']['last4'])

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.CheckoutSuccessHandler._get_checkout_intent')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_checkout_session')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_payment_intent')
    def test_no_payment_method_in_payment_intent(self, mock_payment_intent, mock_session, mock_get_checkout_intent):
        """Test when payment intent has no payment method."""
        mock_get_checkout_intent.return_value = self.checkout_intent_data
        mock_session.return_value = self.stripe_session
        payment_intent_no_method = {**self.stripe_payment_intent}
        payment_intent_no_method.pop('payment_method')
        mock_payment_intent.return_value = payment_intent_no_method

        self.handler.load_and_process()

        # Assert first_billable_invoice is initialized but empty
        self.assertIn('first_billable_invoice', self.context.checkout_intent)
        self.assertIsNone(self.context.checkout_intent['first_billable_invoice']['last4'])

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.CheckoutSuccessHandler._get_checkout_intent')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_checkout_session')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_payment_intent')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_payment_method')
    def test_no_card_in_payment_method(
        self, mock_payment_method, mock_payment_intent, mock_session, mock_get_checkout_intent,
    ):
        """Test when payment method has no card info."""
        mock_get_checkout_intent.return_value = self.checkout_intent_data
        mock_session.return_value = self.stripe_session
        mock_payment_intent.return_value = self.stripe_payment_intent
        payment_method_no_card = {**self.stripe_payment_method}
        payment_method_no_card.pop('card')
        mock_payment_method.return_value = payment_method_no_card

        self.handler.load_and_process()

        self.assertIsNone(self.context.checkout_intent['first_billable_invoice']['last4'])

        # But address data is still populated
        address = self.context.checkout_intent['first_billable_invoice']['billing_address']
        self.assertEqual(address['city'], 'New York')

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.CheckoutSuccessHandler._get_checkout_intent')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_checkout_session')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_payment_intent')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_payment_method')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_invoice')
    def test_invoice_with_no_lines(
        self, mock_invoice, mock_payment_method, mock_payment_intent, mock_session, mock_get_checkout_intent,
    ):
        """Test when invoice has no line items."""
        mock_get_checkout_intent.return_value = self.checkout_intent_data
        mock_session.return_value = {**self.stripe_session, 'invoice': 'in_test_123'}  # Direct invoice path
        mock_payment_intent.return_value = self.stripe_payment_intent
        mock_payment_method.return_value = self.stripe_payment_method
        invoice_no_lines = {**self.stripe_invoice}
        invoice_no_lines.pop('lines')
        mock_invoice.return_value = invoice_no_lines

        self.handler.load_and_process()

        # Assert card data is populated but invoice data is not
        self.assertEqual(self.context.checkout_intent['first_billable_invoice']['last4'], '4242')
        self.assertIsNone(self.context.checkout_intent['first_billable_invoice']['quantity'])
        self.assertIsNone(self.context.checkout_intent['first_billable_invoice']['unit_amount_decimal'])
        self.assertIsNone(self.context.checkout_intent['first_billable_invoice']['start_time'])
        self.assertIsNone(self.context.checkout_intent['first_billable_invoice']['end_time'])
