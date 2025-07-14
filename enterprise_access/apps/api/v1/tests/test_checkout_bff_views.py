"""
Tests for the Checkout BFF ViewSet.
"""
import uuid

from django.urls import reverse
from rest_framework import status

from enterprise_access.apps.api.serializers.checkout_bff import (
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
        # TODO: remove
        self.assertIsNone(response.data['user_id'])

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
        # TODO: remove
        self.assertEqual(response.data['user_id'], self.user.id)

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
            'admin_portal_url': 'https://example.com/enterprise/test-enterprise'
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
