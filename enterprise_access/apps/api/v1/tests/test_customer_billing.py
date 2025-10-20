"""
Tests for customer billing API endpoints.
"""
import json
import uuid
from datetime import timedelta
from unittest import mock

import stripe
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from enterprise_access.apps.core.constants import SYSTEM_ENTERPRISE_ADMIN_ROLE, SYSTEM_ENTERPRISE_LEARNER_ROLE
from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.constants import CheckoutIntentState
from enterprise_access.apps.customer_billing.models import CheckoutIntent
from test_utils import APITest


class CustomerBillingPortalSessionTests(APITest):
    """
    Tests for CustomerBillingPortalSession endpoints.
    """

    def setUp(self):
        super().setUp()
        self.enterprise_uuid = str(uuid.uuid4())
        self.stripe_customer_id = 'cus_test_123'

        # Create a checkout intent for testing
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

    def tearDown(self):
        CheckoutIntent.objects.all().delete()
        super().tearDown()

    def test_create_enterprise_admin_portal_session_success(self):
        """
        Successful creation of enterprise admin portal session.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': self.enterprise_uuid,  # implicit access to this enterprise
        }])

        url = reverse('api:v1:customer-billing-create-enterprise-admin-portal-session')

        mock_session = {
            'id': 'bps_test_123',
            'url': 'https://billing.stripe.com/session/test_123',
            'customer': self.stripe_customer_id,
        }

        with mock.patch('stripe.billing_portal.Session.create') as mock_create:
            mock_create.return_value = mock_session

            response = self.client.get(
                url,
                {'enterprise_customer_uuid': self.enterprise_uuid},
                HTTP_ORIGIN='https://admin.example.com'
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, mock_session)

        # Implementation uses /{enterprise_slug} for Admin portal return URL.
        mock_create.assert_called_once_with(
            customer=self.stripe_customer_id,
            return_url='https://admin.example.com/test-enterprise',
        )

    def test_create_enterprise_admin_portal_session_missing_uuid(self):
        """
        Without enterprise_customer_uuid, RBAC blocks at the decorator -> 403.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': self.enterprise_uuid,
        }])

        url = reverse('api:v1:customer-billing-create-enterprise-admin-portal-session')

        response = self.client.get(url)

        # Permission layer rejects because fn(...) yields None context.
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_enterprise_admin_portal_session_no_checkout_intent(self):
        """
        RBAC passes (user has implicit access to provided UUID), view returns 404 when no intent exists.
        """
        non_existent_uuid = str(uuid.uuid4())
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': non_existent_uuid,
        }])

        url = reverse('api:v1:customer-billing-create-enterprise-admin-portal-session')

        response = self.client.get(
            url,
            {'enterprise_customer_uuid': non_existent_uuid}
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_enterprise_admin_portal_session_no_stripe_customer(self):
        """
        If the CheckoutIntent has no Stripe customer ID, Stripe call will error → 422.
        """
        other_user = UserFactory()
        checkout_intent_no_stripe = CheckoutIntent.objects.create(
            user=other_user,
            enterprise_uuid=str(uuid.uuid4()),
            enterprise_name='Test Enterprise 2',
            enterprise_slug='test-enterprise-2',
            stripe_customer_id=None,
            state=CheckoutIntentState.CREATED,
            quantity=5,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': checkout_intent_no_stripe.enterprise_uuid,
        }])

        url = reverse('api:v1:customer-billing-create-enterprise-admin-portal-session')

        with mock.patch('stripe.billing_portal.Session.create') as mock_create:
            mock_create.side_effect = stripe.InvalidRequestError(
                'Customer does not exist',
                'customer'
            )
            response = self.client.get(
                url,
                {'enterprise_customer_uuid': checkout_intent_no_stripe.enterprise_uuid},
                HTTP_ORIGIN='https://admin.example.com'
            )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_create_enterprise_admin_portal_session_stripe_error(self):
        """
        Stripe API returns an error → 422.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': self.enterprise_uuid,
        }])

        url = reverse('api:v1:customer-billing-create-enterprise-admin-portal-session')

        with mock.patch('stripe.billing_portal.Session.create') as mock_create:
            mock_create.side_effect = stripe.InvalidRequestError(
                'Customer does not exist',
                'customer'
            )

            response = self.client.get(
                url,
                {'enterprise_customer_uuid': self.enterprise_uuid},
                HTTP_ORIGIN='https://admin.example.com'
            )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_create_enterprise_admin_portal_session_authentication_required(self):
        """
        Authentication required for enterprise admin portal session.
        """
        url = reverse('api:v1:customer-billing-create-enterprise-admin-portal-session')

        response = self.client.get(
            url,
            {'enterprise_customer_uuid': self.enterprise_uuid}
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_enterprise_admin_portal_session_permission_required(self):
        """
        User with learner role only should be forbidden by RBAC.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': self.enterprise_uuid,
        }])

        url = reverse('api:v1:customer-billing-create-enterprise-admin-portal-session')

        response = self.client.get(
            url,
            {'enterprise_customer_uuid': self.enterprise_uuid}
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_checkout_portal_session_success(self):
        """
        Successful creation of checkout portal session.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        url = reverse('api:v1:customer-billing-create-checkout-portal-session',
                      kwargs={'pk': self.checkout_intent.id})

        mock_session = {
            'id': 'bps_test_456',
            'url': 'https://billing.stripe.com/session/test_456',
            'customer': self.stripe_customer_id,
        }

        with mock.patch('stripe.billing_portal.Session.create') as mock_create:
            mock_create.return_value = mock_session

            response = self.client.get(
                url,
                HTTP_ORIGIN='https://checkout.example.com'
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, mock_session)

        mock_create.assert_called_once_with(
            customer=self.stripe_customer_id,
            return_url='https://checkout.example.com/billing-details/success',
        )

    def test_create_checkout_portal_session_wrong_user(self):
        """
        Wrong user (permission class denies) → 403.
        """
        other_user = UserFactory()
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }], user=other_user)

        url = reverse('api:v1:customer-billing-create-checkout-portal-session',
                      kwargs={'pk': self.checkout_intent.id})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_checkout_portal_session_nonexistent_intent(self):
        """
        Permission class denies before view (no intent for pk) → 403.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        url = reverse('api:v1:customer-billing-create-checkout-portal-session',
                      kwargs={'pk': 99999})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_checkout_portal_session_no_stripe_customer(self):
        """
        No Stripe customer on the CheckoutIntent → 404 (from view).
        """
        other_user = UserFactory()
        checkout_intent_no_stripe = CheckoutIntent.objects.create(
            user=other_user,
            enterprise_uuid=str(uuid.uuid4()),
            enterprise_name='Test Enterprise 3',
            enterprise_slug='test-enterprise-3',
            stripe_customer_id=None,
            state=CheckoutIntentState.CREATED,
            quantity=5,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }], user=other_user)

        url = reverse('api:v1:customer-billing-create-checkout-portal-session',
                      kwargs={'pk': checkout_intent_no_stripe.id})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_checkout_portal_session_stripe_error(self):
        """
        Stripe API error → 422.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        url = reverse('api:v1:customer-billing-create-checkout-portal-session',
                      kwargs={'pk': self.checkout_intent.id})

        with mock.patch('stripe.billing_portal.Session.create') as mock_create:
            mock_create.side_effect = stripe.AuthenticationError('Invalid API key')

            response = self.client.get(
                url,
                HTTP_ORIGIN='https://checkout.example.com'
            )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_create_checkout_portal_session_authentication_required(self):
        """
        Authentication required for checkout portal session.
        """
        url = reverse('api:v1:customer-billing-create-checkout-portal-session',
                      kwargs={'pk': self.checkout_intent.id})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class StripeWebhookTests(APITest):
    """
    Tests for Stripe webhook endpoint with new authentication.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse('api:v1:customer-billing-stripe-webhook')
        self.valid_event_payload = json.dumps({
            'id': 'evt_test_webhook',
            'object': 'event',
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'id': 'cs_test_123',
                }
            }
        })

    def _post_webhook_with_signature(self, payload, signature):
        """Helper to POST webhook data with signature header."""
        return self.client.post(
            self.url,
            data=payload,
            content_type='application/json',
            HTTP_STRIPE_SIGNATURE=signature,
        )

    @override_settings(STRIPE_WEBHOOK_ENDPOINT_SECRET='whsec_test_secret')
    @mock.patch('enterprise_access.apps.customer_billing.stripe_event_handlers.StripeEventHandler.dispatch')
    @mock.patch('stripe.Webhook.construct_event')
    def test_webhook_success_with_valid_signature(self, mock_construct_event, mock_dispatch):
        """
        Test webhook endpoint succeeds with valid Stripe signature.
        """
        mock_event = {'id': 'evt_test', 'type': 'checkout.session.completed'}
        mock_construct_event.return_value = mock_event

        response = self._post_webhook_with_signature(
            self.valid_event_payload,
            't=1234567890,v1=valid_signature'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_dispatch.assert_called_once_with(mock_event)

    @override_settings(STRIPE_WEBHOOK_ENDPOINT_SECRET='whsec_test_secret')
    @mock.patch('stripe.Webhook.construct_event')
    def test_webhook_fails_with_invalid_signature(self, mock_construct_event):
        """
        Test webhook endpoint fails with invalid signature.
        """
        mock_construct_event.side_effect = stripe.SignatureVerificationError(
            'Invalid signature',
            'sig_header'
        )

        response = self._post_webhook_with_signature(
            self.valid_event_payload,
            't=1234567890,v1=invalid_signature'
        )

        # Authentication failure returns 403
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_webhook_fails_without_signature_header(self):
        """
        Test webhook endpoint fails when signature header is missing.
        """
        response = self.client.post(
            self.url,
            data=self.valid_event_payload,
            content_type='application/json',
        )

        # Missing signature header causes authentication failure (403)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(STRIPE_WEBHOOK_ENDPOINT_SECRET=None)
    def test_webhook_fails_without_secret_configured(self):
        """
        Test webhook endpoint fails when secret is not configured.
        """
        response = self._post_webhook_with_signature(
            self.valid_event_payload,
            't=1234567890,v1=signature'
        )

        # Missing configuration causes authentication failure (403)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(STRIPE_WEBHOOK_ENDPOINT_SECRET='whsec_test_secret')
    @mock.patch('stripe.Webhook.construct_event')
    def test_webhook_fails_with_invalid_payload(self, mock_construct_event):
        """
        With authentication parsing the event, an invalid payload results in auth failing.
        Expect a 403 Forbidden with an appropriate error message.
        """
        # Authentication layer raises due to invalid payload
        mock_construct_event.side_effect = ValueError('Invalid payload')

        response = self._post_webhook_with_signature(
            'invalid payload',
            't=1234567890,v1=valid_signature'
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn('invalid', str(response.data).lower())

    @override_settings(STRIPE_WEBHOOK_ENDPOINT_SECRET='whsec_test_secret')
    @mock.patch('enterprise_access.apps.customer_billing.stripe_event_handlers.StripeEventHandler.dispatch')
    @mock.patch('stripe.Webhook.construct_event')
    def test_webhook_propagates_handler_exceptions(self, mock_construct_event, mock_dispatch):
        """
        Test that exceptions from event handler are propagated (trigger Stripe retry).
        """
        mock_event = {'id': 'evt_test', 'type': 'checkout.session.completed'}
        mock_construct_event.return_value = mock_event
        mock_dispatch.side_effect = Exception('Handler failed')

        with self.assertRaises(Exception) as context:
            self._post_webhook_with_signature(
                self.valid_event_payload,
                't=1234567890,v1=valid_signature'
            )

        self.assertIn('Handler failed', str(context.exception))
