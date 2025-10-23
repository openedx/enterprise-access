"""
Tests for custom API authentication classes.
"""
import json
from unittest import mock

import stripe
from django.conf import settings
from django.test import RequestFactory, TestCase, override_settings
from rest_framework.exceptions import AuthenticationFailed

from enterprise_access.apps.api.authentication import StripeWebhookAuthentication


class StripeWebhookAuthenticationTests(TestCase):
    """
    Tests for StripeWebhookAuthentication class.
    """

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.factory = RequestFactory()
        self.auth = StripeWebhookAuthentication()
        self.webhook_secret = 'whsec_test_secret'
        self.valid_payload = json.dumps({
            'id': 'evt_test_webhook',
            'object': 'event',
            'type': 'checkout.session.completed',
        })

    def _create_request_with_signature(self, payload, signature):
        """Helper to create a request with a Stripe signature header."""
        request = self.factory.post(
            '/api/v1/customer-billing/stripe-webhook/',
            data=payload,
            content_type='application/json',
        )
        request.META['HTTP_STRIPE_SIGNATURE'] = signature
        return request

    @override_settings(STRIPE_WEBHOOK_ENDPOINT_SECRET='whsec_test_secret')
    @mock.patch('stripe.Webhook.construct_event')
    def test_authenticate_success(self, mock_construct_event):
        """
        Test successful authentication with valid signature.
        """
        mock_construct_event.return_value = {'id': 'evt_test'}

        request = self._create_request_with_signature(
            self.valid_payload,
            't=1234567890,v1=valid_signature'
        )

        result = self.auth.authenticate(request)

        # Webhook authentication returns None (no user)
        self.assertIsNone(result)

        # Verify construct_event was called with correct parameters
        mock_construct_event.assert_called_once_with(
            request.body,
            't=1234567890,v1=valid_signature',
            'whsec_test_secret'
        )

    def test_authenticate_missing_signature_header(self):
        """
        Test authentication fails when signature header is missing.
        """
        request = self.factory.post(
            '/api/v1/customer-billing/stripe-webhook/',
            data=self.valid_payload,
            content_type='application/json',
        )

        with self.assertRaises(AuthenticationFailed) as context:
            self.auth.authenticate(request)

        self.assertIn('Missing Stripe signature header', str(context.exception))

    @override_settings(STRIPE_WEBHOOK_ENDPOINT_SECRET=None)
    def test_authenticate_missing_webhook_secret_setting(self):
        """
        Test authentication fails when webhook secret is not configured.
        """
        request = self._create_request_with_signature(
            self.valid_payload,
            't=1234567890,v1=signature'
        )

        with self.assertRaises(AuthenticationFailed) as context:
            self.auth.authenticate(request)

        self.assertIn('Webhook endpoint secret not configured', str(context.exception))

    @override_settings(STRIPE_WEBHOOK_ENDPOINT_SECRET='whsec_test_secret')
    @mock.patch('stripe.Webhook.construct_event')
    def test_authenticate_invalid_payload(self, mock_construct_event):
        """
        Test authentication fails with invalid payload.
        """
        mock_construct_event.side_effect = ValueError('Invalid payload')

        request = self._create_request_with_signature(
            'invalid json payload',
            't=1234567890,v1=signature'
        )

        with self.assertRaises(AuthenticationFailed) as context:
            self.auth.authenticate(request)

        self.assertIn('Invalid payload', str(context.exception))

    @override_settings(STRIPE_WEBHOOK_ENDPOINT_SECRET='whsec_test_secret')
    @mock.patch('stripe.Webhook.construct_event')
    def test_authenticate_invalid_signature(self, mock_construct_event):
        """
        Test authentication fails with invalid signature.
        """
        mock_construct_event.side_effect = stripe.SignatureVerificationError(
            'Invalid signature',
            'sig_header'
        )

        request = self._create_request_with_signature(
            self.valid_payload,
            't=1234567890,v1=invalid_signature'
        )

        with self.assertRaises(AuthenticationFailed) as context:
            self.auth.authenticate(request)

        self.assertIn('Invalid signature', str(context.exception))

    @override_settings(STRIPE_WEBHOOK_ENDPOINT_SECRET='whsec_test_secret')
    @mock.patch('stripe.Webhook.construct_event')
    def test_authenticate_unexpected_error(self, mock_construct_event):
        """
        Test authentication fails gracefully with unexpected errors.
        """
        mock_construct_event.side_effect = Exception('Unexpected error')

        request = self._create_request_with_signature(
            self.valid_payload,
            't=1234567890,v1=signature'
        )

        with self.assertRaises(AuthenticationFailed) as context:
            self.auth.authenticate(request)

        self.assertIn('Authentication failed', str(context.exception))
