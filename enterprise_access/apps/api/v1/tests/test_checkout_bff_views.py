"""
Tests for the Checkout BFF ViewSet.
"""
import uuid
from unittest import mock

from django.conf import settings
from django.urls import reverse
from rest_framework import status

from enterprise_access.apps.bffs.checkout.serializers import (
    CheckoutContextResponseSerializer,
    EnterpriseCustomerSerializer,
    PriceSerializer
)
from enterprise_access.apps.core.constants import SYSTEM_ENTERPRISE_LEARNER_ROLE
from test_utils import APITest


class CheckoutBFFViewSetTests(APITest):
    """
    Tests for the Checkout BFF ViewSet.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse('api:v1:checkout-bff-context')

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

    def test_context_endpoint_authenticated_access(self):
        """
        Test that authenticated users can access the context endpoint.
        """
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

        # Authenticate the user
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        # Make the request
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
        # Make the API throw an exception
        mock_get_customers.side_effect = Exception("API Error")

        # Authenticate the user
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
        # Make the API throw an exception
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
        # Setup mock to return pricing data
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

        # Make the request
        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify the pricing data
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
