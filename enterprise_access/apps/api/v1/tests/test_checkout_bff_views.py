"""
Tests for the Checkout BFF ViewSet.
"""
import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from pytz import UTC
from rest_framework import status

from enterprise_access.apps.api.v1.views.bffs.checkout import CheckoutBFFViewSet
from enterprise_access.apps.bffs.checkout.response_builder import CheckoutValidationResponseBuilder
from enterprise_access.apps.bffs.checkout.serializers import (
    CheckoutContextResponseSerializer,
    CheckoutIntentMinimalResponseSerializer,
    EnterpriseCustomerSerializer,
    PriceSerializer
)
from enterprise_access.apps.core.constants import SYSTEM_ENTERPRISE_LEARNER_ROLE
from enterprise_access.apps.customer_billing.constants import CheckoutIntentState
from enterprise_access.apps.customer_billing.models import CheckoutIntent
from test_utils import APITest


class CheckoutBFFViewSetTests(APITest):
    """
    Tests for the Checkout BFF ViewSet.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse('api:v1:checkout-bff-context')

        # Create a mock checkout intent we can use in tests
        self.mock_checkout_intent_data = {
            'id': 123,
            'state': 'created',
            'enterprise_name': 'Test Enterprise',
            'enterprise_slug': 'test-enterprise',
            'stripe_checkout_session_id': 'cs_test_123abc',
            'last_checkout_error': '',
            'last_provisioning_error': '',
            'workflow_id': None,
            'expires_at': '2025-08-02T13:52:11Z',
            'admin_portal_url': 'https://portal.edx.org/test-enterprise'
        }

    def test_context_endpoint_unauthenticated_access(self):
        """
        Test that unauthenticated users can access the context endpoint.
        """
        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify response structure matches our expectations
        self.assertIn('existing_customers_for_authenticated_user', response.data)
        self.assertIn('pricing', response.data)
        self.assertIn('field_constraints', response.data)

        # For unauthenticated users, existing_customers should be empty
        self.assertEqual(len(response.data['existing_customers_for_authenticated_user']), 0)
        # For unauthenticated users, checkout_intent should be None
        self.assertIsNone(response.data['checkout_intent'])

    @mock.patch('enterprise_access.apps.customer_billing.models.CheckoutIntent.objects.filter')
    def test_context_endpoint_authenticated_access(self, mock_filter):
        """
        Test that authenticated users can access the context endpoint.
        """
        # Set up a mock checkout intent for the authenticated user
        mock_intent = mock.MagicMock()
        for key, value in self.mock_checkout_intent_data.items():
            setattr(mock_intent, key, value)
        mock_filter.return_value.first.return_value = mock_intent

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify response structure matches our expectations
        self.assertIn('existing_customers_for_authenticated_user', response.data)
        self.assertIn('pricing', response.data)
        self.assertIn('field_constraints', response.data)

        # Verify checkout intent data is included
        self.assertIsNotNone(response.data['checkout_intent'])
        self.assertEqual(response.data['checkout_intent']['state'], 'created')
        self.assertEqual(response.data['checkout_intent']['enterprise_name'], 'Test Enterprise')

    def test_response_serializer_validation(self):
        """
        Test that our response serializer validates the expected response structure.
        """
        # Create sample data matching our expected response structure
        sample_data = {
            'existing_customers_for_authenticated_user': [],
            'pricing': {
                'default_by_lookup_key': 'b2b_enterprise_self_service_yearly',
                'prices': []
            },
            'field_constraints': {
                'quantity': {'min': 5, 'max': 30},
                'enterprise_slug': {
                    'min_length': 3,
                    'max_length': 30,
                    'pattern': '^[a-z0-9-]+$'
                }
            }
        }

        # Validate using our serializer
        serializer = CheckoutContextResponseSerializer(data=sample_data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_response_serializer_validation_with_intent(self):
        """
        Test that our response serializer validates response structure with checkout intent.
        """
        # Create sample data matching our expected response structure
        sample_data = {
            'existing_customers_for_authenticated_user': [],
            'pricing': {
                'default_by_lookup_key': 'b2b_enterprise_self_service_yearly',
                'prices': []
            },
            'field_constraints': {
                'quantity': {'min': 5, 'max': 30},
                'enterprise_slug': {
                    'min_length': 3,
                    'max_length': 30,
                    'pattern': '^[a-z0-9-]+$'
                }
            },
            'checkout_intent': self.mock_checkout_intent_data
        }

        # Validate using our serializer
        serializer = CheckoutContextResponseSerializer(data=sample_data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_response_serializer_validation_null_intent(self):
        """
        Test that our response serializer validates when checkout intent is null.
        """
        sample_data = {
            'existing_customers_for_authenticated_user': [],
            'pricing': {
                'default_by_lookup_key': 'b2b_enterprise_self_service_yearly',
                'prices': []
            },
            'field_constraints': {
                'quantity': {'min': 5, 'max': 30},
                'enterprise_slug': {
                    'min_length': 3,
                    'max_length': 30,
                    'pattern': '^[a-z0-9-]+$'
                }
            },
            'checkout_intent': None
        }

        serializer = CheckoutContextResponseSerializer(data=sample_data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_checkout_intent_minimal_serializer(self):
        """
        Test that CheckoutIntentMinimalResponseSerializer correctly validates data.
        """
        sample_data = {
            'id': 123,
            'state': 'paid',
            'enterprise_name': 'Test Enterprise',
            'enterprise_slug': 'test-enterprise',
            'stripe_checkout_session_id': 'cs_test_123abc',
            'expires_at': '2025-08-02T13:52:11Z',
            'admin_portal_url': 'https://portal.edx.org/test-enterprise'
        }

        serializer = CheckoutIntentMinimalResponseSerializer(data=sample_data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_enterprise_customer_serializer(self):
        """
        Test that EnterpriseCustomerSerializer correctly validates data.
        """
        sample_data = {
            'customer_uuid': 'abc123',
            'customer_name': 'Test Enterprise',
            'customer_slug': 'test-enterprise',
            'stripe_customer_id': 'cus_123ABC',
            'is_self_service': True,
            'admin_portal_url': 'http://whatever.com',
        }

        serializer = EnterpriseCustomerSerializer(data=sample_data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_price_serializer(self):
        """
        Test that PriceSerializer correctly validates data.
        """
        sample_data = {
            'id': 'price_123ABC',
            'product': 'prod_123ABC',
            'lookup_key': 'b2b_enterprise_self_service_yearly',
            'recurring': {
                'interval': 'month',
                'interval_count': 12,
                'trial_period_days': 14,
            },
            'currency': 'usd',
            'unit_amount': 10000,
            'unit_amount_decimal': '10000'
        }

        serializer = PriceSerializer(data=sample_data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_and_cache_enterprise_customer_users')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.transform_enterprise_customer_users_data')
    def test_authenticated_user_with_enterprise_customers(self, mock_transform, mock_get_customers):
        """
        Test that authenticated users get their enterprise customers in the response.
        """
        # Setup mocks to return enterprise customer data
        mock_get_customers.return_value = {'results': [{'enterprise_customer': {'uuid': 'test-uuid'}}]}
        mock_transform.return_value = {
            'all_linked_enterprise_customer_users': [
                {'enterprise_customer': {
                    'uuid': 'test-uuid',
                    'name': 'Test Enterprise',
                    'slug': 'test-enterprise',
                    'stripe_customer_id': 'cus_123ABC',
                    'is_self_service': True,
                }}
            ]
        }

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify that enterprise customers are in the response
        customers = response.data['existing_customers_for_authenticated_user']
        self.assertEqual(len(customers), 1)
        self.assertEqual(customers[0]['customer_uuid'], 'test-uuid')
        self.assertEqual(customers[0]['customer_name'], 'Test Enterprise')
        self.assertEqual(customers[0]['customer_slug'], 'test-enterprise')
        self.assertEqual(customers[0]['stripe_customer_id'], 'cus_123ABC')
        self.assertEqual(customers[0]['is_self_service'], True)
        self.assertEqual(
            customers[0]['admin_portal_url'],
            f'{settings.ENTERPRISE_ADMIN_PORTAL_URL}/test-enterprise',
        )

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_and_cache_enterprise_customer_users')
    def test_enterprise_api_error_handling(self, mock_get_customers):
        """
        Test that the API handles errors from enterprise customer APIs gracefully.
        """
        mock_get_customers.side_effect = Exception("API Error")

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        # Make the request - should not fail
        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify that the response still has the expected structure
        self.assertIn('existing_customers_for_authenticated_user', response.data)
        self.assertEqual(len(response.data['existing_customers_for_authenticated_user']), 0)

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_ssp_product_pricing')
    def test_pricing_api_error_handling(self, mock_get_pricing):
        """
        Test that the API handles errors from pricing APIs gracefully.
        """
        mock_get_pricing.side_effect = Exception("API Error")

        # Make the request - should not fail
        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify that the response still has pricing with empty prices
        self.assertIn('pricing', response.data)
        self.assertIn('default_by_lookup_key', response.data['pricing'])
        self.assertEqual(len(response.data['pricing']['prices']), 0)

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_ssp_product_pricing')
    def test_pricing_data_content(self, mock_get_pricing):
        """
        Test that pricing data is correctly formatted in the response.
        """
        mock_get_pricing.return_value = {
            'product1': {
                'id': 'price_123',
                'product': {'id': 'prod_123', 'active': True},
                'billing_scheme': 'per_unit',
                'type': 'recurring',
                'recurring': {'usage_type': 'licensed', 'interval': 'year', 'interval_count': 1},
                'currency': 'usd',
                'unit_amount': 10000,
                'unit_amount_decimal': '100.00',
                'lookup_key': 'test_key',
            }
        }

        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        pricing = response.data['pricing']
        self.assertIn('prices', pricing)
        self.assertEqual(len(pricing['prices']), 1)

        price = pricing['prices'][0]
        self.assertEqual(price['id'], 'price_123')
        self.assertEqual(price['product'], 'prod_123')
        self.assertEqual(price['lookup_key'], 'test_key')
        self.assertEqual(price['currency'], 'usd')
        self.assertEqual(price['unit_amount'], 10000)
        self.assertEqual(price['unit_amount_decimal'], '100.00')
        self.assertEqual(price['recurring']['interval'], 'year')
        self.assertEqual(price['recurring']['interval_count'], 1)


class TestCheckoutValidationBFF(APITest):
    """
    Tests for the CheckoutBFFViewSet.
    """

    def setUp(self):
        super().setUp()
        self.request_factory = RequestFactory()
        self.viewset = CheckoutBFFViewSet()
        self.validate_url = reverse('api:v1:checkout-bff-validate')

    def test_validate_authenticated_user(self):
        """
        Integration test for the validate endpoint.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        # Create request with test data
        request_data = {
            'full_name': 'Test User',
            'admin_email': 'test@example.com',
            'company_name': 'Test Company',
            'enterprise_slug': 'test-slug',
            'quantity': 10,
            'stripe_price_id': 'price_123'
        }

        with mock.patch(
            'enterprise_access.apps.bffs.checkout.handlers.validate_free_trial_checkout_session',
            return_value={},
        ) as mock_validate:
            # Setup mock for LmsApiClient
            with mock.patch('enterprise_access.apps.bffs.checkout.handlers.LmsApiClient') as mock_lms_client_class:
                mock_lms_client = mock_lms_client_class.return_value
                mock_lms_client.get_lms_user_account.return_value = [{'id': 123}]

                response = self.client.post(
                    self.validate_url,
                    data=json.dumps(request_data),
                    content_type='application/json',
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                content = json.loads(response.content.decode('utf-8'))

                self.assertIn('validation_decisions', content)
                self.assertIn('user_authn', content)

                for field in CheckoutValidationResponseBuilder.ALL_VALIDATION_FIELDS:
                    self.assertIn(field, content['validation_decisions'])

                # Check user existence check was performed
                mock_lms_client.get_lms_user_account.assert_called_once_with(email='test@example.com')

            mock_validate.assert_called_once_with(
                user=self.user,
                full_name='Test User',
                admin_email='test@example.com',
                company_name='Test Company',
                enterprise_slug='test-slug',
                quantity=10,
                stripe_price_id='price_123',
            )

    def test_validate_unauthenticated_with_enterprise_slug(self):
        """
        Test validate endpoint with unauthenticated request and enterprise_slug.
        Enterprise slug validation should require authentication.
        """
        request_data = {
            'enterprise_slug': 'test-slug',
        }

        with mock.patch(
            'enterprise_access.apps.bffs.checkout.handlers.validate_free_trial_checkout_session'
        ) as mock_validate:
            response = self.client.post(
                self.validate_url,
                data=json.dumps(request_data),
                content_type='application/json'
            )

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            content = json.loads(response.content.decode('utf-8'))

            # Check enterprise_slug has authentication_required error
            self.assertEqual(
                content['validation_decisions']['enterprise_slug']['error_code'],
                'authentication_required'
            )
            self.assertFalse(mock_validate.called)

    def test_validate_with_validation_errors(self):
        """
        Test validate endpoint when validation errors occur.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])
        request_data = {
            'company_name': 'Existing Company',
            'quantity': 50,  # Above allowed range
        }

        with mock.patch(
            'enterprise_access.apps.bffs.checkout.handlers.validate_free_trial_checkout_session'
        ) as mock_validate:
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

            response = self.client.post(
                self.validate_url,
                data=json.dumps(request_data),
                content_type='application/json',
            )

            # Verify response status is still 200 OK (validation errors are expected)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            content = json.loads(response.content.decode('utf-8'))

            # Check validation errors in response
            validation_decisions = content['validation_decisions']
            self.assertEqual(validation_decisions['company_name']['error_code'], 'existing_enterprise_customer')
            self.assertEqual(validation_decisions['quantity']['error_code'], 'range_exceeded')


class CheckoutBFFSuccessViewSetTests(APITest):
    """Tests for the CheckoutBFFViewSet success action."""

    url = reverse('api:v1:checkout-bff-success')

    def setUp(self):
        """Set up test data before each test."""
        super().setUp()

        # Sample checkout intent data
        self.checkout_intent_data = {
            'uuid': str(uuid.uuid4()),
            'state': 'created',
            'enterprise_name': 'Test Enterprise',
            'enterprise_slug': 'test-enterprise',
            'stripe_checkout_session_id': 'cs_test_123',
            'last_checkout_error': '',
            'last_provisioning_error': '',
            'workflow_id': str(uuid.uuid4()),
            'expires_at': datetime.now(tz=UTC).isoformat(),
            'admin_portal_url': 'https://portal.edx.org/test-enterprise',
            'first_billable_invoice': {
                'start_time': datetime.now(tz=UTC).isoformat(),
                'end_time': datetime.now(tz=UTC).isoformat(),
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

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

    def test_success_unauthenticated(self):
        """Test that unauthenticated users get a 401."""
        # Clear authentication
        self.client.cookies.clear()

        response = self.client.post(self.url, {})

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_ssp_product_pricing')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_and_cache_enterprise_customer_users')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.CheckoutSuccessHandler')
    @mock.patch('enterprise_access.apps.bffs.checkout.context.CheckoutSuccessContext')
    def test_success_endpoint_empty_response(  # pylint: disable=unused-argument
        self, mock_context_class, mock_handler_class, mock_get_customers, mock_get_pricing,
    ):
        """Test success endpoint when no checkout intent is found."""
        mock_context = mock.MagicMock()
        mock_context_class.return_value = mock_context
        mock_handler = mock.MagicMock()
        mock_handler_class.return_value = mock_handler
        mock_handler.load_and_process.return_value = None

        mock_context.checkout_intent = {}

        response = self.client.post(self.url, {})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.json()['checkout_intent'])

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_ssp_product_pricing')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_and_cache_enterprise_customer_users')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.CheckoutSuccessHandler._get_checkout_intent')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.CheckoutSuccessHandler.enhance_with_stripe_data')
    def test_success_endpoint_handler_exception(  # pylint: disable=unused-argument
        self, mock_enhance, mock_get_checkout_intent, mock_get_customers, mock_get_pricing,
    ):
        """Test success endpoint when handler raises an exception."""
        mock_enhance.side_effect = Exception("Special Handler error")
        mock_get_checkout_intent.return_value = self.checkout_intent_data

        response = self.client.post(self.url, {})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("Special Handler error", str(response.json()['errors']))

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.CheckoutIntent.for_user')
    def test_success_endpoint_no_checkout_session(self, mock_for_user):
        """Test the success endpoint when no checkout session id is present."""
        mock_checkout_intent = CheckoutIntent.objects.create(
            user_id=self.user.id,
            state=CheckoutIntentState.CREATED,
            quantity=10,
            enterprise_name='Test Enterprise',
            enterprise_slug='test-enterprise',
            stripe_checkout_session_id=None,
            last_checkout_error='',
            last_provisioning_error='',
            expires_at=timezone.now() + timedelta(hours=4),
        )
        mock_for_user.return_value = mock_checkout_intent

        response = self.client.post(self.url, {})

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Response should include basic fields from the checkout intent
        response_data = response.json()['checkout_intent']
        self.assertEqual(response_data['id'], mock_checkout_intent.id)
        self.assertEqual(response_data['state'], mock_checkout_intent.state)
        self.assertEqual(response_data['enterprise_name'], mock_checkout_intent.enterprise_name)

        # first_billable_invoice key should be present but valued with null
        self.assertIsNone(response_data['first_billable_invoice'])

    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_ssp_product_pricing')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_and_cache_enterprise_customer_users')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_checkout_session')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_payment_intent')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_payment_method')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_subscription')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_invoice')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.get_stripe_customer')
    @mock.patch('enterprise_access.apps.bffs.checkout.handlers.CheckoutIntent.for_user')
    def test_success_endpoint_full_stripe_data(  # pylint: disable=unused-argument
        self, mock_for_user, mock_customer, mock_invoice,
        mock_subscription, mock_payment_method,
        mock_payment_intent, mock_session,
        mock_get_customer, mock_get_pricing,
    ):
        """Test the success endpoint with full Stripe data integration."""
        mock_checkout_intent = CheckoutIntent.objects.create(
            user_id=self.user.id,
            state=CheckoutIntentState.CREATED,
            quantity=10,
            enterprise_name='Test Enterprise',
            enterprise_slug='test-enterprise',
            stripe_checkout_session_id='cs_test_123',
            last_checkout_error='',
            last_provisioning_error='',
            expires_at=timezone.now() + timedelta(hours=4),
        )
        mock_for_user.return_value = mock_checkout_intent

        # Setup mock Stripe API responses
        mock_session.return_value = {
            'id': 'cs_test_123',
            'payment_intent': 'pi_test_123',
            'subscription': 'sub_test_123',
        }

        mock_payment_intent.return_value = {
            'id': 'pi_test_123',
            'payment_method': 'pm_test_123',
        }

        mock_payment_method.return_value = {
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

        mock_subscription.return_value = {
            'id': 'sub_test_123',
            'latest_invoice': 'in_test_123',
        }

        mock_invoice.return_value = {
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
                            'start': int(datetime.now().timestamp()),
                            'end': int(datetime.now().timestamp()) + 31536000,  # +1 year
                        }
                    }
                ]
            }
        }

        mock_customer.return_value = {
            'id': 'cus_test_123',
            'name': 'Test Customer',
            'phone': '+15551234567',
        }

        response = self.client.post(self.url, {})

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response_data = response.json()['checkout_intent']
        self.assertEqual(response_data['id'], mock_checkout_intent.id)
        self.assertEqual(response_data['state'], mock_checkout_intent.state)

        invoice = response_data['first_billable_invoice']
        self.assertEqual(invoice['last4'], 4242)
        self.assertEqual(invoice['quantity'], 35)
        self.assertEqual(Decimal(invoice['unit_amount_decimal']), 396.00)
        self.assertEqual(invoice['customer_name'], 'Test Customer')
        self.assertEqual(invoice['customer_phone'], '+15551234567')

        address = invoice['billing_address']
        self.assertEqual(address['city'], 'New York')
        self.assertEqual(address['country'], 'US')
        self.assertEqual(address['line1'], '123 Main St')
