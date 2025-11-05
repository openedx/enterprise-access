"""
Tests for StripEventSummary viewset.
"""
import uuid
from datetime import timedelta
from urllib.parse import urlencode

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
        self.subscription_plan_uuid = str(uuid.uuid4())
        self.subscription_plan_uuid_2 = str(uuid.uuid4())

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

        # manually populating subscription_plan_uuid
        summary1 = StripeEventSummary.objects.get(event_id="evt_test_invoice")
        summary1.subscription_plan_uuid = self.subscription_plan_uuid
        summary1.save()

        summary2 = StripeEventSummary.objects.get(event_id="evt_test_invoice_2")
        summary2.subscription_plan_uuid = self.subscription_plan_uuid_2
        summary2.save()

    def tearDown(self):
        StripeEventSummary.objects.all().delete()
        super().tearDown()

    def test_get_stripe_event_summary_no_authorization(self):
        """
        Successful retrieval of StripeEventSummary object
        """
        url = reverse('api:v1:stripe-event-summary-list')
        response = self.client.get(url)
        assert response.status_code == 401

    def test_get_stripe_event_summary_by_subscription_uuid(self):
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': self.enterprise_uuid,  # implicit access to this enterprise
        }])

        query_params = {
            'subscription_plan_uuid': self.subscription_plan_uuid,
        }
        url = reverse('api:v1:stripe-event-summary-list')
        url += f"?{urlencode(query_params)}"

        response = self.client.get(url)
        assert response.status_code == 200
        assert response.data['count'] == 1
        results = response.data['results']
        assert results[0]['subscription_plan_uuid'] == self.subscription_plan_uuid

    def test_get_stripe_event_summary_by_subscription_uuid_no_auth(self):
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': self.enterprise_uuid_2,  # access to a different enterprise than the sub plan
        }])

        query_params = {
            'subscription_plan_uuid': self.subscription_plan_uuid,
        }
        url = reverse('api:v1:stripe-event-summary-list')
        url += f"?{urlencode(query_params)}"

        response = self.client.get(url)
        # trying to fetch a subscription plan for an enterprise customer they are not associated with
        assert response.status_code == 403


class StripeEventUpcomingInvoiceAmountDueTests(APITest):
    """
    Tests for first_upcoming_invoice_amount_due endpoint.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.enterprise_uuid = str(uuid.uuid4())
        self.stripe_customer_id = 'cus_test_123'
        self.subscription_plan_uuid = str(uuid.uuid4())

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

        subscription_event_data = {
            'id': 'evt_test_sub_created',
            'type': 'customer.subscription.created',
            'data': {
                'object': {
                    'object': 'subscription',
                    'id': 'sub_test_789',
                    'status': 'active',
                    'items': {
                        'data': [
                            {
                                'object': 'subscription_item',
                                'current_period_start': 1609459200,  # 2021-01-01 00:00:00 UTC
                                'current_period_end': 1640995200,    # 2022-01-01 00:00:00 UTC
                            }
                        ]
                    },
                }
            },
            'metadata': {
                'checkout_intent_id': self.checkout_intent.id,
                'enterprise_customer_name': 'Test Enterprise',
                'enterprise_customer_slug': 'test-enterprise',
            }
        }

        # Creating a StripeEventData record triggers a create of related StripeEventSummary
        self.stripe_event_data = StripeEventData.objects.create(
            event_id='evt_test_subscription',
            event_type='customer.subscription.created',
            checkout_intent=self.checkout_intent,
            data=subscription_event_data,
        )

        test_summary = StripeEventSummary.objects.filter(event_id='evt_test_subscription').first()
        test_summary.upcoming_invoice_amount_due = 200
        test_summary.subscription_plan_uuid = self.subscription_plan_uuid
        test_summary.save(update_fields=['upcoming_invoice_amount_due', 'subscription_plan_uuid'])

    def test_get_first_upcoming_invoice_amount_due(self):
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': self.enterprise_uuid,  # implicit access to this enterprise
        }])

        query_params = {
            'subscription_plan_uuid': self.subscription_plan_uuid,
        }

        url = reverse('api:v1:stripe-event-summary-first-upcoming-invoice-amount-due')
        url += f"?{urlencode(query_params)}"
        response = self.client.get(url)
        assert response.status_code == 200
        assert response.data == {'upcoming_invoice_amount_due': 200}
