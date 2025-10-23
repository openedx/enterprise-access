"""
Tests for StripEventSummary viewset.
"""
import uuid
from datetime import timedelta
from unittest import mock

from django.urls import reverse
from django.utils import timezone

from enterprise_access.apps.core.constants import SYSTEM_ENTERPRISE_ADMIN_ROLE
from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.constants import CheckoutIntentState
from enterprise_access.apps.customer_billing.models import CheckoutIntent, StripeEventData, StripeEventSummary
from test_utils import APITest


class StripeEventSummaryTests(APITest):
    """
    Tests for StripeEventSummary endpoints.
    """

    def setUp(self):
        super().setUp()
        self.user_2 = UserFactory()
        self.enterprise_uuid = str(uuid.uuid4())
        self.enterprise_uuid_2 = str(uuid.uuid4())
        self.stripe_customer_id = 'cus_test_123'
        self.stripe_customer_id_2 = 'cus_test_321'

        invoice_event_data = {
            'id': 'evt_test_invoice',
            'type': 'invoice.paid',
            'data': {
                'object': {
                    'object': 'invoice',
                    'id': 'in_test_123',
                    'subscription': 'sub_test_123',
                    'amount_paid': 2500,  # $25.00 in cents
                    'currency': 'usd',
                    'lines': {
                        'data': [
                            {
                                'quantity': 10,
                                'pricing': {
                                    'unit_amount_decimal': '250.0'  # $2.50 per unit
                                }
                            }
                        ]
                    },
                    'parent': {
                        'subscription_details': {
                            'subscription': 'sub_test_123'
                        }
                    }
                }
            }
        }

        invoice_event_data_2 = {
            'id': 'evt_test_invoice',
            'type': 'invoice.paid',
            'data': {
                'object': {
                    'object': 'invoice',
                    'id': 'in_test_123',
                    'subscription': 'sub_test_456',
                    'amount_paid': 2500,  # $25.00 in cents
                    'currency': 'usd',
                    'lines': {
                        'data': [
                            {
                                'quantity': 20,
                                'pricing': {
                                    'unit_amount_decimal': '250.0'  # $2.50 per unit
                                }
                            }
                        ]
                    },
                    'parent': {
                        'subscription_details': {
                            'subscription': 'sub_test_456'
                        }
                    }
                }
            }
        }

        self.checkout_intent = CheckoutIntent.objects.create(
            user=self.user,
            enterprise_uuid=self.enterprise_uuid,
            enterprise_name='Test Enterprise',
            enterprise_slug='test-enterprise',
            stripe_customer_id=self.stripe_customer_id,
            state=CheckoutIntentState.PAID,
            quantity=10,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        self.checkout_intent_2 = CheckoutIntent.objects.create(
            user=self.user_2,
            enterprise_uuid=self.enterprise_uuid_2,
            enterprise_name='Test Enterprise 2',
            enterprise_slug='test-enterprise-2',
            stripe_customer_id=self.stripe_customer_id_2,
            state=CheckoutIntentState.PAID,
            quantity=20,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        # Create two StripeEventData objects, will each create StripeEventSummary on create
        self.stripe_event_data = StripeEventData.objects.create(
            event_id='evt_test_invoice',
            event_type='invoice.paid',
            checkout_intent=self.checkout_intent,
            data=invoice_event_data,
        )
        self.stripe_event_data_2 = StripeEventData.objects.create(
            event_id='evt_test_invoice_2',
            event_type='invoice.paid',
            checkout_intent=self.checkout_intent_2,
            data=invoice_event_data_2,
        )

    def tearDown(self):
        CheckoutIntent.objects.all().delete()
        StripeEventData.objects.all().delete()
        StripeEventSummary.objects.all().delete()
        super().tearDown()

    def test_get_stripe_event_summary_no_authorization(self):
        """
        Successful retrieval of StripeEventSummary object
        """
        url = reverse('api:v1:stripe-event-summary-list')
        response = self.client.get(url)
        assert response.status_code == 401

    def test_get_stripe_event_summary_list(self):
        """
        Successful retrieval of StripeEventSummary object
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': self.enterprise_uuid,  # implicit access to this enterprise
        }])

        url = reverse('api:v1:stripe-event-summary-list')
        response = self.client.get(url)
        assert response.status_code == 200
        assert response.data['count'] == 2

    def test_get_stripe_event_summary_single(self):
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': self.enterprise_uuid,  # implicit access to this enterprise
        }])

        url = reverse("api:v1:stripe-event-summary-detail", args=['sub_test_123'])
        response = self.client.get(url)
        assert response.status_code == 200
        assert response.data['invoice_quantity'] == 10
