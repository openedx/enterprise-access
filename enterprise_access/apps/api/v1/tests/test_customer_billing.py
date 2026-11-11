"""
Tests for customer billing API endpoints.
"""
import json
import uuid
from datetime import timedelta
from unittest import mock

import ddt
import requests
import stripe
from django.core.cache import cache
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from enterprise_access.apps.api.v1.views.customer_billing import BillingManagementViewSet, CheckoutIntentPermission
from enterprise_access.apps.api.v1.views.subsidy_access_policy import SubsidyAPIUnavailableException
from enterprise_access.apps.core.constants import (
    SYSTEM_ENTERPRISE_ADMIN_ROLE,
    SYSTEM_ENTERPRISE_LEARNER_ROLE,
    SYSTEM_ENTERPRISE_OPERATOR_ROLE
)
from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.constants import CheckoutIntentState
from enterprise_access.apps.customer_billing.models import (
    CheckoutIntent,
    SspProduct,
    StripeEventData,
    StripeEventSummary
)
from enterprise_access.apps.customer_billing.tests.factories import EnterpriseStripeCustomerFactory
from enterprise_access.apps.customer_billing.tests.utils import AttrDict
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory
)
from test_utils import APITest

SUBSIDY_CLIENT_PATH = 'enterprise_access.apps.subsidy_access_policy.models.get_versioned_subsidy_client'
ACCOUNT_STANDING_PATH = 'enterprise_access.apps.customer_billing.api.is_account_in_good_standing'


@ddt.ddt
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
        # Clear Django cache to prevent Stripe API cache pollution between tests
        cache.clear()
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

        mock_session_data = {
            'id': 'bps_test_123',
            'url': 'https://billing.stripe.com/session/test_123',
            'customer': self.stripe_customer_id,
        }
        mock_stripe_session = mock.Mock()
        mock_stripe_session.to_dict.return_value = mock_session_data

        with mock.patch('stripe.billing_portal.Session.create') as mock_create:
            mock_create.return_value = mock_stripe_session

            response = self.client.get(
                url,
                {'enterprise_customer_uuid': self.enterprise_uuid},
                HTTP_ORIGIN='https://admin.example.com'
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, mock_session_data)

        # Implementation uses /{enterprise_slug} for Admin portal return URL.
        mock_create.assert_called_once_with(
            customer=self.stripe_customer_id,
            return_url='https://admin.example.com/test-enterprise',
        )

    @ddt.data(
        ('no_auth', None, None, True, status.HTTP_401_UNAUTHORIZED),
        ('wrong_role', SYSTEM_ENTERPRISE_LEARNER_ROLE, 'existing', True, status.HTTP_403_FORBIDDEN),
        ('missing_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, 'existing', False, status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_create_enterprise_admin_portal_session_rbac(
        self, _scenario, role, _uuid_type, include_uuid, expected_status
    ):
        """
        Test RBAC scenarios for enterprise admin portal session endpoint.
        Scenarios: no_auth (401), wrong_role (403), missing_uuid (403).
        """
        # Setup authentication based on scenario
        if role is not None:
            self.set_jwt_cookie([{
                'system_wide_role': role,
                'context': self.enterprise_uuid,
            }])

        url = reverse('api:v1:customer-billing-create-enterprise-admin-portal-session')

        # Build query params based on scenario
        query_params = {}
        if include_uuid:
            query_params['enterprise_customer_uuid'] = self.enterprise_uuid

        response = self.client.get(url, query_params)

        self.assertEqual(response.status_code, expected_status)

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
            mock_create.return_value.to_dict.return_value = mock_session

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

    @ddt.data(
        ('no_auth', None, 'existing', status.HTTP_401_UNAUTHORIZED),
        ('wrong_user', 'other_user', 'existing', status.HTTP_403_FORBIDDEN),
        ('nonexistent_intent', 'same_user', 99999, status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_create_checkout_portal_session_rbac(
        self, _scenario, user_type, intent_pk, expected_status
    ):
        """
        Test RBAC scenarios for checkout portal session endpoint.
        Scenarios: no_auth (401), wrong_user (403), nonexistent_intent (403).
        """
        # Setup authentication and user based on scenario
        if user_type == 'other_user':
            other_user = UserFactory()
            self.set_jwt_cookie([{
                'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
                'context': str(uuid.uuid4()),
            }], user=other_user)
        elif user_type == 'same_user':
            self.set_jwt_cookie([{
                'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
                'context': str(uuid.uuid4()),
            }])

        # Determine the intent pk to use
        pk = self.checkout_intent.id if intent_pk == 'existing' else intent_pk

        url = reverse('api:v1:customer-billing-create-checkout-portal-session',
                      kwargs={'pk': pk})

        response = self.client.get(url)

        self.assertEqual(response.status_code, expected_status)

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

    def test_create_checkout_portal_session_with_uuid_lookup(self):
        """
        Test checkout portal session endpoint with UUID lookup instead of integer ID.
        """
        # Create a checkout intent for this test
        enterprise_uuid = str(uuid.uuid4())
        checkout_intent = CheckoutIntent.objects.create(
            user=self.user,
            enterprise_uuid=enterprise_uuid,
            enterprise_name='Test Enterprise',
            enterprise_slug='test-enterprise',
            stripe_customer_id='cus_test_uuid',
            state=CheckoutIntentState.PAID,
            quantity=10,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        url = reverse('api:v1:customer-billing-create-checkout-portal-session',
                      kwargs={'pk': str(checkout_intent.uuid)})

        mock_session = {
            'id': 'bps_test_uuid',
            'url': 'https://billing.stripe.com/session/test_uuid',
            'customer': 'cus_test_uuid',
        }

        with mock.patch('stripe.billing_portal.Session.create') as mock_create:
            mock_create.return_value.to_dict.return_value = mock_session

            response = self.client.get(
                url,
                HTTP_ORIGIN='https://checkout.example.com'
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, mock_session)

        checkout_intent.delete()

    def test_create_checkout_portal_session_general_exception(self):
        """
        Test checkout portal session with general (non-Stripe) exception.
        """
        # Create a checkout intent for this test
        enterprise_uuid = str(uuid.uuid4())
        checkout_intent = CheckoutIntent.objects.create(
            user=self.user,
            enterprise_uuid=enterprise_uuid,
            enterprise_name='Test Enterprise',
            enterprise_slug='test-enterprise',
            stripe_customer_id='cus_test_general',
            state=CheckoutIntentState.PAID,
            quantity=10,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        url = reverse('api:v1:customer-billing-create-checkout-portal-session',
                      kwargs={'pk': checkout_intent.id})

        with mock.patch('stripe.billing_portal.Session.create') as mock_create:
            mock_create.side_effect = Exception('Unexpected error')

            response = self.client.get(
                url,
                HTTP_ORIGIN='https://checkout.example.com'
            )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn('General exception', response.data)

        checkout_intent.delete()

    def test_create_enterprise_admin_portal_no_stripe_customer_or_slug(self):
        """
        Test admin portal session when CheckoutIntent has no stripe_customer_id or enterprise_slug.
        """
        # Create intent without stripe_customer_id and slug
        other_user = UserFactory()
        enterprise_uuid = str(uuid.uuid4())
        checkout_intent = CheckoutIntent.objects.create(
            user=other_user,
            enterprise_uuid=enterprise_uuid,
            enterprise_name='Test Enterprise',
            enterprise_slug=None,
            stripe_customer_id=None,
            state=CheckoutIntentState.CREATED,
            quantity=5,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': enterprise_uuid,
        }])

        url = reverse('api:v1:customer-billing-create-enterprise-admin-portal-session')
        response = self.client.get(
            url,
            {'enterprise_customer_uuid': enterprise_uuid}
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('stripe customer id or enterprise slug', response.data)

        checkout_intent.delete()

    def test_create_enterprise_admin_portal_general_exception(self):
        """
        Test admin portal session with general (non-Stripe) exception.
        """
        # Create a checkout intent for this test
        enterprise_uuid = str(uuid.uuid4())
        checkout_intent = CheckoutIntent.objects.create(
            user=self.user,
            enterprise_uuid=enterprise_uuid,
            enterprise_name='Test Enterprise',
            enterprise_slug='test-enterprise',
            stripe_customer_id='cus_test_admin_exc',
            state=CheckoutIntentState.PAID,
            quantity=10,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': enterprise_uuid,
        }])

        url = reverse('api:v1:customer-billing-create-enterprise-admin-portal-session')

        with mock.patch('stripe.billing_portal.Session.create') as mock_create:
            mock_create.side_effect = Exception('Unexpected error')

            response = self.client.get(
                url,
                {'enterprise_customer_uuid': enterprise_uuid},
                HTTP_ORIGIN='https://admin.example.com'
            )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn('General exception', response.data)

        checkout_intent.delete()


class CheckoutIntentPermissionTests(APITest):
    """
    Tests for CheckoutIntentPermission class edge cases.
    """

    def setUp(self):
        super().setUp()
        self.enterprise_uuid = str(uuid.uuid4())
        self.checkout_intent = CheckoutIntent.objects.create(
            user=self.user,
            enterprise_uuid=self.enterprise_uuid,
            enterprise_name='Test Enterprise',
            enterprise_slug='test-enterprise',
            stripe_customer_id='cus_test_123',
            state=CheckoutIntentState.PAID,
            quantity=10,
            expires_at=timezone.now() + timedelta(hours=1),
        )

    def tearDown(self):
        CheckoutIntent.objects.all().delete()
        super().tearDown()

    def test_permission_invalid_uuid_fallback_to_int_typeerror(self):
        """
        Test CheckoutIntentPermission when pk is invalid for both UUID and int (TypeError).
        """
        permission = CheckoutIntentPermission()
        factory = RequestFactory()

        # Create a mock request
        request = factory.get('/')
        request.user = self.user
        request.parser_context = {'kwargs': {'pk': None}}  # None will cause TypeError

        # Create a mock view
        class MockView:
            action = 'create_checkout_portal_session'

        view = MockView()

        # Should return False due to TypeError
        result = permission.has_permission(request, view)
        self.assertFalse(result)

    def test_permission_invalid_int_valueerror(self):
        """
        Test CheckoutIntentPermission when pk fails int() conversion (ValueError).
        """
        permission = CheckoutIntentPermission()
        factory = RequestFactory()

        # Create a mock request with invalid pk
        request = factory.get('/')
        request.user = self.user
        request.parser_context = {'kwargs': {'pk': 'not-a-uuid-or-int'}}

        # Create a mock view
        class MockView:
            action = 'create_checkout_portal_session'

        view = MockView()

        # Should return False due to ValueError in both UUID and int parsing
        result = permission.has_permission(request, view)
        self.assertFalse(result)


class BillingManagementBaseTest(APITest):
    """
    Base test class for billing management endpoints with shared setUp/tearDown.
    Provides common test fixtures: enterprise_uuid, stripe_customer_id, and checkout_intent.
    """

    def setUp(self):
        super().setUp()
        self.enterprise_uuid = str(uuid.uuid4())
        self.stripe_customer_id = self._get_stripe_customer_id()

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

        # Set JWT cookie with appropriate permissions
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': self.enterprise_uuid,
        }])

    def tearDown(self):
        CheckoutIntent.objects.all().delete()
        # Clear Django cache to prevent Stripe API cache pollution between tests
        cache.clear()
        super().tearDown()

    def _get_stripe_customer_id(self):
        """
        Override in subclasses to provide a unique Stripe customer ID for testing.
        Default provides a generic ID.
        """
        return 'cus_test_123'


class BillingManagementAPITests(APITest):
    """
    Tests for the billing management API endpoints.
    """

    def setUp(self):
        super().setUp()
        self.enterprise_uuid = str(uuid.uuid4())
        # Set JWT cookie for authentication
        self.set_jwt_cookie()

    def test_billing_management_api_endpoint_available(self):
        """
        Test that the billing management API health-check endpoint is available and requires authentication.
        """
        # The endpoint should be available when flag is enabled in settings
        url = reverse('api:v1:billing-management-health-check')
        response = self.client.get(url)
        # Should return 200 OK because we have authentication set up by APITest
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {'status': 'healthy'})

    def test_billing_management_requires_authentication(self):
        """
        Test that the billing management API endpoint requires authentication.
        """
        # Remove authentication
        self.client.cookies.clear()

        url = reverse('api:v1:billing-management-health-check')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


@ddt.ddt
class BillingManagementAddressEndpointTests(BillingManagementBaseTest):
    """
    Tests for the billing management address endpoint.
    """

    def _get_stripe_customer_id(self):
        return 'cus_test_address_123'

    @mock.patch('stripe.Customer.retrieve')
    def test_get_address_success(self, mock_stripe_customer_retrieve):
        """
        Test successful retrieval of billing address.
        """
        mock_stripe_customer = AttrDict.wrap({
            'id': self.stripe_customer_id,
            'name': 'John Doe',
            'email': 'john@example.com',
            'phone': '+1234567890',
            'address': {
                'line1': '123 Main St',
                'line2': 'Suite 100',
                'city': 'San Francisco',
                'state': 'CA',
                'postal_code': '94105',
                'country': 'US',
            },
        })
        mock_stripe_customer_retrieve.return_value = mock_stripe_customer

        url = reverse('api:v1:billing-management-address')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        self.assertEqual(response_data['name'], 'John Doe')
        self.assertEqual(response_data['email'], 'john@example.com')
        self.assertEqual(response_data['phone'], '+1234567890')
        self.assertEqual(response_data['address_line_1'], '123 Main St')
        self.assertEqual(response_data['address_line_2'], 'Suite 100')
        self.assertEqual(response_data['city'], 'San Francisco')
        self.assertEqual(response_data['state'], 'CA')
        self.assertEqual(response_data['postal_code'], '94105')
        self.assertEqual(response_data['country'], 'US')

    @ddt.data(
        ('missing_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, None, status.HTTP_403_FORBIDDEN),
        ('nonexistent_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, 'nonexistent', status.HTTP_403_FORBIDDEN),
        ('wrong_role', SYSTEM_ENTERPRISE_LEARNER_ROLE, 'existing', status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_get_address_rbac(self, scenario, role, uuid_type, expected_status):
        """
        Test RBAC scenarios for get address endpoint.
        Scenarios: missing_uuid (403), nonexistent_uuid (403), wrong_role (403).
        """
        # Setup authentication with appropriate role
        if scenario == 'wrong_role':
            unprivileged_user = UserFactory()
            self.set_jwt_cookie([{
                'system_wide_role': role,
                'context': self.enterprise_uuid,
            }], user=unprivileged_user)
        else:
            self.set_jwt_cookie([{
                'system_wide_role': role,
                'context': self.enterprise_uuid,
            }])

        url = reverse('api:v1:billing-management-address')

        # Build query params based on scenario
        if uuid_type is None:
            query_params = {}
        elif uuid_type == 'nonexistent':
            query_params = {'enterprise_customer_uuid': str(uuid.uuid4())}
        else:  # 'existing'
            query_params = {'enterprise_customer_uuid': str(self.enterprise_uuid)}

        response = self.client.get(url, query_params)

        self.assertEqual(response.status_code, expected_status)

    @mock.patch('stripe.Customer.retrieve')
    def test_get_address_stripe_error(self, mock_stripe_customer_retrieve):
        """
        Test that Stripe API errors are handled gracefully.
        """
        mock_stripe_customer_retrieve.side_effect = stripe.error.StripeError('Stripe API Error')

        url = reverse('api:v1:billing-management-address')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn('error', response.json())

    @mock.patch('stripe.Customer.retrieve')
    def test_get_address_with_partial_address_data(self, mock_stripe_customer_retrieve):
        """
        Test that endpoint handles Stripe customers with partial address data.
        """
        mock_stripe_customer = AttrDict.wrap({
            'id': self.stripe_customer_id,
            'name': 'Jane Doe',
            'email': 'jane@example.com',
            'phone': None,
            'address': None,
        })
        mock_stripe_customer_retrieve.return_value = mock_stripe_customer

        url = reverse('api:v1:billing-management-address')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        self.assertEqual(response_data['name'], 'Jane Doe')
        self.assertEqual(response_data['email'], 'jane@example.com')
        self.assertIsNone(response_data['phone'])
        self.assertIsNone(response_data.get('address_line_1'))
        self.assertIsNone(response_data.get('city'))

    @mock.patch('stripe.Customer.retrieve')
    def test_get_address_with_empty_string_values(self, mock_stripe_customer_retrieve):
        """
        Test that endpoint handles Stripe customers with empty string address values.

        Stripe may return empty strings for address fields rather than null values.
        The response serializer should accept these without validation errors.
        """
        mock_stripe_customer = AttrDict.wrap({
            'id': self.stripe_customer_id,
            'name': 'Jane Doe',
            'email': 'jane@example.com',
            'phone': None,
            'address': {
                'line1': '',
                'line2': '',
                'city': '',
                'state': '',
                'postal_code': '',
                'country': 'US',
            },
        })
        mock_stripe_customer_retrieve.return_value = mock_stripe_customer

        url = reverse('api:v1:billing-management-address')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        self.assertEqual(response_data['name'], 'Jane Doe')
        self.assertEqual(response_data['email'], 'jane@example.com')
        self.assertIsNone(response_data['phone'])
        self.assertEqual(response_data['address_line_1'], '')
        self.assertEqual(response_data['address_line_2'], '')
        self.assertEqual(response_data['city'], '')
        self.assertEqual(response_data['state'], '')
        self.assertEqual(response_data['postal_code'], '')
        self.assertEqual(response_data['country'], 'US')


@ddt.ddt
class BillingManagementAddressUpdateTests(BillingManagementBaseTest):
    """
    Tests for the billing management address update endpoint.
    """

    def _get_stripe_customer_id(self):
        return 'cus_test_update_123'

    @mock.patch('stripe.Customer.modify')
    def test_update_address_success(self, mock_customer_modify):
        """
        Test successful update of billing address.
        """
        updated_customer = AttrDict.wrap({
            'id': self.stripe_customer_id,
            'name': 'Jane Smith',
            'email': 'jane.smith@example.com',
            'phone': '+14155551234',
            'address': {
                'line1': '456 Oak Ave',
                'line2': 'Floor 2',
                'city': 'New York',
                'state': 'NY',
                'postal_code': '10001',
                'country': 'US',
            },
        })
        mock_customer_modify.return_value = updated_customer

        url = reverse('api:v1:billing-management-address')
        request_data = {
            'name': 'Jane Smith',
            'email': 'jane.smith@example.com',
            'phone': '+14155551234',
            'address_line_1': '456 Oak Ave',
            'address_line_2': 'Floor 2',
            'city': 'New York',
            'state': 'NY',
            'postal_code': '10001',
            'country': 'US',
        }
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            request_data,
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        self.assertEqual(response_data['name'], 'Jane Smith')
        self.assertEqual(response_data['email'], 'jane.smith@example.com')
        self.assertEqual(response_data['phone'], '+14155551234')
        self.assertEqual(response_data['address_line_1'], '456 Oak Ave')
        self.assertEqual(response_data['address_line_2'], 'Floor 2')
        self.assertEqual(response_data['city'], 'New York')
        self.assertEqual(response_data['state'], 'NY')
        self.assertEqual(response_data['postal_code'], '10001')
        self.assertEqual(response_data['country'], 'US')

        # Verify Stripe API was called correctly
        mock_customer_modify.assert_called_once_with(
            self.stripe_customer_id,
            name='Jane Smith',
            email='jane.smith@example.com',
            phone='+14155551234',
            address={
                'line1': '456 Oak Ave',
                'line2': 'Floor 2',
                'city': 'New York',
                'state': 'NY',
                'postal_code': '10001',
                'country': 'US',
            },
        )

    @ddt.data(
        ('missing_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, None, status.HTTP_403_FORBIDDEN),
        ('nonexistent_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, 'nonexistent', status.HTTP_403_FORBIDDEN),
        ('wrong_role', SYSTEM_ENTERPRISE_LEARNER_ROLE, 'existing', status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_update_address_rbac(self, scenario, role, uuid_type, expected_status):
        """
        Test RBAC scenarios for update address endpoint.
        Scenarios: missing_uuid (403), nonexistent_uuid (403), wrong_role (403).
        """
        # Setup authentication with appropriate role
        if scenario == 'wrong_role':
            unprivileged_user = UserFactory()
            self.set_jwt_cookie([{
                'system_wide_role': role,
                'context': self.enterprise_uuid,
            }], user=unprivileged_user)
        else:
            self.set_jwt_cookie([{
                'system_wide_role': role,
                'context': self.enterprise_uuid,
            }])

        url = reverse('api:v1:billing-management-address')
        request_data = {
            'name': 'Jane Smith',
            'email': 'jane@example.com',
            'country': 'US',
            'address_line_1': '123 Main St',
            'city': 'San Francisco',
            'state': 'CA',
            'postal_code': '94105',
        }

        # Build URL with query params based on scenario
        if uuid_type is None:
            response = self.client.post(url, request_data, format='json')
        elif uuid_type == 'nonexistent':
            response = self.client.post(
                f"{url}?enterprise_customer_uuid={uuid.uuid4()}",
                request_data,
                format='json'
            )
        else:  # 'existing'
            response = self.client.post(
                f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
                request_data,
                format='json'
            )

        self.assertEqual(response.status_code, expected_status)

    @ddt.data(
        (
            'missing_required_fields',
            {'name': 'Jane Smith'},
            ['email', 'country']
        ),
        (
            'invalid_country_code',
            {
                'name': 'Jane Smith',
                'email': 'jane@example.com',
                'country': 'USA',  # Invalid - should be 2 letters
                'address_line_1': '123 Main St',
                'city': 'San Francisco',
                'state': 'CA',
                'postal_code': '94105',
            },
            ['country']
        ),
    )
    @ddt.unpack
    def test_update_address_validation_errors(self, _scenario, request_data, expected_error_fields):
        """
        Test validation errors for update address endpoint.
        Scenarios: missing_required_fields (400), invalid_country_code (400).
        """
        url = reverse('api:v1:billing-management-address')
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            request_data,
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response_data = response.json()
        for field in expected_error_fields:
            self.assertIn(field, response_data)

    @mock.patch('stripe.Customer.modify')
    def test_update_address_stripe_error(self, mock_customer_modify):
        """
        Test that Stripe API errors are handled gracefully.
        """
        mock_customer_modify.side_effect = stripe.error.StripeError('Stripe API Error')

        url = reverse('api:v1:billing-management-address')
        request_data = {
            'name': 'Jane Smith',
            'email': 'jane@example.com',
            'country': 'US',
            'address_line_1': '123 Main St',
            'city': 'San Francisco',
            'state': 'CA',
            'postal_code': '94105',
        }
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            request_data,
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn('error', response.json())

    @mock.patch('stripe.Customer.modify')
    def test_update_address_with_optional_fields_omitted(self, mock_customer_modify):
        """
        Test that update works when optional fields are omitted.
        """
        updated_customer = AttrDict.wrap({
            'id': self.stripe_customer_id,
            'name': 'John Doe',
            'email': 'john@example.com',
            'phone': None,
            'address': {
                'line1': '789 Pine St',
                'line2': None,
                'city': 'Los Angeles',
                'state': 'CA',
                'postal_code': '90001',
                'country': 'US',
            },
        })
        mock_customer_modify.return_value = updated_customer

        url = reverse('api:v1:billing-management-address')
        request_data = {
            'name': 'John Doe',
            'email': 'john@example.com',
            'country': 'US',
            'address_line_1': '789 Pine St',
            # Omit optional: address_line_2, phone
            'city': 'Los Angeles',
            'state': 'CA',
            'postal_code': '90001',
        }
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            request_data,
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        self.assertEqual(response_data['name'], 'John Doe')
        self.assertEqual(response_data['email'], 'john@example.com')


@ddt.ddt
class BillingManagementPaymentMethodsTests(BillingManagementBaseTest):
    """
    Tests for the billing management payment methods endpoint.
    """

    def _get_stripe_customer_id(self):
        return 'cus_test_payment_123'

    @mock.patch('stripe.Customer.retrieve')
    @mock.patch('stripe.PaymentMethod.list')
    def test_list_payment_methods_success(self, mock_payment_method_list, mock_customer_retrieve):
        """
        Test successful retrieval of payment methods.
        """
        mock_customer = AttrDict.wrap({'invoice_settings': {'default_payment_method': 'pm_card_visa'}})
        mock_customer_retrieve.return_value = mock_customer

        mock_payment_methods = [
            AttrDict.wrap({
                'id': 'pm_card_visa',
                'type': 'card',
                'card': {
                    'last4': '4242',
                    'brand': 'visa',
                    'exp_month': 12,
                    'exp_year': 2025,
                }
            }),
            AttrDict.wrap({
                'id': 'pm_card_mastercard',
                'type': 'card',
                'card': {
                    'last4': '5555',
                    'brand': 'mastercard',
                    'exp_month': 6,
                    'exp_year': 2026,
                }
            }),
        ]
        mock_payment_method_list.return_value = mock.Mock(data=mock_payment_methods)

        url = reverse('api:v1:billing-management-payment-methods')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        self.assertEqual(len(response_data['payment_methods']), 2)

        # Check first payment method (the default)
        first_method = response_data['payment_methods'][0]
        self.assertEqual(first_method['id'], 'pm_card_visa')
        self.assertEqual(first_method['type'], 'card')
        self.assertEqual(first_method['last4'], '4242')
        self.assertEqual(first_method['brand'], 'visa')
        self.assertEqual(first_method['exp_month'], 12)
        self.assertEqual(first_method['exp_year'], 2025)
        self.assertTrue(first_method['is_default'])
        self.assertEqual(first_method['status'], 'verified')  # Cards are always verified

        # Check second payment method (not default)
        second_method = response_data['payment_methods'][1]
        self.assertEqual(second_method['id'], 'pm_card_mastercard')
        self.assertEqual(second_method['type'], 'card')
        self.assertEqual(second_method['last4'], '5555')
        self.assertEqual(second_method['brand'], 'mastercard')
        self.assertFalse(second_method['is_default'])
        self.assertEqual(second_method['status'], 'verified')  # Cards are always verified

    @mock.patch('stripe.Customer.retrieve')
    @mock.patch('stripe.PaymentMethod.list')
    def test_list_payment_methods_empty(self, mock_payment_method_list, mock_customer_retrieve):
        """
        Test that empty payment methods list returns successfully.
        """
        mock_customer = AttrDict.wrap({'invoice_settings': {}})
        mock_customer_retrieve.return_value = mock_customer
        mock_payment_method_list.return_value = mock.Mock(data=[])

        url = reverse('api:v1:billing-management-payment-methods')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        self.assertEqual(len(response_data['payment_methods']), 0)

    @ddt.data(
        ('missing_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, None, status.HTTP_403_FORBIDDEN),
        ('nonexistent_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, 'nonexistent', status.HTTP_403_FORBIDDEN),
        ('wrong_role', SYSTEM_ENTERPRISE_LEARNER_ROLE, 'existing', status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_list_payment_methods_rbac(self, scenario, role, uuid_type, expected_status):
        """
        Test RBAC scenarios for list payment methods endpoint.
        Scenarios: missing_uuid (403), nonexistent_uuid (403), wrong_role (403).
        """
        # Setup authentication with appropriate role
        if scenario == 'wrong_role':
            unprivileged_user = UserFactory()
            self.set_jwt_cookie([{
                'system_wide_role': role,
                'context': self.enterprise_uuid,
            }], user=unprivileged_user)
        else:
            self.set_jwt_cookie([{
                'system_wide_role': role,
                'context': self.enterprise_uuid,
            }])

        url = reverse('api:v1:billing-management-payment-methods')

        # Build query params based on scenario
        if uuid_type is None:
            query_params = {}
        elif uuid_type == 'nonexistent':
            query_params = {'enterprise_customer_uuid': str(uuid.uuid4())}
        else:  # 'existing'
            query_params = {'enterprise_customer_uuid': str(self.enterprise_uuid)}

        response = self.client.get(url, query_params)

        self.assertEqual(response.status_code, expected_status)

    @mock.patch('stripe.Customer.retrieve')
    def test_list_payment_methods_stripe_error(self, mock_customer_retrieve):
        """
        Test that Stripe API errors are handled gracefully.
        """
        mock_customer_retrieve.side_effect = stripe.error.StripeError('Stripe API Error')

        url = reverse('api:v1:billing-management-payment-methods')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn('error', response.json())

    @mock.patch('stripe.Customer.retrieve')
    @mock.patch('stripe.PaymentMethod.list')
    def test_list_payment_methods_with_bank_account(self, mock_payment_method_list, mock_customer_retrieve):
        """
        Test that payment methods include bank account details when present.
        """
        mock_customer = AttrDict.wrap({'invoice_settings': {'default_payment_method': 'pm_bank_account'}})
        mock_customer_retrieve.return_value = mock_customer

        mock_payment_methods = [
            AttrDict.wrap({
                'id': 'pm_bank_account',
                'type': 'us_bank_account',
                'us_bank_account': {
                    'last4': '6789',
                }
            }),
        ]
        mock_payment_method_list.return_value = mock.Mock(data=mock_payment_methods)

        url = reverse('api:v1:billing-management-payment-methods')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        self.assertEqual(len(response_data['payment_methods']), 1)

        method = response_data['payment_methods'][0]
        self.assertEqual(method['id'], 'pm_bank_account')
        self.assertEqual(method['type'], 'us_bank_account')
        self.assertEqual(method['last4'], '6789')
        self.assertTrue(method['is_default'])
        self.assertEqual(method['status'], 'verified')  # Default status when not specified

    @ddt.data(
        ('verified', 'verified', 'Verified bank account'),
        ('verification_required', 'pending', 'Pending verification'),
        ('new', 'pending', 'New bank account pending verification'),
        ('verification_failed', 'failed', 'Failed verification'),
        ('errored', 'failed', 'Errored during verification'),
    )
    @ddt.unpack
    @mock.patch('stripe.Customer.retrieve')
    @mock.patch('stripe.PaymentMethod.list')
    def test_list_payment_methods_bank_account_status(
        self, stripe_status, expected_status, description, mock_payment_method_list, mock_customer_retrieve
    ):
        """
        Test that bank account payment methods return correct status based on Stripe verification status.
        """
        mock_customer = AttrDict.wrap({'invoice_settings': {'default_payment_method': 'pm_bank_account'}})
        mock_customer_retrieve.return_value = mock_customer

        mock_payment_methods = [
            AttrDict.wrap({
                'id': 'pm_bank_account',
                'type': 'us_bank_account',
                'status': stripe_status,
                'us_bank_account': {
                    'last4': '6789',
                    'status_details': {
                        'status': stripe_status,
                    },
                }
            }),
        ]
        mock_payment_method_list.return_value = mock.Mock(data=mock_payment_methods)

        url = reverse('api:v1:billing-management-payment-methods')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        method = response_data['payment_methods'][0]
        self.assertEqual(method['status'], expected_status, f'Failed for {description}')


@ddt.ddt
class BillingManagementAttachPaymentMethodTests(BillingManagementBaseTest):
    """
    Tests for the attach payment method endpoint (POST /payment-methods/).
    """

    def setUp(self):
        super().setUp()
        self.payment_method_id = 'pm_test_attach_123'

    def _get_stripe_customer_id(self):
        return 'cus_test_attach_456'

    @mock.patch('stripe.PaymentMethod.attach')
    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_attach_payment_method_card_success(self, mock_pm_retrieve, mock_pm_attach):
        """
        Test successfully attaching a card payment method.
        """
        # Mock payment method retrieve (verify it exists)
        mock_pm = mock.Mock()
        mock_pm.to_dict.return_value = {'id': self.payment_method_id, 'type': 'card', 'customer': None}
        mock_pm_retrieve.return_value = mock_pm

        # Mock payment method attach
        mock_pm_attach.return_value = mock_pm

        url = reverse('api:v1:billing-management-payment-methods')
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            data={'payment_method_id': self.payment_method_id},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        self.assertEqual(response_data['message'], 'Payment method added successfully')
        self.assertEqual(response_data['payment_method_id'], self.payment_method_id)

        # Verify Stripe API calls
        mock_pm_retrieve.assert_called_once_with(self.payment_method_id)
        mock_pm_attach.assert_called_once_with(
            self.payment_method_id,
            customer=self.stripe_customer_id,
        )

    @mock.patch('stripe.PaymentMethod.attach')
    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_attach_payment_method_bank_account_success(self, mock_pm_retrieve, mock_pm_attach):
        """
        Test successfully attaching a us_bank_account payment method.
        """
        # Mock payment method retrieve
        mock_pm = mock.Mock()
        mock_pm.to_dict.return_value = {'id': self.payment_method_id, 'type': 'us_bank_account', 'customer': None}
        mock_pm_retrieve.return_value = mock_pm
        mock_pm_attach.return_value = mock_pm

        url = reverse('api:v1:billing-management-payment-methods')
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            data={'payment_method_id': self.payment_method_id},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        self.assertEqual(response_data['message'], 'Payment method added successfully')
        self.assertEqual(response_data['payment_method_id'], self.payment_method_id)

    @mock.patch('stripe.PaymentMethod.attach')
    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_attach_payment_method_already_attached_idempotent(self, mock_pm_retrieve, mock_pm_attach):
        """
        Test that attaching an already-attached payment method is idempotent (returns success).
        Stripe.PaymentMethod.attach() is idempotent - returns success if already attached to same customer.
        """
        mock_pm = mock.Mock()
        # Already attached to THIS customer - should return success without calling attach
        mock_pm.to_dict.return_value = {
            'id': self.payment_method_id, 'type': 'card', 'customer': self.stripe_customer_id,
        }
        mock_pm_retrieve.return_value = mock_pm

        url = reverse('api:v1:billing-management-payment-methods')
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            data={'payment_method_id': self.payment_method_id},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Verify attach was NOT called since it's already attached
        mock_pm_attach.assert_not_called()

    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_attach_payment_method_not_found(self, mock_pm_retrieve):
        """
        Test attaching a non-existent payment method returns 404.
        """
        mock_pm_retrieve.side_effect = stripe.error.InvalidRequestError(
            'No such payment method: pm_invalid',
            param='payment_method'
        )

        url = reverse('api:v1:billing-management-payment-methods')
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            data={'payment_method_id': 'pm_invalid'},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('Payment method not found', response.json()['error'])

    def test_attach_payment_method_missing_uuid(self):
        """
        Test missing enterprise_customer_uuid returns 403 (RBAC blocks before view).
        """
        url = reverse('api:v1:billing-management-payment-methods')
        response = self.client.post(
            url,
            data={'payment_method_id': self.payment_method_id},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_attach_payment_method_missing_payment_method_id(self):
        """
        Test missing payment_method_id in request body returns 400.
        """
        url = reverse('api:v1:billing-management-payment-methods')
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            data={},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_attach_payment_method_nonexistent_enterprise(self):
        """
        Test non-existent enterprise UUID returns 403 (RBAC check fails).
        """
        nonexistent_uuid = uuid.uuid4()
        url = reverse('api:v1:billing-management-payment-methods')
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={nonexistent_uuid}',
            data={'payment_method_id': self.payment_method_id},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @mock.patch('stripe.PaymentMethod.attach')
    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_attach_payment_method_stripe_error(self, mock_pm_retrieve, mock_pm_attach):
        """
        Test Stripe API error returns 422.
        """
        mock_pm = mock.Mock()
        mock_pm.to_dict.return_value = {'customer': None}
        mock_pm_retrieve.return_value = mock_pm
        mock_pm_attach.side_effect = stripe.error.StripeError('Stripe error occurred')

        url = reverse('api:v1:billing-management-payment-methods')
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            data={'payment_method_id': self.payment_method_id},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn('Stripe API error', response.json()['error'])

    @ddt.data(SYSTEM_ENTERPRISE_OPERATOR_ROLE, SYSTEM_ENTERPRISE_ADMIN_ROLE)
    @mock.patch('stripe.PaymentMethod.attach')
    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_attach_payment_method_admin_and_operator_success(self, role, mock_pm_retrieve, mock_pm_attach):
        """
        Test that both admin and operator roles can attach payment methods.
        """
        # Setup authentication with specified role
        self.set_jwt_cookie([{
            'system_wide_role': role,
            'context': self.enterprise_uuid,
        }])

        mock_pm = mock.Mock()
        mock_pm.to_dict.return_value = {'id': self.payment_method_id, 'customer': None}
        mock_pm_retrieve.return_value = mock_pm
        mock_pm_attach.return_value = mock_pm

        url = reverse('api:v1:billing-management-payment-methods')
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            data={'payment_method_id': self.payment_method_id},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_attach_payment_method_learner_denied(self):
        """
        Test that learner role cannot attach payment methods (403).
        """
        unprivileged_user = UserFactory()
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': self.enterprise_uuid,
        }], user=unprivileged_user)

        url = reverse('api:v1:billing-management-payment-methods')
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            data={'payment_method_id': self.payment_method_id},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


@ddt.ddt
class BillingManagementSetDefaultPaymentMethodTests(BillingManagementBaseTest):
    """
    Tests for the set default payment method endpoint.
    """

    def setUp(self):
        super().setUp()
        self.payment_method_id = 'pm_test_set_default_456'

    def _get_stripe_customer_id(self):
        return 'cus_test_set_default_123'

    @mock.patch('stripe.Customer.modify')
    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_set_default_payment_method_success(self, mock_payment_method_retrieve, mock_customer_modify):
        """
        Test successfully setting a payment method as default.
        """
        mock_payment_method_retrieve.return_value = AttrDict.wrap({'customer': self.stripe_customer_id})

        url = reverse('api:v1:billing-management-set-default-payment-method', args=[self.payment_method_id])
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['message'], 'Payment method set as default successfully')

        # Verify Stripe API calls
        mock_payment_method_retrieve.assert_called_once_with(self.payment_method_id)
        mock_customer_modify.assert_called_once_with(
            self.stripe_customer_id,
            invoice_settings={'default_payment_method': self.payment_method_id}
        )

    @ddt.data(
        ('missing_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, None, status.HTTP_403_FORBIDDEN),
        ('nonexistent_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, 'nonexistent', status.HTTP_403_FORBIDDEN),
        ('wrong_role', SYSTEM_ENTERPRISE_LEARNER_ROLE, 'existing', status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_set_default_payment_method_rbac(self, scenario, role, uuid_type, expected_status):
        """
        Test RBAC scenarios for set default payment method endpoint.
        Scenarios: missing_uuid (403), nonexistent_uuid (403), wrong_role (403).
        """
        # Setup authentication with appropriate role
        if scenario == 'wrong_role':
            unprivileged_user = UserFactory()
            self.set_jwt_cookie([{
                'system_wide_role': role,
                'context': self.enterprise_uuid,
            }], user=unprivileged_user)
        else:
            self.set_jwt_cookie([{
                'system_wide_role': role,
                'context': self.enterprise_uuid,
            }])

        url = reverse('api:v1:billing-management-set-default-payment-method', args=[self.payment_method_id])

        # Build URL with query params based on scenario
        if uuid_type is None:
            response = self.client.post(url, format='json')
        elif uuid_type == 'nonexistent':
            response = self.client.post(
                f"{url}?enterprise_customer_uuid={uuid.uuid4()}",
                format='json'
            )
        else:  # 'existing'
            response = self.client.post(
                f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
                format='json'
            )

        self.assertEqual(response.status_code, expected_status)

    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_set_default_payment_method_not_found(self, mock_payment_method_retrieve):
        """
        Test that non-existent payment method returns 404.
        """
        mock_payment_method_retrieve.side_effect = stripe.error.InvalidRequestError(
            'No such payment_method',
            param='id'
        )

        url = reverse('api:v1:billing-management-set-default-payment-method', args=[self.payment_method_id])
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('error', response.json())

    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_set_default_payment_method_wrong_customer(self, mock_payment_method_retrieve):
        """
        Test that payment method belonging to different customer returns 404.
        """
        # Mock payment method belonging to a different customer
        mock_payment_method_retrieve.return_value = AttrDict.wrap({'customer': 'cus_different_customer'})

        url = reverse('api:v1:billing-management-set-default-payment-method', args=[self.payment_method_id])
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        response_data = response.json()
        self.assertIn('error', response_data)
        self.assertIn('does not belong to this customer', response_data['error'])

    @mock.patch('stripe.Customer.modify')
    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_set_default_payment_method_stripe_error(self, mock_payment_method_retrieve, mock_customer_modify):
        """
        Test that Stripe API errors are handled gracefully.
        """
        mock_payment_method_retrieve.return_value = AttrDict.wrap({'customer': self.stripe_customer_id})
        mock_customer_modify.side_effect = stripe.error.StripeError('Stripe API Error')

        url = reverse('api:v1:billing-management-set-default-payment-method', args=[self.payment_method_id])
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn('error', response.json())

    def test_set_default_payment_method_no_checkout_intent(self):
        """
        Test that missing checkout intent returns 404.
        """
        # Create a new enterprise without checkout intent
        new_enterprise_uuid = str(uuid.uuid4())
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': new_enterprise_uuid,
        }])

        url = reverse('api:v1:billing-management-set-default-payment-method', args=[self.payment_method_id])
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={new_enterprise_uuid}',
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        response_data = response.json()
        self.assertIn('error', response_data)
        self.assertIn('Stripe customer not found', response_data['error'])

    @mock.patch('stripe.Customer.modify')
    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_set_default_payment_method_operator_role(
        self, mock_payment_method_retrieve, mock_customer_modify  # pylint: disable=unused-argument
    ):
        """
        Test that operator role can also set default payment method.
        """
        # Set JWT cookie with operator role
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE,
            'context': self.enterprise_uuid,
        }])

        mock_payment_method_retrieve.return_value = AttrDict.wrap({'customer': self.stripe_customer_id})

        url = reverse('api:v1:billing-management-set-default-payment-method', args=[self.payment_method_id])
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['message'], 'Payment method set as default successfully')


@ddt.ddt
class BillingManagementDeletePaymentMethodTests(BillingManagementBaseTest):
    """
    Tests for the delete payment method endpoint.
    """

    def _get_stripe_customer_id(self):
        return 'cus_test_delete_123'

    @mock.patch('stripe.PaymentMethod.detach')
    @mock.patch('stripe.PaymentMethod.list')
    @mock.patch('stripe.Customer.retrieve')
    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_delete_payment_method_success(self, mock_retrieve_pm, mock_retrieve_cust, mock_list_pm, mock_detach):
        """
        Test successfully deleting a non-default payment method when others exist.
        """
        mock_retrieve_pm.return_value = AttrDict.wrap({
            'id': 'pm_test123',
            'customer': self.stripe_customer_id,
        })

        # Mock customer with different default
        mock_retrieve_cust.return_value = AttrDict.wrap({
            'id': self.stripe_customer_id,
            'invoice_settings': {'default_payment_method': 'pm_default'},
        })

        # Mock multiple payment methods (only len() is called, no .to_dict())
        mock_list_pm.return_value = mock.Mock(data=['pm_default', 'pm_test123'])

        url = reverse('api:v1:billing-management-delete-payment-method', kwargs={'payment_method_id': 'pm_test123'})
        response = self.client.delete(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        self.assertIn('message', response_data)
        self.assertIn('deleted successfully', response_data['message'])
        mock_detach.assert_called_once_with('pm_test123')

    @mock.patch('stripe.PaymentMethod.list')
    @mock.patch('stripe.Customer.retrieve')
    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_delete_only_payment_method_fails(self, mock_retrieve_pm, mock_retrieve_cust, mock_list_pm):
        """
        Test that deleting the only payment method returns 409 conflict.
        """
        mock_retrieve_pm.return_value = AttrDict.wrap({
            'id': 'pm_only',
            'customer': self.stripe_customer_id,
        })

        # Mock customer
        mock_retrieve_cust.return_value = AttrDict.wrap({
            'id': self.stripe_customer_id,
            'invoice_settings': {'default_payment_method': 'pm_only'},
        })

        # Mock only one payment method (only len() is called)
        mock_list_pm.return_value = mock.Mock(data=['pm_only'])

        url = reverse('api:v1:billing-management-delete-payment-method', kwargs={'payment_method_id': 'pm_only'})
        response = self.client.delete(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}')

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        response_data = response.json()
        self.assertIn('error', response_data)
        self.assertIn('only payment method', response_data['error'])

    @mock.patch('stripe.PaymentMethod.list')
    @mock.patch('stripe.Customer.retrieve')
    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_delete_default_payment_method_with_others_fails(self, mock_retrieve_pm, mock_retrieve_cust, mock_list_pm):
        """
        Test that deleting the default payment method when others exist returns 409 conflict.
        """
        mock_retrieve_pm.return_value = AttrDict.wrap({
            'id': 'pm_default',
            'customer': self.stripe_customer_id,
        })

        # Mock customer with this method as default
        mock_retrieve_cust.return_value = AttrDict.wrap({
            'id': self.stripe_customer_id,
            'invoice_settings': {'default_payment_method': 'pm_default'},
        })

        # Mock multiple payment methods (only len() is called)
        mock_list_pm.return_value = mock.Mock(data=['pm_default', 'pm_other'])

        url = reverse('api:v1:billing-management-delete-payment-method', kwargs={'payment_method_id': 'pm_default'})
        response = self.client.delete(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}')

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        response_data = response.json()
        self.assertIn('error', response_data)
        self.assertIn('Set another method as default', response_data['error'])

    @ddt.data(
        ('missing_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, None, status.HTTP_403_FORBIDDEN),
        ('nonexistent_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, 'nonexistent', status.HTTP_403_FORBIDDEN),
        ('wrong_role', SYSTEM_ENTERPRISE_LEARNER_ROLE, 'existing', status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_delete_payment_method_rbac(self, _scenario, role, uuid_type, expected_status):
        """
        Test RBAC scenarios for delete payment method endpoint.
        Scenarios: missing_uuid (403), nonexistent_uuid (403), wrong_role (403).
        """
        # Setup authentication with appropriate role
        self.set_jwt_cookie([{
            'system_wide_role': role,
            'context': self.enterprise_uuid,
        }])

        url = reverse('api:v1:billing-management-delete-payment-method', kwargs={'payment_method_id': 'pm_test123'})

        # Build URL with query params based on scenario
        if uuid_type is None:
            response = self.client.delete(url)
        elif uuid_type == 'nonexistent':
            response = self.client.delete(f'{url}?enterprise_customer_uuid={uuid.uuid4()}')
        else:  # 'existing'
            response = self.client.delete(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}')

        self.assertEqual(response.status_code, expected_status)

    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_delete_payment_method_payment_method_not_found(self, mock_retrieve_pm):
        """
        Test that endpoint returns 404 when payment method doesn't exist.
        """
        mock_retrieve_pm.side_effect = stripe.error.InvalidRequestError(
            'No such payment method', param='payment_method'
        )

        url = reverse('api:v1:billing-management-delete-payment-method', kwargs={'payment_method_id': 'pm_invalid'})
        response = self.client.delete(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        response_data = response.json()
        self.assertIn('error', response_data)

    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_delete_payment_method_belongs_to_different_customer(self, mock_retrieve_pm):
        """
        Test that endpoint returns 404 when payment method belongs to different customer.
        """
        mock_retrieve_pm.return_value = AttrDict.wrap({
            'id': 'pm_test123',
            'customer': 'cus_different_customer',
        })

        url = reverse('api:v1:billing-management-delete-payment-method', kwargs={'payment_method_id': 'pm_test123'})
        response = self.client.delete(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        response_data = response.json()
        self.assertIn('error', response_data)
        self.assertIn('does not belong to this customer', response_data['error'])

    @mock.patch('stripe.PaymentMethod.list')
    @mock.patch('stripe.Customer.retrieve')
    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_delete_payment_method_stripe_api_error(self, mock_retrieve_pm, mock_retrieve_cust, mock_list_pm):
        """
        Test that endpoint handles Stripe API errors gracefully.
        """
        mock_retrieve_pm.return_value = AttrDict.wrap({
            'id': 'pm_test123',
            'customer': self.stripe_customer_id,
        })

        # Mock customer
        mock_retrieve_cust.return_value = AttrDict.wrap({
            'id': self.stripe_customer_id,
            'invoice_settings': {'default_payment_method': 'pm_default'},
        })

        # Mock multiple payment methods (only len() is called)
        mock_list_pm.return_value = mock.Mock(data=['pm_default', 'pm_test123'])

        # Mock detach to fail
        with mock.patch('stripe.PaymentMethod.detach', side_effect=stripe.error.StripeError('Connection error')):
            url = reverse('api:v1:billing-management-delete-payment-method', kwargs={'payment_method_id': 'pm_test123'})
            response = self.client.delete(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}')

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        response_data = response.json()
        self.assertIn('error', response_data)
        self.assertIn('Stripe API error', response_data['error'])


@ddt.ddt
class BillingManagementTransactionsTests(BillingManagementBaseTest):
    """
    Tests for the list transactions endpoint.
    """

    def _get_stripe_customer_id(self):
        return 'cus_test_transactions_123'

    @mock.patch('stripe.Charge.retrieve')
    @mock.patch('stripe.Invoice.list')
    def test_list_transactions_success(self, mock_invoice_list, mock_charge_retrieve):
        """
        Test successfully retrieving transaction list.
        """
        # Mock invoice response
        mock_invoices = [
            AttrDict.wrap({
                'id': 'in_test123',
                'created': 1640000000,  # Unix timestamp
                'amount_paid': 9900,
                'currency': 'USD',
                'status': 'paid',
                'description': 'Test Invoice 1',
                'hosted_invoice_url': 'https://stripe.com/invoice/1',
                'charge': 'ch_test123',
            }),
            AttrDict.wrap({
                'id': 'in_test456',
                'created': 1639900000,  # Unix timestamp
                'amount_paid': 5000,
                'currency': 'USD',
                'status': 'open',
                'description': 'Test Invoice 2',
                'hosted_invoice_url': 'https://stripe.com/invoice/2',
                'charge': None,
            }),
        ]
        mock_invoice_list.return_value = mock.Mock(
            data=mock_invoices,
            has_more=False,
        )

        # Mock charge response
        mock_charge = AttrDict.wrap({
            'id': 'ch_test123',
            'receipt_url': 'https://stripe.com/receipt/ch_test123',
        })
        mock_charge_retrieve.return_value = mock_charge

        url = reverse('api:v1:billing-management-list-transactions')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()

        self.assertIn('transactions', response_data)
        self.assertEqual(len(response_data['transactions']), 2)

        # Check first transaction
        tx = response_data['transactions'][0]
        self.assertEqual(tx['id'], 'in_test123')
        self.assertEqual(tx['amount'], 9900)
        self.assertEqual(tx['currency'], 'usd')
        self.assertEqual(tx['status'], 'paid')
        self.assertEqual(tx['description'], 'Test Invoice 1')
        self.assertIsNone(response_data.get('next_page_token'))

    @mock.patch('stripe.Invoice.list')
    def test_list_transactions_with_pagination(self, mock_invoice_list):
        """
        Test pagination with next_page_token.
        """
        # Mock invoice response with has_more=True
        mock_invoices = [
            AttrDict.wrap({
                'id': 'in_test123',
                'created': 1640000000,  # Unix timestamp
                'amount_paid': 9900,
                'currency': 'USD',
                'status': 'paid',
                'description': 'Test Invoice 1',
                'hosted_invoice_url': 'https://stripe.com/invoice/1',
                'charge': None,
            }),
        ]
        mock_invoice_list.return_value = mock.Mock(
            data=mock_invoices,
            has_more=True,
        )

        url = reverse('api:v1:billing-management-list-transactions')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()

        self.assertIn('next_page_token', response_data)
        self.assertIsNotNone(response_data['next_page_token'])
        self.assertEqual(response_data['next_page_token'], 'in_test123')

    @mock.patch('stripe.Invoice.list')
    def test_list_transactions_with_limit_parameter(self, mock_invoice_list):
        """
        Test limit parameter is passed to Stripe API and capped at 25.
        """
        mock_invoices = []
        mock_invoice_list.return_value = mock.Mock(data=mock_invoices, has_more=False)

        url = reverse('api:v1:billing-management-list-transactions')
        response = self.client.get(url, {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'limit': '15'
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_invoice_list.assert_called_once()
        call_kwargs = mock_invoice_list.call_args[1]
        self.assertEqual(call_kwargs['limit'], 15)

    @mock.patch('stripe.Invoice.list')
    def test_list_transactions_limit_capped_at_25(self, mock_invoice_list):
        """
        Test that limit is capped at max of 25.
        """
        mock_invoices = []
        mock_invoice_list.return_value = mock.Mock(data=mock_invoices, has_more=False)

        url = reverse('api:v1:billing-management-list-transactions')
        response = self.client.get(url, {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'limit': '100'
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        call_kwargs = mock_invoice_list.call_args[1]
        self.assertEqual(call_kwargs['limit'], 10)  # Falls back to default when > 25

    @ddt.data(
        ('missing_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, None, status.HTTP_403_FORBIDDEN),
        ('nonexistent_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, 'nonexistent', status.HTTP_403_FORBIDDEN),
        ('wrong_role', SYSTEM_ENTERPRISE_LEARNER_ROLE, 'existing', status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_list_transactions_rbac(self, _scenario, role, uuid_type, expected_status):
        """
        Test RBAC scenarios for list transactions endpoint.
        Scenarios: missing_uuid (403), nonexistent_uuid (403), wrong_role (403).
        """
        # Setup authentication with appropriate role
        self.set_jwt_cookie([{
            'system_wide_role': role,
            'context': self.enterprise_uuid,
        }])

        url = reverse('api:v1:billing-management-list-transactions')

        # Build query params based on scenario
        if uuid_type is None:
            query_params = {}
        elif uuid_type == 'nonexistent':
            query_params = {'enterprise_customer_uuid': str(uuid.uuid4())}
        else:  # 'existing'
            query_params = {'enterprise_customer_uuid': str(self.enterprise_uuid)}

        response = self.client.get(url, query_params)

        self.assertEqual(response.status_code, expected_status)

    @mock.patch('stripe.Invoice.list')
    def test_list_transactions_stripe_api_error(self, mock_invoice_list):
        """
        Test that endpoint handles Stripe API errors gracefully.
        """
        mock_invoice_list.side_effect = stripe.error.StripeError('Connection error')

        url = reverse('api:v1:billing-management-list-transactions')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        response_data = response.json()
        self.assertIn('error', response_data)
        self.assertIn('Stripe API error', response_data['error'])

    @mock.patch('stripe.Charge.retrieve')
    @mock.patch('stripe.Invoice.list')
    def test_list_transactions_empty_list(self, mock_invoice_list, _mock_charge_retrieve):
        """
        Test returning empty transaction list.
        """
        mock_invoice_list.return_value = mock.Mock(data=[], has_more=False)

        url = reverse('api:v1:billing-management-list-transactions')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()

        self.assertIn('transactions', response_data)
        self.assertEqual(len(response_data['transactions']), 0)
        self.assertIsNone(response_data.get('next_page_token'))

    @mock.patch('stripe.Charge.retrieve')
    @mock.patch('stripe.Invoice.list')
    def test_list_transactions_normalizes_status(self, mock_invoice_list, _mock_charge_retrieve):
        """
        Test that invoice statuses are normalized correctly.
        """
        # Mock invoices with various Stripe statuses
        mock_invoices = [
            AttrDict.wrap({
                'id': 'in_paid',
                'created': 1640000000,  # Unix timestamp
                'amount_paid': 1000,
                'currency': 'USD',
                'status': 'paid',
                'description': 'Paid invoice',
                'hosted_invoice_url': 'https://stripe.com/invoice/1',
                'charge': None,
            }),
            AttrDict.wrap({
                'id': 'in_draft',
                'created': 1640000000,  # Unix timestamp
                'amount_paid': 0,
                'currency': 'USD',
                'status': 'draft',
                'description': 'Draft invoice',
                'hosted_invoice_url': 'https://stripe.com/invoice/2',
                'charge': None,
            }),
            AttrDict.wrap({
                'id': 'in_void',
                'created': 1640000000,  # Unix timestamp
                'amount_paid': 0,
                'currency': 'USD',
                'status': 'void',
                'description': 'Void invoice',
                'hosted_invoice_url': 'https://stripe.com/invoice/3',
                'charge': None,
            }),
        ]
        mock_invoice_list.return_value = mock.Mock(data=mock_invoices, has_more=False)

        url = reverse('api:v1:billing-management-list-transactions')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()

        transactions = response_data['transactions']
        self.assertEqual(transactions[0]['status'], 'paid')
        self.assertEqual(transactions[1]['status'], 'open')  # Draft normalized to open
        self.assertEqual(transactions[2]['status'], 'void')


@ddt.ddt
class BillingManagementSubscriptionTests(BillingManagementBaseTest):
    """
    Tests for the get subscription status endpoint.
    """

    def _get_stripe_customer_id(self):
        return 'cus_test_subscription_123'

    def test_get_subscription_success(self):
        """
        Test successfully retrieving subscription from StripeEventSummary.
        """

        # Create subscription event data
        sub_event_data = StripeEventData.objects.create(
            event_id='evt_sub_test123',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )

        # Create StripeEventSummary for subscription (no invoice fields)
        StripeEventSummary.objects.create(
            stripe_event_data=sub_event_data,
            event_id='evt_sub_test123',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test123',
            subscription_status='active',
            subscription_period_start=timezone.now() - timedelta(days=30),
            subscription_period_end=timezone.now() + timedelta(days=335),  # 365 days total
            currency='usd',
        )

        # Create invoice event data for pricing information
        invoice_event_data = StripeEventData.objects.create(
            event_id='evt_invoice_test123',
            event_type='invoice.paid',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )

        # Create StripeEventSummary for invoice with pricing data
        StripeEventSummary.objects.create(
            stripe_event_data=invoice_event_data,
            event_id='evt_invoice_test123',
            event_type='invoice.paid',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test123',
            invoice_unit_amount=50000,  # $500 per license
            invoice_quantity=5,  # 5 licenses
        )

        url = reverse('api:v1:billing-management-get-subscription')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()

        sub = response_data
        self.assertEqual(sub['id'], 'sub_test123')
        self.assertEqual(sub['status'], 'active')
        self.assertFalse(sub['cancel_at_period_end'])
        self.assertEqual(sub['product_type'], 'Teams')
        # Yearly amount: 50000 (unit_amount) * 5 (quantity) = 250000
        self.assertEqual(sub['yearly_amount'], 250000)
        self.assertEqual(sub['license_count'], 5)
        self.assertIsNone(sub['academy_name'])

    @mock.patch('enterprise_access.apps.customer_billing.models.get_cached_academy_data')
    def test_get_subscription_success_essentials_product_type(self, mock_get_cached_academy_data):
        """
        Test retrieving subscription response includes Essentials product_type for academy-backed products,
        along with the academy_name derived from the ssp_product's academy_title.
        """
        mock_get_cached_academy_data.return_value = {'title': 'AI Academy'}
        essentials_product = SspProduct.objects.create(
            slug='ai-academy-yearly',
            stripe_price_lookup_key='ai_academy_yearly_price_test',
            academy_uuid=uuid.uuid4(),
            catalog_query_uuid=uuid.uuid4(),
            is_active=True,
        )
        checkout_intent = CheckoutIntent.objects.create(
            user=self.user,
            enterprise_uuid=str(uuid.uuid4()),
            enterprise_name='Essentials Enterprise',
            enterprise_slug='essentials-enterprise',
            stripe_customer_id='cus_essentials_123',
            state=CheckoutIntentState.PAID,
            quantity=10,
            expires_at=timezone.now() + timedelta(hours=1),
            ssp_product=essentials_product,
        )

        sub_event_data = StripeEventData.objects.create(
            event_id='evt_sub_essentials',
            event_type='customer.subscription.updated',
            checkout_intent=checkout_intent,
            data={'data': {'object': {}}},
        )
        StripeEventSummary.objects.create(
            stripe_event_data=sub_event_data,
            event_id='evt_sub_essentials',
            event_type='customer.subscription.updated',
            checkout_intent=checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_essentials_test',
            subscription_status='active',
            subscription_period_start=timezone.now() - timedelta(days=30),
            subscription_period_end=timezone.now() + timedelta(days=335),
            currency='usd',
        )

        invoice_event_data = StripeEventData.objects.create(
            event_id='evt_invoice_essentials',
            event_type='invoice.paid',
            checkout_intent=checkout_intent,
            data={'data': {'object': {}}},
        )
        StripeEventSummary.objects.create(
            stripe_event_data=invoice_event_data,
            event_id='evt_invoice_essentials',
            event_type='invoice.paid',
            checkout_intent=checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_essentials_test',
            invoice_unit_amount=50000,
            invoice_quantity=5,
        )

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(checkout_intent.enterprise_uuid),
        }])

        url = reverse('api:v1:billing-management-get-subscription')
        response = self.client.get(url, {'enterprise_customer_uuid': str(checkout_intent.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        self.assertEqual(response_data['product_type'], 'Essentials')
        self.assertEqual(response_data['academy_name'], 'AI Academy')
        mock_get_cached_academy_data.assert_called_with(essentials_product.academy_uuid)

    def test_get_product_type_from_checkout_intent_returns_expected_values(self):
        """
        Test the product type helper returns expected values for Teams and Essentials.
        """
        teams_checkout_intent = mock.Mock(ssp_product=mock.Mock(academy_uuid=None))
        essentials_checkout_intent = mock.Mock(ssp_product=mock.Mock(academy_uuid=uuid.uuid4()))

        # pylint: disable=protected-access
        self.assertEqual(
            BillingManagementViewSet._get_product_type_from_checkout_intent(teams_checkout_intent),
            'Teams'
        )
        self.assertEqual(
            BillingManagementViewSet._get_product_type_from_checkout_intent(essentials_checkout_intent),
            'Essentials'
        )
        # pylint: enable=protected-access

    def test_get_subscription_no_active_subscription(self):
        """
        Test returning null subscription when no StripeEventSummary exists.
        """
        # No StripeEventSummary created - should return null

        url = reverse('api:v1:billing-management-get-subscription')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()

        self.assertEqual(response_data, {})

    def test_get_subscription_from_event_summary(self):
        """
        Test retrieving subscription data during trial period with $0 invoice.
        Verifies fallback to upcoming_invoice_amount_due from subscription.created event.
        """

        # Create subscription.created event with upcoming_invoice_amount_due
        sub_created_event_data = StripeEventData.objects.create(
            event_id='evt_sub_created_456',
            event_type='customer.subscription.created',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )

        StripeEventSummary.objects.create(
            stripe_event_data=sub_created_event_data,
            event_id='evt_sub_created_456',
            event_type='customer.subscription.created',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now() - timedelta(hours=1),
            stripe_subscription_id='sub_test456',
            subscription_status='trialing',
            subscription_period_end=timezone.now() + timedelta(days=365),
            currency='usd',
            upcoming_invoice_amount_due=250000,  # What they'll be charged after trial
        )

        # Create $0 trial invoice event
        invoice_event_data = StripeEventData.objects.create(
            event_id='evt_invoice_trial_456',
            event_type='invoice.paid',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )

        StripeEventSummary.objects.create(
            stripe_event_data=invoice_event_data,
            event_id='evt_invoice_trial_456',
            event_type='invoice.paid',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test456',
            invoice_unit_amount=0,  # $0 trial invoice
            invoice_quantity=5,
        )

        url = reverse('api:v1:billing-management-get-subscription')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()

        sub = response_data
        # Verify subscription data is correctly extracted from StripeEventSummary
        self.assertEqual(sub['id'], 'sub_test456')
        self.assertEqual(sub['status'], 'trialing')
        # Yearly amount should fall back to upcoming_invoice_amount_due since invoice is $0
        self.assertEqual(sub['yearly_amount'], 250000)
        self.assertEqual(sub['license_count'], 5)

    @ddt.data(
        ('missing_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, None, status.HTTP_403_FORBIDDEN),
        ('nonexistent_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, 'nonexistent', status.HTTP_403_FORBIDDEN),
        ('wrong_role', SYSTEM_ENTERPRISE_LEARNER_ROLE, 'existing', status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_get_subscription_rbac(self, _scenario, role, uuid_type, expected_status):
        """
        Test RBAC scenarios for get subscription endpoint.
        Scenarios: missing_uuid (403), nonexistent_uuid (403), wrong_role (403).
        """
        # Setup authentication with appropriate role
        self.set_jwt_cookie([{
            'system_wide_role': role,
            'context': self.enterprise_uuid,
        }])

        url = reverse('api:v1:billing-management-get-subscription')

        # Build query params based on scenario
        if uuid_type is None:
            query_params = {}
        elif uuid_type == 'nonexistent':
            query_params = {'enterprise_customer_uuid': str(uuid.uuid4())}
        else:  # 'existing'
            query_params = {'enterprise_customer_uuid': str(self.enterprise_uuid)}

        response = self.client.get(url, query_params)

        self.assertEqual(response.status_code, expected_status)

    def test_get_subscription_yearly_amount_calculation(self):
        """
        Test yearly amount calculation from invoice event data.
        """

        # Create subscription event data
        sub_event_data = StripeEventData.objects.create(
            event_id='evt_sub_yearly_test',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )

        # Create StripeEventSummary for subscription (no invoice fields)
        StripeEventSummary.objects.create(
            stripe_event_data=sub_event_data,
            event_id='evt_sub_yearly_test',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_yearly_test',
            subscription_status='active',
            subscription_period_end=timezone.now() + timedelta(days=365),
            currency='usd',
        )

        # Create invoice event data for pricing
        invoice_event_data = StripeEventData.objects.create(
            event_id='evt_invoice_yearly_test',
            event_type='invoice.paid',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )

        # Create StripeEventSummary for invoice with pricing data
        # Yearly amount is calculated as: invoice_unit_amount * invoice_quantity
        StripeEventSummary.objects.create(
            stripe_event_data=invoice_event_data,
            event_id='evt_invoice_yearly_test',
            event_type='invoice.paid',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_yearly_test',
            invoice_unit_amount=60000,  # $600 per license per year
            invoice_quantity=10,  # 10 licenses
        )

        url = reverse('api:v1:billing-management-get-subscription')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()

        sub = response_data
        # Yearly amount: 60000 * 10 = 600000
        self.assertEqual(sub['yearly_amount'], 600000)
        self.assertEqual(sub['license_count'], 10)


@ddt.ddt
class BillingManagementCancelSubscriptionTests(BillingManagementBaseTest):
    """
    Tests for the cancel subscription endpoint.
    """

    def _get_stripe_customer_id(self):
        return 'cus_test_cancel_123'

    @mock.patch('enterprise_access.apps.api.v1.views.customer_billing.get_ssp_product_pricing')
    @mock.patch('stripe.Subscription.modify')
    @mock.patch('stripe.Subscription.retrieve')
    def test_cancel_subscription_teams_plan_success(
        self, mock_sub_retrieve, mock_sub_modify, mock_get_ssp_pricing
    ):
        """
        Test successfully cancelling a Teams plan subscription.
        """

        # Create StripeEventSummary with active subscription (not scheduled for cancellation)
        event_data = StripeEventData.objects.create(
            event_id='evt_cancel_teams',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )
        _event_summary = StripeEventSummary.objects.create(
            stripe_event_data=event_data,
            event_id='evt_cancel_teams',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test123',
            subscription_status='active',
            subscription_cancel_at=None,  # NOT scheduled for cancellation
            subscription_period_end=timezone.now() + timedelta(days=30),
            currency='usd',
        )

        # Mock SSP product pricing to include this price
        mock_get_ssp_pricing.return_value = {
            'teams_plan': {
                'id': 'price_test123',
                'quantity_range': (5, 30),
            }
        }

        # Mock subscription retrieval for eligibility check
        mock_subscription = {
            'id': 'sub_test123',
            'status': 'active',
            'cancel_at_period_end': False,
            'current_period_end': 1640000000,  # Unix timestamp
            'items': {
                'data': [
                    {
                        'price': {'id': 'price_test123'},
                        'quantity': 5,
                    }
                ]
            },
        }
        mock_sub_retrieve.return_value = AttrDict.wrap(mock_subscription)

        # Mock modified subscription
        mock_modified_subscription = mock_subscription.copy()
        mock_modified_subscription['cancel_at_period_end'] = True
        mock_sub_modify.return_value = AttrDict.wrap(mock_modified_subscription)

        url = reverse('api:v1:billing-management-cancel-subscription')
        response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()

        sub = response_data
        self.assertEqual(sub['id'], 'sub_test123')
        self.assertTrue(sub['cancel_at_period_end'])
        self.assertEqual(sub['product_type'], 'Teams')
        self.assertIsNone(sub['academy_name'])
        mock_sub_modify.assert_called_once_with('sub_test123', cancel_at_period_end=True)

    @mock.patch('enterprise_access.apps.customer_billing.models.get_cached_academy_data')
    @mock.patch('enterprise_access.apps.api.v1.views.customer_billing.get_ssp_product_pricing')
    @mock.patch('stripe.Subscription.modify')
    @mock.patch('stripe.Subscription.retrieve')
    def test_cancel_subscription_essentials_plan_success(
        self, mock_sub_retrieve, mock_sub_modify, mock_get_ssp_pricing, mock_get_cached_academy_data
    ):
        """
        Test successfully cancelling an Essentials plan subscription.
        """
        mock_get_cached_academy_data.return_value = {'title': 'AI Academy'}
        essentials_product = SspProduct.objects.create(
            slug='ai-academy-cancel-yearly',
            stripe_price_lookup_key='ai_academy_cancel_yearly_price_test',
            academy_uuid=uuid.uuid4(),
            catalog_query_uuid=uuid.uuid4(),
            is_active=True,
        )
        self.checkout_intent.ssp_product = essentials_product
        self.checkout_intent.save(update_fields=['ssp_product'])

        # Create StripeEventSummary with active subscription (not scheduled for cancellation)
        event_data = StripeEventData.objects.create(
            event_id='evt_cancel_essentials',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )
        _event_summary = StripeEventSummary.objects.create(
            stripe_event_data=event_data,
            event_id='evt_cancel_essentials',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test123',
            subscription_status='active',
            subscription_cancel_at=None,  # NOT scheduled for cancellation
            subscription_period_end=timezone.now() + timedelta(days=30),
            currency='usd',
        )

        # Mock SSP product pricing to include this price
        mock_get_ssp_pricing.return_value = {
            'essentials_plan': {
                'id': 'price_test123',
                'quantity_range': (1, 100),
            }
        }

        mock_subscription = {
            'id': 'sub_test123',
            'status': 'active',
            'cancel_at_period_end': False,
            'current_period_end': 1640000000,  # Unix timestamp
            'items': {
                'data': [
                    {
                        'price': {'id': 'price_test123'},
                        'quantity': 1,
                    }
                ]
            },
        }
        mock_sub_retrieve.return_value = AttrDict.wrap(mock_subscription)

        mock_modified_subscription = mock_subscription.copy()
        mock_modified_subscription['cancel_at_period_end'] = True
        mock_sub_modify.return_value = AttrDict.wrap(mock_modified_subscription)

        url = reverse('api:v1:billing-management-cancel-subscription')
        response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        sub = response_data
        self.assertTrue(sub['cancel_at_period_end'])
        self.assertEqual(sub['product_type'], 'Essentials')
        self.assertEqual(sub['academy_name'], 'AI Academy')

    @mock.patch('enterprise_access.apps.api.v1.views.customer_billing.get_ssp_product_pricing')
    @mock.patch('stripe.Subscription.retrieve')
    def test_cancel_subscription_learner_credit_plan_fails(self, mock_sub_retrieve, mock_get_ssp_pricing):
        """
        Test that cancelling LearnerCredit plan returns 403.
        """

        # Create StripeEventSummary with active subscription
        event_data = StripeEventData.objects.create(
            event_id='evt_cancel_lc',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )
        _event_summary = StripeEventSummary.objects.create(
            stripe_event_data=event_data,
            event_id='evt_cancel_lc',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test123',
            subscription_status='active',
            subscription_cancel_at=None,
            subscription_period_end=timezone.now() + timedelta(days=30),
            currency='usd',
        )

        # Mock SSP pricing to return empty dict so no prices match
        mock_get_ssp_pricing.return_value = {}

        # Mock subscription retrieval - this subscription has a price NOT in SSP pricing
        mock_subscription = {
            'id': 'sub_test123',
            'status': 'active',
            'cancel_at_period_end': False,
            'current_period_end': 1640000000,  # Unix timestamp
            'items': {
                'data': [
                    {
                        'price': {'id': 'price_not_in_ssp'},  # Not in SSP pricing
                        'quantity': 1,
                    }
                ]
            },
        }
        mock_sub_retrieve.return_value = AttrDict.wrap(mock_subscription)

        url = reverse('api:v1:billing-management-cancel-subscription')
        response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        response_data = response.json()
        self.assertIn('error', response_data)
        self.assertIn('plan type', response_data['error'])

    @mock.patch('enterprise_access.apps.api.v1.views.customer_billing.get_ssp_product_pricing')
    @mock.patch('stripe.Subscription.retrieve')
    def test_cancel_subscription_other_plan_fails(self, mock_sub_retrieve, mock_get_ssp_pricing):
        """
        Test that cancelling Other plan returns 403.
        """

        # Create StripeEventSummary with active subscription
        event_data = StripeEventData.objects.create(
            event_id='evt_cancel_other',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )
        _event_summary = StripeEventSummary.objects.create(
            stripe_event_data=event_data,
            event_id='evt_cancel_other',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test123',
            subscription_status='active',
            subscription_cancel_at=None,
            subscription_period_end=timezone.now() + timedelta(days=30),
            currency='usd',
        )

        # Mock SSP pricing to return empty dict so no prices match
        mock_get_ssp_pricing.return_value = {}

        # Mock subscription retrieval - this subscription has a price NOT in SSP pricing
        mock_subscription = {
            'id': 'sub_test123',
            'status': 'active',
            'cancel_at_period_end': False,
            'current_period_end': 1640000000,  # Unix timestamp
            'items': {
                'data': [
                    {
                        'price': {'id': 'price_other_plan'},  # Not in SSP pricing
                        'quantity': 1,
                    }
                ]
            },
        }
        mock_sub_retrieve.return_value = AttrDict.wrap(mock_subscription)

        url = reverse('api:v1:billing-management-cancel-subscription')
        response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        response_data = response.json()
        self.assertIn('error', response_data)
        self.assertIn('plan type', response_data['error'])

    def test_cancel_subscription_already_cancelling(self):
        """
        Test that cancelling an already-cancelling subscription returns 409.
        """

        # Create StripeEventSummary with subscription already scheduled for cancellation
        event_data = StripeEventData.objects.create(
            event_id='evt_already_cancelling',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )
        _event_summary = StripeEventSummary.objects.create(
            stripe_event_data=event_data,
            event_id='evt_already_cancelling',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test123',
            subscription_status='active',
            subscription_cancel_at=timezone.now() + timedelta(days=30),  # IS scheduled for cancellation
            subscription_period_end=timezone.now() + timedelta(days=30),
            currency='usd',
        )

        url = reverse('api:v1:billing-management-cancel-subscription')
        response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        response_data = response.json()
        self.assertIn('error', response_data)
        self.assertIn('already scheduled', response_data['error'])

    @mock.patch('stripe.Subscription.list')
    def test_cancel_subscription_no_active_subscription(self, mock_sub_list):
        """
        Test that cancelling with no active subscription returns 404.
        """
        mock_sub_list.return_value = mock.Mock(data=[])

        url = reverse('api:v1:billing-management-cancel-subscription')
        response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        response_data = response.json()
        self.assertIn('error', response_data)
        self.assertIn('No active subscription', response_data['error'])

    @ddt.data(
        ('missing_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, None, status.HTTP_403_FORBIDDEN),
        ('nonexistent_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, 'nonexistent', status.HTTP_403_FORBIDDEN),
        ('wrong_role', SYSTEM_ENTERPRISE_LEARNER_ROLE, 'existing', status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_cancel_subscription_rbac(self, _scenario, role, uuid_type, expected_status):
        """
        Test RBAC scenarios for cancel subscription endpoint.
        Scenarios: missing_uuid (403), nonexistent_uuid (403), wrong_role (403).
        """
        # Setup authentication with appropriate role
        self.set_jwt_cookie([{
            'system_wide_role': role,
            'context': self.enterprise_uuid,
        }])

        url = reverse('api:v1:billing-management-cancel-subscription')

        # Build URL with query params based on scenario
        if uuid_type is None:
            response = self.client.post(url)
        elif uuid_type == 'nonexistent':
            response = self.client.post(
                f'{url}?enterprise_customer_uuid={uuid.uuid4()}',
                format='json'
            )
        else:  # 'existing'
            response = self.client.post(
                f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
                format='json'
            )

        self.assertEqual(response.status_code, expected_status)

    @mock.patch('enterprise_access.apps.api.v1.views.customer_billing.get_ssp_product_pricing')
    @mock.patch('stripe.Subscription.retrieve')
    def test_cancel_subscription_stripe_api_error(self, mock_sub_retrieve, mock_get_ssp_pricing):
        """
        Test that endpoint handles Stripe API errors gracefully.
        """

        # Create StripeEventSummary with active subscription
        event_data = StripeEventData.objects.create(
            event_id='evt_cancel_error',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )
        _event_summary = StripeEventSummary.objects.create(
            stripe_event_data=event_data,
            event_id='evt_cancel_error',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test123',
            subscription_status='active',
            subscription_cancel_at=None,
            subscription_period_end=timezone.now() + timedelta(days=30),
            currency='usd',
        )

        # Mock SSP product pricing to include this price
        mock_get_ssp_pricing.return_value = {
            'teams_plan': {
                'id': 'price_test123',
                'quantity_range': (1, 30),
            }
        }

        mock_subscription = {
            'id': 'sub_test123',
            'status': 'active',
            'cancel_at_period_end': False,
            'current_period_end': 1640000000,  # Unix timestamp
            'items': {
                'data': [
                    {
                        'price': {'id': 'price_test123'},
                        'quantity': 1,
                    }
                ]
            },
        }
        mock_sub_retrieve.return_value = AttrDict.wrap(mock_subscription)

        # Mock modify to fail
        with mock.patch('stripe.Subscription.modify', side_effect=stripe.error.StripeError('Connection error')):
            url = reverse('api:v1:billing-management-cancel-subscription')
            response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        response_data = response.json()
        self.assertIn('error', response_data)
        self.assertIn('Stripe API error', response_data['error'])


@ddt.ddt
class BillingManagementReinstateSubscriptionTests(BillingManagementBaseTest):
    """
    Tests for the reinstate subscription endpoint.
    """

    def _get_stripe_customer_id(self):
        return 'cus_test_reinstate_123'

    @mock.patch('enterprise_access.apps.api.v1.views.customer_billing.get_ssp_product_pricing')
    @mock.patch('stripe.Subscription.modify')
    @mock.patch('stripe.Subscription.retrieve')
    def test_reinstate_subscription_teams_plan_success(
        self, mock_sub_retrieve, mock_sub_modify, mock_get_ssp_pricing
    ):
        """
        Test successfully reinstating a Teams plan subscription.
        """

        # Create StripeEventSummary with subscription scheduled for cancellation
        future_cancel_at = timezone.now() + timedelta(days=30)
        event_data = StripeEventData.objects.create(
            event_id='evt_reinstate_teams',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )
        _event_summary = StripeEventSummary.objects.create(
            stripe_event_data=event_data,
            event_id='evt_reinstate_teams',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test123',
            subscription_status='active',
            subscription_cancel_at=future_cancel_at,  # IS scheduled for cancellation
            subscription_period_end=future_cancel_at,
            currency='usd',
        )

        # Mock SSP product pricing to include this price
        mock_get_ssp_pricing.return_value = {
            'teams_plan': {
                'id': 'price_test123',
                'quantity_range': (5, 30),
            }
        }

        # Mock subscription retrieval for eligibility check
        future_period_end = int(timezone.now().timestamp()) + 86400  # 1 day from now
        mock_subscription = {
            'id': 'sub_test123',
            'status': 'active',
            'cancel_at_period_end': True,
            'current_period_end': future_period_end,
            'items': {
                'data': [
                    {
                        'price': {'id': 'price_test123'},
                        'quantity': 5,
                    }
                ]
            },
        }
        mock_sub_retrieve.return_value = AttrDict.wrap(mock_subscription)

        # Mock modified subscription
        mock_modified_subscription = mock_subscription.copy()
        mock_modified_subscription['cancel_at_period_end'] = False
        mock_sub_modify.return_value = AttrDict.wrap(mock_modified_subscription)

        url = reverse('api:v1:billing-management-reinstate-subscription')
        response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()

        sub = response_data
        self.assertEqual(sub['id'], 'sub_test123')
        self.assertFalse(sub['cancel_at_period_end'])
        self.assertEqual(sub['product_type'], 'Teams')
        self.assertIn('product_type', sub)
        self.assertIsNone(sub['academy_name'])
        mock_sub_modify.assert_called_once_with('sub_test123', cancel_at_period_end=False)

    @mock.patch('enterprise_access.apps.customer_billing.models.get_cached_academy_data')
    @mock.patch('enterprise_access.apps.api.v1.views.customer_billing.get_ssp_product_pricing')
    @mock.patch('stripe.Subscription.modify')
    @mock.patch('stripe.Subscription.retrieve')
    def test_reinstate_subscription_essentials_plan_success(
        self, mock_sub_retrieve, mock_sub_modify, mock_get_ssp_pricing, mock_get_cached_academy_data
    ):
        """
        Test successfully reinstating an Essentials plan subscription.
        """
        mock_get_cached_academy_data.return_value = {'title': 'AI Academy'}
        essentials_product = SspProduct.objects.create(
            slug='ai-academy-reinstate-yearly',
            stripe_price_lookup_key='ai_academy_reinstate_yearly_price_test',
            academy_uuid=uuid.uuid4(),
            catalog_query_uuid=uuid.uuid4(),
            is_active=True,
        )
        self.checkout_intent.ssp_product = essentials_product
        self.checkout_intent.save(update_fields=['ssp_product'])

        # Create StripeEventSummary with subscription scheduled for cancellation
        future_cancel_at = timezone.now() + timedelta(days=30)
        event_data = StripeEventData.objects.create(
            event_id='evt_reinstate_essentials',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )
        _event_summary = StripeEventSummary.objects.create(
            stripe_event_data=event_data,
            event_id='evt_reinstate_essentials',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test123',
            subscription_status='active',
            subscription_cancel_at=future_cancel_at,  # IS scheduled for cancellation
            subscription_period_end=future_cancel_at,
            currency='usd',
        )

        # Mock SSP product pricing to include this price
        mock_get_ssp_pricing.return_value = {
            'essentials_plan': {
                'id': 'price_test123',
                'quantity_range': (1, 100),
            }
        }

        future_period_end = int(timezone.now().timestamp()) + 86400
        mock_subscription = {
            'id': 'sub_test123',
            'status': 'active',
            'cancel_at_period_end': True,
            'current_period_end': future_period_end,
            'items': {
                'data': [
                    {
                        'price': {'id': 'price_test123'},
                        'quantity': 1,
                    }
                ]
            },
        }
        mock_sub_retrieve.return_value = AttrDict.wrap(mock_subscription)

        mock_modified_subscription = mock_subscription.copy()
        mock_modified_subscription['cancel_at_period_end'] = False
        mock_sub_modify.return_value = AttrDict.wrap(mock_modified_subscription)

        url = reverse('api:v1:billing-management-reinstate-subscription')
        response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        sub = response_data
        self.assertFalse(sub['cancel_at_period_end'])
        self.assertEqual(sub['product_type'], 'Essentials')
        self.assertEqual(sub['academy_name'], 'AI Academy')

    @mock.patch('stripe.Subscription.retrieve')
    def test_reinstate_subscription_learner_credit_plan_fails(self, mock_sub_retrieve):
        """
        Test that reinstating LearnerCredit plan returns 403.
        """

        # Create StripeEventSummary with subscription scheduled for cancellation
        future_cancel_at = timezone.now() + timedelta(days=30)
        event_data = StripeEventData.objects.create(
            event_id='evt_reinstate_lc',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )
        _event_summary = StripeEventSummary.objects.create(
            stripe_event_data=event_data,
            event_id='evt_reinstate_lc',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test123',
            subscription_status='active',
            subscription_cancel_at=future_cancel_at,  # IS scheduled for cancellation
            subscription_period_end=future_cancel_at,
            currency='usd',
        )

        # Mock subscription retrieval - price NOT in SSP pricing
        future_period_end = int(timezone.now().timestamp()) + 86400
        mock_subscription = {
            'id': 'sub_test123',
            'status': 'active',
            'cancel_at_period_end': True,
            'current_period_end': future_period_end,
            'items': {
                'data': [
                    {
                        'price': {'id': 'price_not_in_ssp'},  # Not in SSP pricing
                        'quantity': 1,
                    }
                ]
            },
        }
        mock_sub_retrieve.return_value = AttrDict.wrap(mock_subscription)

        url = reverse('api:v1:billing-management-reinstate-subscription')
        response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        response_data = response.json()
        self.assertIn('error', response_data)
        self.assertIn('plan type', response_data['error'])

    def test_reinstate_subscription_not_pending_cancellation(self):
        """
        Test that reinstating a subscription not pending cancellation returns 409.
        """

        # Create StripeEventSummary with subscription NOT scheduled for cancellation
        event_data = StripeEventData.objects.create(
            event_id='evt_reinstate_not_pending',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )
        _event_summary = StripeEventSummary.objects.create(
            stripe_event_data=event_data,
            event_id='evt_reinstate_not_pending',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test123',
            subscription_status='active',
            subscription_cancel_at=None,  # NOT scheduled for cancellation
            subscription_period_end=timezone.now() + timedelta(days=30),
            currency='usd',
        )

        url = reverse('api:v1:billing-management-reinstate-subscription')
        response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        response_data = response.json()
        self.assertIn('error', response_data)
        self.assertIn('not currently scheduled', response_data['error'])

    def test_reinstate_subscription_period_ended(self):
        """
        Test that reinstating when period has ended returns 409.
        """

        # Create StripeEventSummary with subscription period already ended
        past_period_end = timezone.now() - timedelta(days=1)  # 1 day ago
        event_data = StripeEventData.objects.create(
            event_id='evt_reinstate_ended',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )
        _event_summary = StripeEventSummary.objects.create(
            stripe_event_data=event_data,
            event_id='evt_reinstate_ended',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test123',
            subscription_status='active',
            subscription_cancel_at=timezone.now() + timedelta(days=1),  # Scheduled but period ended
            subscription_period_end=past_period_end,  # Already ended
            currency='usd',
        )

        url = reverse('api:v1:billing-management-reinstate-subscription')
        response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        response_data = response.json()
        self.assertIn('error', response_data)
        self.assertIn('period has already ended', response_data['error'])

    @mock.patch('stripe.Subscription.list')
    def test_reinstate_subscription_no_active_subscription(self, mock_sub_list):
        """
        Test that reinstating with no active subscription returns 404.
        """
        mock_sub_list.return_value = mock.Mock(data=[])

        url = reverse('api:v1:billing-management-reinstate-subscription')
        response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        response_data = response.json()
        self.assertIn('error', response_data)
        self.assertIn('No active subscription', response_data['error'])

    @ddt.data(
        ('missing_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, None, status.HTTP_403_FORBIDDEN),
        ('nonexistent_uuid', SYSTEM_ENTERPRISE_ADMIN_ROLE, 'nonexistent', status.HTTP_403_FORBIDDEN),
        ('wrong_role', SYSTEM_ENTERPRISE_LEARNER_ROLE, 'existing', status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_reinstate_subscription_rbac(self, _scenario, role, uuid_type, expected_status):
        """
        Test RBAC scenarios for reinstate subscription endpoint.
        Scenarios: missing_uuid (403), nonexistent_uuid (403), wrong_role (403).
        """
        # Setup authentication with appropriate role
        self.set_jwt_cookie([{
            'system_wide_role': role,
            'context': self.enterprise_uuid,
        }])

        url = reverse('api:v1:billing-management-reinstate-subscription')

        # Build URL with query params based on scenario
        if uuid_type is None:
            response = self.client.post(url)
        elif uuid_type == 'nonexistent':
            response = self.client.post(
                f'{url}?enterprise_customer_uuid={uuid.uuid4()}',
                format='json'
            )
        else:  # 'existing'
            response = self.client.post(
                f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
                format='json'
            )

        self.assertEqual(response.status_code, expected_status)

    @mock.patch('enterprise_access.apps.api.v1.views.customer_billing.get_ssp_product_pricing')
    @mock.patch('stripe.Subscription.retrieve')
    def test_reinstate_subscription_stripe_api_error(self, mock_sub_retrieve, mock_get_ssp_pricing):
        """
        Test that endpoint handles Stripe API errors gracefully.
        """

        # Create StripeEventSummary with subscription scheduled for cancellation
        future_cancel_at = timezone.now() + timedelta(days=30)
        event_data = StripeEventData.objects.create(
            event_id='evt_reinstate_error',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )
        _event_summary = StripeEventSummary.objects.create(
            stripe_event_data=event_data,
            event_id='evt_reinstate_error',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test123',
            subscription_status='active',
            subscription_cancel_at=future_cancel_at,  # IS scheduled for cancellation
            subscription_period_end=future_cancel_at,
            currency='usd',
        )

        # Mock SSP product pricing to include this price
        mock_get_ssp_pricing.return_value = {
            'teams_plan': {
                'id': 'price_test123',
                'quantity_range': (1, 30),
            }
        }

        future_period_end = int(timezone.now().timestamp()) + 86400
        mock_subscription = {
            'id': 'sub_test123',
            'status': 'active',
            'cancel_at_period_end': True,
            'current_period_end': future_period_end,
            'items': {
                'data': [
                    {
                        'price': {'id': 'price_test123'},
                        'quantity': 1,
                    }
                ]
            },
        }
        mock_sub_retrieve.return_value = AttrDict.wrap(mock_subscription)

        # Mock modify to fail
        with mock.patch('stripe.Subscription.modify', side_effect=stripe.error.StripeError('Connection error')):
            url = reverse('api:v1:billing-management-reinstate-subscription')
            response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        response_data = response.json()
        self.assertIn('error', response_data)
        self.assertIn('Stripe API error', response_data['error'])


@ddt.ddt
class BillingManagementAdditionalCoverageTests(BillingManagementBaseTest):
    """
    Additional tests to cover edge cases and error paths in billing management endpoints.
    """

    def _get_stripe_customer_id(self):
        return 'cus_test_additional_coverage'

    # Address endpoint edge cases
    @mock.patch('stripe.Customer.retrieve')
    def test_get_address_no_stripe_customer_found(self, mock_customer_retrieve):
        """
        Test get address when Stripe customer is not found.
        """
        mock_customer_retrieve.return_value = None

        url = reverse('api:v1:billing-management-address')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('Stripe customer not found', response.json()['error'])

    @mock.patch('stripe.Customer.retrieve')
    @mock.patch('enterprise_access.apps.api.serializers.customer_billing.BillingAddressResponseSerializer.is_valid')
    def test_get_address_general_exception(self, _mock_is_valid, mock_get_stripe_customer):
        """
        Test get address with general (non-Stripe) exception.
        """
        mock_get_stripe_customer.side_effect = Exception('Unexpected error')

        url = reverse('api:v1:billing-management-address')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn('unexpected error', response.json()['error'])

    @mock.patch('stripe.Customer.modify')
    @mock.patch('enterprise_access.apps.api.serializers.customer_billing.BillingAddressResponseSerializer.is_valid')
    def test_update_address_general_exception(self, _mock_is_valid, mock_customer_modify):
        """
        Test update address with general (non-Stripe) exception.
        """
        mock_customer_modify.side_effect = Exception('Unexpected error')

        url = reverse('api:v1:billing-management-address')
        request_data = {
            'name': 'Test Name',
            'email': 'test@example.com',
            'address_line_1': '123 Test St',
            'city': 'Test City',
            'state': 'TS',
            'postal_code': '12345',
            'country': 'US',
        }
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            request_data,
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn('unexpected error', response.json()['error'])

    # Payment methods edge cases
    @mock.patch('stripe.PaymentMethod.list')
    @mock.patch('stripe.Customer.retrieve')
    def test_list_payment_methods_with_bank_account(self, mock_customer_retrieve, mock_pm_list):
        """
        Test list payment methods with us_bank_account type.
        """
        mock_customer_retrieve.return_value = AttrDict.wrap({
            'id': self.stripe_customer_id,
            'invoice_settings': {'default_payment_method': 'pm_bank_123'},
        })

        mock_pm_list.return_value = mock.Mock(data=[
            AttrDict.wrap({
                'id': 'pm_bank_123',
                'type': 'us_bank_account',
                'us_bank_account': {
                    'last4': '6789',
                    'status_details': {'status': 'verified'},
                },
            }),
        ])

        url = reverse('api:v1:billing-management-payment-methods')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        self.assertEqual(len(response_data['payment_methods']), 1)
        self.assertEqual(response_data['payment_methods'][0]['type'], 'us_bank_account')
        self.assertEqual(response_data['payment_methods'][0]['last4'], '6789')
        self.assertEqual(response_data['payment_methods'][0]['status'], 'verified')

    @mock.patch('stripe.PaymentMethod.list')
    @mock.patch('stripe.Customer.retrieve')
    @mock.patch('enterprise_access.apps.api.serializers.customer_billing.PaymentMethodsListResponseSerializer.is_valid')
    def test_list_payment_methods_general_exception(self, _mock_is_valid, mock_customer_retrieve, _mock_pm_list):
        """
        Test list payment methods with general exception.
        """
        mock_customer_retrieve.side_effect = Exception('Unexpected error')

        url = reverse('api:v1:billing-management-payment-methods')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn('unexpected error', response.json()['error'])

    @mock.patch('stripe.PaymentMethod.attach')
    @mock.patch('stripe.PaymentMethod.retrieve')
    @mock.patch(
        'enterprise_access.apps.api.serializers.customer_billing.AttachPaymentMethodResponseSerializer.is_valid'
    )
    def test_attach_payment_method_general_exception(self, _mock_is_valid, mock_pm_retrieve, _mock_pm_attach):
        """
        Test attach payment method with general exception.
        """
        mock_pm_retrieve.side_effect = Exception('Unexpected error')

        url = reverse('api:v1:billing-management-payment-methods')
        request_data = {'payment_method_id': 'pm_test_123'}
        response = self.client.post(
            f'{url}?enterprise_customer_uuid={self.enterprise_uuid}',
            request_data,
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn('unexpected error', response.json()['error'])

    # Set default payment method edge cases
    @mock.patch('stripe.Customer.modify')
    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_set_default_payment_method_general_exception(self, mock_pm_retrieve, mock_customer_modify):
        """
        Test set default payment method with general exception.
        """
        mock_pm_retrieve.return_value = AttrDict.wrap({'id': 'pm_test_123', 'customer': self.stripe_customer_id})
        mock_customer_modify.side_effect = Exception('Unexpected error')

        url = reverse(
            'api:v1:billing-management-set-default-payment-method',
            kwargs={'payment_method_id': 'pm_test_123'},
        )
        response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn('unexpected error', response.json()['error'])

    # Delete payment method edge cases
    @mock.patch('stripe.PaymentMethod.detach')
    @mock.patch('stripe.PaymentMethod.list')
    @mock.patch('stripe.Customer.retrieve')
    @mock.patch('stripe.PaymentMethod.retrieve')
    def test_delete_payment_method_general_exception(
        self, mock_pm_retrieve, mock_customer_retrieve, mock_pm_list, mock_pm_detach
    ):
        """
        Test delete payment method with general exception.
        """
        mock_pm_retrieve.return_value = AttrDict.wrap({'id': 'pm_test_123', 'customer': self.stripe_customer_id})
        mock_customer_retrieve.return_value = AttrDict.wrap({
            'id': self.stripe_customer_id,
            'invoice_settings': {'default_payment_method': 'pm_default'},
        })
        mock_pm_list.return_value = mock.Mock(data=['pm_test_123', 'pm_default'])
        mock_pm_detach.side_effect = Exception('Unexpected error')

        url = reverse('api:v1:billing-management-delete-payment-method', kwargs={'payment_method_id': 'pm_test_123'})
        response = self.client.delete(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}')

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn('unexpected error', response.json()['error'])

    # Transactions endpoint edge cases
    @mock.patch('stripe.Invoice.list')
    def test_list_transactions_invalid_limit_parameter(self, mock_invoice_list):
        """
        Test list transactions with invalid limit parameter (should default to 10).
        """
        mock_invoice_list.return_value = mock.Mock(data=[], has_more=False)

        url = reverse('api:v1:billing-management-list-transactions')
        response = self.client.get(url, {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'limit': 'invalid',  # Invalid limit
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Verify default limit of 10 was used
        mock_invoice_list.assert_called_once_with(
            customer=self.stripe_customer_id,
            limit=10,
            starting_after=None,
        )

    @mock.patch('stripe.Invoice.list')
    def test_list_transactions_limit_out_of_range(self, mock_invoice_list):
        """
        Test list transactions with limit out of valid range (should default to 10).
        """
        mock_invoice_list.return_value = mock.Mock(data=[], has_more=False)

        url = reverse('api:v1:billing-management-list-transactions')
        response = self.client.get(url, {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'limit': '50',  # Above max of 25
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Verify default limit of 10 was used
        mock_invoice_list.assert_called_once_with(
            customer=self.stripe_customer_id,
            limit=10,
            starting_after=None,
        )

    @mock.patch('stripe.Charge.retrieve')
    @mock.patch('stripe.Invoice.list')
    def test_list_transactions_charge_retrieval_error(self, mock_invoice_list, mock_charge_retrieve):
        """
        Test list transactions when charge retrieval fails (should set receipt_url to None).
        """
        mock_invoice_list.return_value = mock.Mock(
            data=[AttrDict.wrap({
                'id': 'in_test_123',
                'created': 1234567890,
                'amount_paid': 10000,
                'currency': 'usd',
                'status': 'paid',
                'description': 'Test invoice',
                'hosted_invoice_url': 'https://invoice.stripe.com/test',
                'charge': 'ch_test_123',
            })],
            has_more=False
        )
        mock_charge_retrieve.side_effect = stripe.error.StripeError('Charge not found')

        url = reverse('api:v1:billing-management-list-transactions')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        self.assertIsNone(response_data['transactions'][0]['receipt_url'])

    @mock.patch('stripe.Invoice.list')
    def test_list_transactions_invalid_request_error(self, mock_invoice_list):
        """
        Test list transactions with Stripe InvalidRequestError.
        """
        mock_invoice_list.side_effect = stripe.error.InvalidRequestError('Invalid customer', 'customer')

        url = reverse('api:v1:billing-management-list-transactions')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Invalid request', response.json()['error'])

    @mock.patch('stripe.Invoice.list')
    def test_list_transactions_general_exception(self, mock_invoice_list):
        """
        Test list transactions with general exception.
        """
        mock_invoice_list.side_effect = Exception('Unexpected error')

        url = reverse('api:v1:billing-management-list-transactions')
        response = self.client.get(url, {'enterprise_customer_uuid': str(self.enterprise_uuid)})

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn('unexpected error', response.json()['error'])

    def test_normalize_invoice_status_uncollectible(self):
        """
        Test _normalize_invoice_status with uncollectible status.
        """

        result = BillingManagementViewSet._normalize_invoice_status('uncollectible')  # pylint: disable=protected-access
        self.assertEqual(result, 'uncollectible')

    # Subscription endpoint edge cases
    # Cancel subscription edge cases
    @mock.patch('stripe.Subscription.retrieve')
    def test_cancel_subscription_invalid_request_error(self, mock_sub_retrieve):
        """
        Test cancel subscription with Stripe InvalidRequestError.
        """

        # Create StripeEventSummary with active subscription
        event_data = StripeEventData.objects.create(
            event_id='evt_cancel_invalid',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )
        _event_summary = StripeEventSummary.objects.create(
            stripe_event_data=event_data,
            event_id='evt_cancel_invalid',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test123',
            subscription_status='active',
            subscription_cancel_at=None,
            subscription_period_end=timezone.now() + timedelta(days=30),
            currency='usd',
        )

        mock_sub_retrieve.side_effect = stripe.error.InvalidRequestError('Invalid customer', 'customer')

        url = reverse('api:v1:billing-management-cancel-subscription')
        response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Invalid request', response.json()['error'])

    @mock.patch('stripe.Subscription.retrieve')
    def test_cancel_subscription_general_exception(self, mock_sub_retrieve):
        """
        Test cancel subscription with general exception.
        """

        # Create StripeEventSummary with active subscription
        event_data = StripeEventData.objects.create(
            event_id='evt_cancel_exception',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )
        _event_summary = StripeEventSummary.objects.create(
            stripe_event_data=event_data,
            event_id='evt_cancel_exception',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test123',
            subscription_status='active',
            subscription_cancel_at=None,
            subscription_period_end=timezone.now() + timedelta(days=30),
            currency='usd',
        )

        mock_sub_retrieve.side_effect = Exception('Unexpected error')

        url = reverse('api:v1:billing-management-cancel-subscription')
        response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn('unexpected error', response.json()['error'])

    # Reinstate subscription edge cases
    @mock.patch('stripe.Subscription.retrieve')
    def test_reinstate_subscription_invalid_request_error(self, mock_sub_retrieve):
        """
        Test reinstate subscription with Stripe InvalidRequestError.
        """

        # Create StripeEventSummary with subscription scheduled for cancellation
        future_cancel_at = timezone.now() + timedelta(days=30)
        event_data = StripeEventData.objects.create(
            event_id='evt_reinstate_invalid',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )
        _event_summary = StripeEventSummary.objects.create(
            stripe_event_data=event_data,
            event_id='evt_reinstate_invalid',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test123',
            subscription_status='active',
            subscription_cancel_at=future_cancel_at,
            subscription_period_end=future_cancel_at,
            currency='usd',
        )

        mock_sub_retrieve.side_effect = stripe.error.InvalidRequestError('Invalid customer', 'customer')

        url = reverse('api:v1:billing-management-reinstate-subscription')
        response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Invalid request', response.json()['error'])

    @mock.patch('stripe.Subscription.retrieve')
    def test_reinstate_subscription_general_exception(self, mock_sub_retrieve):
        """
        Test reinstate subscription with general exception.
        """

        # Create StripeEventSummary with subscription scheduled for cancellation
        future_cancel_at = timezone.now() + timedelta(days=30)
        event_data = StripeEventData.objects.create(
            event_id='evt_reinstate_exception',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )
        _event_summary = StripeEventSummary.objects.create(
            stripe_event_data=event_data,
            event_id='evt_reinstate_exception',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_test123',
            subscription_status='active',
            subscription_cancel_at=future_cancel_at,
            subscription_period_end=future_cancel_at,
            currency='usd',
        )

        mock_sub_retrieve.side_effect = Exception('Unexpected error')

        url = reverse('api:v1:billing-management-reinstate-subscription')
        response = self.client.post(f'{url}?enterprise_customer_uuid={self.enterprise_uuid}', format='json')

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn('unexpected error', response.json()['error'])

    # Payment method status helpers
    def test_get_payment_method_status_failed(self):
        """
        Test _get_payment_method_status with failed bank account verification.
        """

        payment_method = {
            'type': 'us_bank_account',
            'us_bank_account': {
                'status_details': {'status': 'verification_failed'}
            }
        }
        result = BillingManagementViewSet._get_payment_method_status(payment_method)  # pylint: disable=protected-access
        self.assertEqual(result, 'failed')

        # Test with 'errored' status
        payment_method['us_bank_account']['status_details']['status'] = 'errored'
        result = BillingManagementViewSet._get_payment_method_status(payment_method)  # pylint: disable=protected-access
        self.assertEqual(result, 'failed')

    # _get_plan_type_from_subscription helper tests
    @ddt.data(
        # (subscription_dict, price_return, product_return, price_side_effect, expected)
        # no items → 'Other', no Stripe calls made
        ({'items': {'data': []}}, None, None, None, 'Other'),
        # item present but no price ID → 'Other', no Stripe calls made
        ({'items': {'data': [{'price': {}}]}}, None, None, None, 'Other'),
        # plan_type found in price metadata → returned directly
        (
            {'items': {'data': [{'price': {'id': 'price_123'}}]}},
            {'metadata': {'plan_type': 'Teams'}, 'product': None},
            None, None, 'Teams',
        ),
        # plan_type absent in price metadata, found in product metadata → returned from product
        (
            {'items': {'data': [{'price': {'id': 'price_123'}}]}},
            {'metadata': {}, 'product': 'prod_123'},
            {'metadata': {'plan_type': 'Essentials'}},
            None, 'Essentials',
        ),
        # plan_type absent from both price and product metadata → 'Other'
        (
            {'items': {'data': [{'price': {'id': 'price_123'}}]}},
            {'metadata': {}, 'product': 'prod_123'},
            {'metadata': {}},
            None, 'Other',
        ),
        # price has no product ID and price metadata lacks plan_type → 'Other'
        (
            {'items': {'data': [{'price': {'id': 'price_123'}}]}},
            {'metadata': {}, 'product': None},
            None, None, 'Other',
        ),
        # StripeError raised → 'Other'
        (
            {'items': {'data': [{'price': {'id': 'price_123'}}]}},
            None, None, stripe.error.StripeError('Stripe unavailable'), 'Other',
        ),
        # unexpected Exception raised → 'Other'
        (
            {'items': {'data': [{'price': {'id': 'price_123'}}]}},
            None, None, Exception('Unexpected failure'), 'Other',
        ),
    )
    @ddt.unpack
    @mock.patch('stripe.Product.retrieve')
    @mock.patch('stripe.Price.retrieve')
    def test_get_plan_type_from_subscription(
        self,
        subscription_dict,
        price_return,
        product_return,
        price_side_effect,
        expected,
        mock_price_retrieve,
        mock_product_retrieve,
    ):
        """
        Tests all branches of _get_plan_type_from_subscription.
        """
        if price_side_effect is not None:
            mock_price_retrieve.side_effect = price_side_effect
        elif price_return is not None:
            mock_price_retrieve.return_value = AttrDict.wrap(price_return)

        if product_return is not None:
            mock_product_retrieve.return_value = AttrDict.wrap(product_return)

        sub = AttrDict.wrap(subscription_dict)
        result = BillingManagementViewSet._get_plan_type_from_subscription(sub)  # pylint: disable=protected-access
        self.assertEqual(result, expected)

    # _get_yearly_amount helper tests
    @ddt.data(
        # (subscription_dict, price_retrieve_return, price_side_effect, expected)
        # no items → 0
        ({'items': {'data': []}}, None, None, 0),
        # unit_amount inline, yearly billing, quantity 2
        (
            {'items': {'data': [{'price': {'unit_amount': 1000, 'recurring': {'interval': 'year'}}, 'quantity': 2}]}},
            None, None, 2000,
        ),
        # unit_amount inline, monthly billing, quantity 1
        (
            {'items': {'data': [{'price': {'unit_amount': 500, 'recurring': {'interval': 'month'}}, 'quantity': 1}]}},
            None, None, 6000,
        ),
        # unit_amount inline, weekly billing, quantity 1
        (
            {'items': {'data': [{'price': {'unit_amount': 100, 'recurring': {'interval': 'week'}}, 'quantity': 1}]}},
            None, None, 5200,
        ),
        # unit_amount inline, unknown billing period → 0
        (
            {'items': {'data': [{'price': {'unit_amount': 1000, 'recurring': {'interval': 'day'}}, 'quantity': 1}]}},
            None, None, 0,
        ),
        # no unit_amount in price, has price_id → retrieves price and calculates (monthly)
        (
            {'items': {'data': [{'price': {'id': 'price_123'}, 'quantity': 3}]}},
            {'unit_amount': 5000, 'recurring': {'interval': 'month'}},
            None, 180000,
        ),
        # no unit_amount in price, has price_id → StripeError on retrieve → item skipped
        (
            {'items': {'data': [{'price': {'id': 'price_123'}, 'quantity': 1}]}},
            None, stripe.error.StripeError('Stripe error'), 0,
        ),
        # no unit_amount in price, no price_id → unit_amount defaults to 0
        (
            {'items': {'data': [{'price': {}, 'quantity': 5}]}},
            None, None, 0,
        ),
        # multiple items summed: yearly (1000*1) + monthly (500*2*12) = 13000
        (
            {'items': {'data': [
                {'price': {'unit_amount': 1000, 'recurring': {'interval': 'year'}}, 'quantity': 1},
                {'price': {'unit_amount': 500, 'recurring': {'interval': 'month'}}, 'quantity': 2},
            ]}},
            None, None, 13000,
        ),
    )
    @ddt.unpack
    @mock.patch('stripe.Price.retrieve')
    def test_get_yearly_amount(
        self,
        subscription_dict,
        price_retrieve_return,
        price_side_effect,
        expected,
        mock_price_retrieve,
    ):
        """
        Tests all branches of _get_yearly_amount.
        """
        if price_side_effect is not None:
            mock_price_retrieve.side_effect = price_side_effect
        elif price_retrieve_return is not None:
            mock_price_retrieve.return_value = AttrDict.wrap(price_retrieve_return)

        sub = AttrDict.wrap(subscription_dict)
        result = BillingManagementViewSet._get_yearly_amount(sub)  # pylint: disable=protected-access
        self.assertEqual(result, expected)

    def test_get_yearly_amount_general_exception(self):
        """
        Returns 0 when subscription.to_dict() raises an unexpected exception.
        """
        sub = mock.Mock()
        sub.to_dict.side_effect = Exception('Unexpected failure')
        result = BillingManagementViewSet._get_yearly_amount(sub)  # pylint: disable=protected-access
        self.assertEqual(result, 0)

    # _get_license_count helper tests
    @ddt.data(
        # empty items list → 0
        ({'items': {'data': []}}, 0),
        # single item with quantity
        ({'items': {'data': [{'quantity': 5}]}}, 5),
        # multiple items summed
        ({'items': {'data': [{'quantity': 3}, {'quantity': 7}]}}, 10),
        # item missing quantity key → defaults to 0
        ({'items': {'data': [{'quantity': 4}, {}]}}, 4),
    )
    @ddt.unpack
    def test_get_license_count(self, subscription_dict, expected):
        """
        Tests all branches of _get_license_count.
        """
        sub = AttrDict.wrap(subscription_dict)
        result = BillingManagementViewSet._get_license_count(sub)  # pylint: disable=protected-access
        self.assertEqual(result, expected)

    def test_get_license_count_general_exception(self):
        """
        Returns 0 when subscription.to_dict() raises an unexpected exception.
        """
        sub = mock.Mock()
        sub.to_dict.side_effect = Exception('Unexpected failure')
        result = BillingManagementViewSet._get_license_count(sub)  # pylint: disable=protected-access
        self.assertEqual(result, 0)


@ddt.ddt
class BillingManagementEnterpriseStripeCustomerTests(BillingManagementBaseTest):
    """
    Billing management finds the Stripe customer in EnterpriseStripeCustomer first, then on the newest CheckoutIntent
    (ENT-12509). The subscription actions still read the CheckoutIntent itself.
    """
    MAPPED_CUSTOMER_ID = 'cus_test_mapped_customer'

    def _get_stripe_customer_id(self):
        return 'cus_test_checkout_intent_customer'

    def _map_enterprise(self):
        EnterpriseStripeCustomerFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            stripe_customer_id=self.MAPPED_CUSTOMER_ID,
        )

    def _create_subscription_summary(self, subscription_cancel_at=None):
        """
        Record an active subscription on the enterprise's CheckoutIntent.
        """
        event_data = StripeEventData.objects.create(
            event_id='evt_mapped_sub',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'data': {'object': {}}},
        )
        StripeEventSummary.objects.create(
            stripe_event_data=event_data,
            event_id='evt_mapped_sub',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            stripe_event_created_at=timezone.now(),
            stripe_subscription_id='sub_checkout_intent',
            subscription_status='active',
            subscription_cancel_at=subscription_cancel_at,
            subscription_period_end=timezone.now() + timedelta(days=30),
            currency='usd',
        )

    @ddt.data(
        # The mapping wins over the CheckoutIntent.
        (True, True, MAPPED_CUSTOMER_ID),
        # Without a mapping, the CheckoutIntent's customer is used, as before.
        (False, True, 'cus_test_checkout_intent_customer'),
        # Learner-Credit-only enterprise (provisioned through Salesforce): a mapping and no CheckoutIntent.
        (True, False, MAPPED_CUSTOMER_ID),
        # Neither: 404 without calling Stripe, as before.
        (False, False, None),
    )
    @ddt.unpack
    @mock.patch('stripe.Customer.retrieve')
    @mock.patch('stripe.PaymentMethod.list')
    def test_payment_methods_resolve_stripe_customer(
        self, has_mapping, has_checkout_intent, expected_customer_id, mock_payment_method_list, mock_customer_retrieve,
    ):
        if has_mapping:
            self._map_enterprise()
        if not has_checkout_intent:
            self.checkout_intent.delete()
        mock_customer_retrieve.return_value = AttrDict.wrap(
            {'invoice_settings': {'default_payment_method': 'pm_card_visa'}},
        )
        mock_payment_method_list.return_value = mock.Mock(data=[
            AttrDict.wrap({
                'id': 'pm_card_visa',
                'type': 'card',
                'card': {'last4': '4242', 'brand': 'visa', 'exp_month': 12, 'exp_year': 2030},
            }),
        ])

        url = reverse('api:v1:billing-management-payment-methods')
        response = self.client.get(url, {'enterprise_customer_uuid': self.enterprise_uuid})

        if expected_customer_id is None:
            self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
            self.assertEqual(response.json(), {'error': 'Stripe customer not found for this enterprise'})
            mock_customer_retrieve.assert_not_called()
            return
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment_methods = response.json()['payment_methods']
        self.assertEqual([(pm['id'], pm['is_default']) for pm in payment_methods], [('pm_card_visa', True)])
        mock_customer_retrieve.assert_called_once_with(expected_customer_id)
        mock_payment_method_list.assert_called_once_with(customer=expected_customer_id, limit=100)

    @mock.patch('stripe.Invoice.list')
    @mock.patch('stripe.Customer.retrieve')
    def test_learner_credit_only_enterprise_gets_address_and_transactions(
        self, mock_customer_retrieve, mock_invoice_list,
    ):
        """
        An enterprise with a mapping and no CheckoutIntent gets its mapped customer's address and invoices.
        """
        self._map_enterprise()
        self.checkout_intent.delete()
        mock_customer_retrieve.return_value = AttrDict.wrap({
            'id': self.MAPPED_CUSTOMER_ID, 'name': 'Billing Contact', 'email': 'billing@example.com',
            'phone': None, 'address': None,
        })
        mock_invoice_list.return_value = mock.Mock(data=[], has_more=False)
        query = {'enterprise_customer_uuid': self.enterprise_uuid}

        address_response = self.client.get(reverse('api:v1:billing-management-address'), query)
        transactions_response = self.client.get(reverse('api:v1:billing-management-list-transactions'), query)

        self.assertEqual(address_response.status_code, status.HTTP_200_OK)
        self.assertEqual(address_response.json()['email'], 'billing@example.com')
        mock_customer_retrieve.assert_called_once_with(self.MAPPED_CUSTOMER_ID)
        self.assertEqual(transactions_response.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_invoice_list.call_args.kwargs['customer'], self.MAPPED_CUSTOMER_ID)

    @ddt.data(
        ('get', 'api:v1:billing-management-get-subscription', 'Checkout Intent not found for this enterprise'),
        ('post', 'api:v1:billing-management-cancel-subscription', 'Stripe customer not found for this enterprise'),
        ('post', 'api:v1:billing-management-reinstate-subscription', 'Stripe customer not found for this enterprise'),
    )
    @ddt.unpack
    def test_subscription_actions_still_need_a_checkout_intent(self, method, url_name, expected_error):
        """
        A mapping alone doesn't give an enterprise a subscription: the subscription actions answer 404, as before.
        """
        self._map_enterprise()
        self.checkout_intent.delete()

        response = getattr(self.client, method)(f'{reverse(url_name)}?enterprise_customer_uuid={self.enterprise_uuid}')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.json(), {'error': expected_error})

    @ddt.data(
        ('get', 'api:v1:billing-management-get-subscription', None, None),
        # Cancel: not yet scheduled for cancellation.
        ('post', 'api:v1:billing-management-cancel-subscription', None, True),
        # Reinstate: scheduled for cancellation at the end of the period.
        ('post', 'api:v1:billing-management-reinstate-subscription', timedelta(days=30), False),
    )
    @ddt.unpack
    @mock.patch('enterprise_access.apps.api.v1.views.customer_billing.get_ssp_product_pricing')
    @mock.patch('stripe.Subscription.modify')
    @mock.patch('stripe.Subscription.retrieve')
    def test_subscription_actions_read_checkout_intent_when_mapped(
        self, method, url_name, cancel_in, expected_cancel_at_period_end,
        mock_subscription_retrieve, mock_subscription_modify, mock_get_ssp_pricing,
    ):
        """
        A mapping to another customer doesn't change which subscription is read, cancelled or reinstated.
        """
        self._map_enterprise()
        self._create_subscription_summary(
            subscription_cancel_at=timezone.now() + cancel_in if cancel_in else None,
        )
        mock_get_ssp_pricing.return_value = {'teams_plan': {'id': 'price_test123', 'quantity_range': (5, 30)}}
        subscription = {
            'id': 'sub_checkout_intent',
            'status': 'active',
            'cancel_at_period_end': not expected_cancel_at_period_end,
            'current_period_end': 1640000000,
            'items': {'data': [{'price': {'id': 'price_test123'}, 'quantity': 5}]},
        }
        mock_subscription_retrieve.return_value = AttrDict.wrap(subscription)
        mock_subscription_modify.return_value = AttrDict.wrap(
            {**subscription, 'cancel_at_period_end': expected_cancel_at_period_end},
        )

        response = getattr(self.client, method)(f'{reverse(url_name)}?enterprise_customer_uuid={self.enterprise_uuid}')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual((response.json()['id'], response.json()['product_type']), ('sub_checkout_intent', 'Teams'))
        if expected_cancel_at_period_end is not None:
            mock_subscription_modify.assert_called_once_with(
                'sub_checkout_intent', cancel_at_period_end=expected_cancel_at_period_end,
            )


class CreateCheckoutSessionViewTests(APITest):
    """
    Tests for the CustomerBillingViewSet.create_checkout_session endpoint.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse('api:v1:customer-billing-create-checkout-session')

    def tearDown(self):
        CheckoutIntent.objects.all().delete()
        super().tearDown()

    @mock.patch(
        'enterprise_access.apps.customer_billing.models.SspProduct.enterprise_catalog_metadata',
        new_callable=mock.PropertyMock,
        return_value={'title': 'Open Courses', 'catalog_query_id': 1},
    )
    @mock.patch('enterprise_access.apps.customer_billing.api.create_subscription_checkout_session')
    @mock.patch('enterprise_access.apps.customer_billing.api.CheckoutIntent.create_intent')
    @mock.patch('enterprise_access.apps.customer_billing.api.CheckoutSessionInputValidator.validate')
    def test_create_checkout_session_returns_client_secret_from_dict(
        self,
        mock_validate,
        mock_create_intent,
        mock_create_stripe_session,
        mock_enterprise_catalog_metadata,
    ):
        """
        client_secret is read via dict access from the dict returned by create_subscription_checkout_session,
        not via attribute access. Regression test for the .to_dict() change in stripe_api.py.
        """
        self.set_jwt_cookie()
        mock_validate.return_value = {}  # no validation errors
        mock_intent = mock.MagicMock()
        mock_intent.id = 1
        mock_create_intent.return_value = mock_intent
        mock_create_stripe_session.return_value = {
            'id': 'cs_test_abc',
            'customer': 'cus_test_123',
            'client_secret': 'cs_test_abc_secret_xyz',
        }

        # Prevent live Stripe pricing lookups during checkout flow
        with mock.patch('enterprise_access.apps.customer_billing.api.get_ssp_product_pricing') as mock_get_pricing:
            mock_get_pricing.return_value = {
                'quarterly_license_plan': {'id': 'price_test_quarterly', 'quantity_range': (5, 30)}
            }

            response = self.client.post(
                self.url,
                data={
                    'admin_email': self.user.email,
                    'enterprise_slug': 'test-slug',
                    'company_name': 'Test Co',
                    'quantity': 5,
                    'stripe_price_id': 'price_abc123',
                    'ssp_product': 'quarterly_license_plan',
                },
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            response.data['checkout_session_client_secret'],
            'cs_test_abc_secret_xyz',
        )
        mock_enterprise_catalog_metadata.assert_called()


@ddt.ddt
@override_settings(FEATURE_TOP_UP_LEARNER_CREDIT=True)
class LearnerCreditTopUpEligibilityTests(APITest):
    """
    Tests for GET /api/v1/learner-credit-top-ups/eligibility/.
    """

    def setUp(self):
        super().setUp()
        self.enterprise_uuid = uuid.uuid4()
        self.url = reverse('api:v1:learner-credit-top-ups-eligibility')
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_uuid),
        }])
        subsidy = {
            'is_active': True,
            'expiration_datetime': (timezone.now() + timedelta(days=100)).isoformat(),
        }
        patcher = mock.patch(SUBSIDY_CLIENT_PATH)
        self.mock_retrieve_subsidy = patcher.start().return_value.retrieve_subsidy
        self.mock_retrieve_subsidy.return_value = subsidy
        self.addCleanup(patcher.stop)

    def _policy(self, **kwargs):
        return PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid, **kwargs,
        )

    def _get(self, **params):
        params.setdefault('enterprise_customer_uuid', str(self.enterprise_uuid))
        return self.client.get(self.url, params)

    def test_single_policy_eligible(self):
        policy = self._policy()
        response = self._get(policy_uuid=str(policy.uuid))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        [result] = response.json()['results']
        self.assertEqual(result['policy_uuid'], str(policy.uuid))
        self.assertTrue(result['is_eligible'])
        self.assertEqual(result['reasons'], [])
        self.assertIsNotNone(result['subsidy_expiration_datetime'])

    def test_ineligible_returns_200_with_reasons(self):
        policy = self._policy()
        self.mock_retrieve_subsidy.return_value = {
            'is_active': True,
            'expiration_datetime': (timezone.now() + timedelta(days=13)).isoformat(),
        }
        response = self._get(policy_uuid=str(policy.uuid))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        [result] = response.json()['results']
        self.assertFalse(result['is_eligible'])
        self.assertEqual([r['error_code'] for r in result['reasons']], ['budget_expiring_soon'])
        self.assertTrue(result['reasons'][0]['message'])

    def test_account_standing_not_eligible(self):
        policy = self._policy()
        with mock.patch(ACCOUNT_STANDING_PATH, return_value=False):
            [result] = self._get(policy_uuid=str(policy.uuid)).json()['results']
        self.assertEqual([r['error_code'] for r in result['reasons']], ['account_not_eligible'])

    def test_inactive_policy_by_uuid_is_404_with_error_code(self):
        policy = self._policy(active=False)
        response = self._get(policy_uuid=str(policy.uuid))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.json()['error_code'], 'budget_not_found')
        self.mock_retrieve_subsidy.assert_not_called()

    def test_inactive_subsidy_reports_budget_not_active(self):
        policy = self._policy()
        self.mock_retrieve_subsidy.return_value = {
            'is_active': False,
            'expiration_datetime': (timezone.now() + timedelta(days=100)).isoformat(),
        }
        [result] = self._get(policy_uuid=str(policy.uuid)).json()['results']
        self.assertEqual([r['error_code'] for r in result['reasons']], ['budget_not_active'])

    def test_no_policy_uuid_returns_active_true_policies_of_enterprise_only(self):
        active_1 = self._policy()
        active_2 = self._policy()
        self._policy(active=False)
        retired = self._policy(retired=True)  # still active=True: listed, but reports budget_not_active
        PerLearnerSpendCapLearnerCreditAccessPolicyFactory()  # other enterprise
        response = self._get()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned = {r['policy_uuid'] for r in response.json()['results']}
        self.assertEqual(returned, {str(active_1.uuid), str(active_2.uuid), str(retired.uuid)})
        by_uuid = {r['policy_uuid']: r for r in response.json()['results']}
        self.assertEqual([r['error_code'] for r in by_uuid[str(retired.uuid)]['reasons']], ['budget_not_active'])

    def test_policy_uuid_filters_to_one_policy(self):
        policy = self._policy()
        self._policy()
        results = self._get(policy_uuid=str(policy.uuid)).json()['results']
        self.assertEqual([r['policy_uuid'] for r in results], [str(policy.uuid)])

    def test_every_failing_reason_is_reported(self):
        policy = self._policy()
        self.mock_retrieve_subsidy.return_value = {
            'is_active': False,
            'expiration_datetime': (timezone.now() + timedelta(days=3)).isoformat(),
        }
        with mock.patch(ACCOUNT_STANDING_PATH, return_value=False):
            [result] = self._get(policy_uuid=str(policy.uuid)).json()['results']
        self.assertEqual(
            [r['error_code'] for r in result['reasons']],
            ['budget_not_active', 'budget_expiring_soon', 'account_not_eligible'],
        )

    def test_exactly_14_days_left_is_eligible(self):
        policy = self._policy()
        self.mock_retrieve_subsidy.return_value = {
            'is_active': True,
            'expiration_datetime': (timezone.now() + timedelta(days=14, minutes=1)).isoformat(),
        }
        [result] = self._get(policy_uuid=str(policy.uuid)).json()['results']
        self.assertTrue(result['is_eligible'])

    def test_subsidy_unreachable_is_503_not_500(self):
        self._policy()
        self.mock_retrieve_subsidy.side_effect = requests.exceptions.ConnectionError('Name or service not known')
        self.assertEqual(self._get().status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    def test_subsidy_server_error_is_503_not_budget_not_active(self):
        policy = self._policy()
        self._subsidies_failing_with({policy.subsidy_uuid}, 500)
        response = self._get()
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        # The generic SubsidyAPIUnavailableException body: upstream error details never reach the client.
        self.assertEqual(response.json(), {'detail': SubsidyAPIUnavailableException.default_detail})

    def _subsidies_failing_with(self, failing_subsidy_uuids, status_code):
        """ Make retrieve_subsidy fail with ``status_code`` for the given subsidies and succeed for the rest. """
        healthy = self.mock_retrieve_subsidy.return_value

        def retrieve_subsidy(subsidy_uuid):
            if subsidy_uuid in failing_subsidy_uuids:
                response = requests.Response()
                response.status_code = status_code
                raise requests.exceptions.HTTPError(f'{status_code} error', response=response)
            return healthy
        self.mock_retrieve_subsidy.side_effect = retrieve_subsidy

    @ddt.data(403, 404)
    def test_unreachable_subsidy_reports_budget_not_active_and_healthy_budgets_still_returned(self, status_code):
        """ One soft-deleted (403) or missing (404) subsidy must not turn the whole response into a 503. """
        healthy = self._policy()
        dead = self._policy()
        self._subsidies_failing_with({dead.subsidy_uuid}, status_code)
        response = self._get()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_uuid = {r['policy_uuid']: r for r in response.json()['results']}
        self.assertTrue(by_uuid[str(healthy.uuid)]['is_eligible'])
        self.assertFalse(by_uuid[str(dead.uuid)]['is_eligible'])
        self.assertEqual([r['error_code'] for r in by_uuid[str(dead.uuid)]['reasons']], ['budget_not_active'])

    @ddt.data(403, 404)
    def test_every_subsidy_unreachable_is_503(self, status_code):
        """ A 403 from every subsidy looks like our own credentials failing: report an outage, not empty results. """
        first, second = self._policy(), self._policy()
        self._subsidies_failing_with({first.subsidy_uuid, second.subsidy_uuid}, status_code)
        response = self._get()
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.json(), {'detail': SubsidyAPIUnavailableException.default_detail})

    @ddt.data(403, 404)
    def test_requested_budget_with_unreachable_subsidy_reports_budget_not_active(self, status_code):
        """ An explicitly requested budget whose subsidy is gone reports why, rather than a lasting 503. """
        policy = self._policy()
        self._subsidies_failing_with({policy.subsidy_uuid}, status_code)
        response = self._get(policy_uuid=str(policy.uuid))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        [result] = response.json()['results']
        self.assertEqual([r['error_code'] for r in result['reasons']], ['budget_not_active'])

    def test_requested_budget_with_subsidy_server_error_is_503(self):
        policy = self._policy()
        self._subsidies_failing_with({policy.subsidy_uuid}, 500)
        self.assertEqual(self._get(policy_uuid=str(policy.uuid)).status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    def test_server_error_from_one_subsidy_is_503_even_with_healthy_budgets(self):
        """ A 5xx is not isolated per subsidy: it means enterprise-subsidy itself is failing. """
        self._policy()
        dead = self._policy()
        self._subsidies_failing_with({dead.subsidy_uuid}, 500)
        self.assertEqual(self._get().status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    def test_no_active_policies_is_404_with_error_code(self):
        self._policy(active=False)  # inactive budgets are not listed
        response = self._get()
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.json()['error_code'], 'no_budgets_found')
        self.assertIn(str(self.enterprise_uuid), response.json()['detail'])

    def test_operator_with_unknown_enterprise_is_404_with_error_code(self):
        """ An operator may query any enterprise, so a mistyped enterprise_customer_uuid must still report an error. """
        self.set_jwt_cookie([{'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': '*'}])
        unknown_enterprise_uuid = str(uuid.uuid4())
        response = self._get(enterprise_customer_uuid=unknown_enterprise_uuid)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.json()['error_code'], 'no_budgets_found')
        self.assertIn(unknown_enterprise_uuid, response.json()['detail'])

    def test_policy_of_other_enterprise_is_404_with_error_code(self):
        other = PerLearnerSpendCapLearnerCreditAccessPolicyFactory()
        response = self._get(policy_uuid=str(other.uuid))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.json()['error_code'], 'budget_not_found')

    def test_missing_policy_is_404_with_error_code(self):
        policy_uuid = str(uuid.uuid4())
        response = self._get(policy_uuid=policy_uuid)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.json()['error_code'], 'budget_not_found')
        self.assertIn(policy_uuid, response.json()['detail'])
        self.assertIn(str(self.enterprise_uuid), response.json()['detail'])

    def test_invalid_policy_uuid_is_400(self):
        self.assertEqual(self._get(policy_uuid='not-a-uuid').status_code, status.HTTP_400_BAD_REQUEST)

    @ddt.data(
        SYSTEM_ENTERPRISE_LEARNER_ROLE,  # wrong role for this enterprise
    )
    def test_non_admin_is_403(self, role):
        self.set_jwt_cookie([{'system_wide_role': role, 'context': str(self.enterprise_uuid)}])
        self.assertEqual(self._get().status_code, status.HTTP_403_FORBIDDEN)

    def test_operator_is_200(self):
        self._policy()
        self.set_jwt_cookie([{'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': '*'}])
        self.assertEqual(self._get().status_code, status.HTTP_200_OK)

    def test_admin_of_other_enterprise_is_403(self):
        self.set_jwt_cookie([{'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(uuid.uuid4())}])
        self.assertEqual(self._get().status_code, status.HTTP_403_FORBIDDEN)

    def test_missing_enterprise_customer_uuid_is_403(self):
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)

    @ddt.data(SYSTEM_ENTERPRISE_ADMIN_ROLE, SYSTEM_ENTERPRISE_LEARNER_ROLE)
    @override_settings(FEATURE_TOP_UP_LEARNER_CREDIT=False)
    def test_flag_off_is_404_for_every_role(self, role):
        self.set_jwt_cookie([{'system_wide_role': role, 'context': str(self.enterprise_uuid)}])
        self.assertEqual(self._get().status_code, status.HTTP_404_NOT_FOUND)
        self.mock_retrieve_subsidy.assert_not_called()

    def test_unauthenticated_is_401(self):
        self.client.cookies.clear()
        self.assertEqual(self._get().status_code, status.HTTP_401_UNAUTHORIZED)
