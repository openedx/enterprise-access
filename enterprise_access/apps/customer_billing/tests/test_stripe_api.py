"""
Unit tests for interacting with stripe via ``stripe_api.api``.
"""
from unittest import mock

import stripe
from django.test import TestCase
from edx_django_utils.cache import TieredCache

from enterprise_access.apps.customer_billing.stripe_api import (
    get_stripe_checkout_session,
    get_stripe_invoice,
    get_stripe_payment_intent,
    get_stripe_payment_method,
    stripe_cache
)


class StripeApiFunctionsTests(TestCase):
    """Tests for Stripe API functions with caching."""

    def setUp(self):
        """Set up test case."""
        # Clear cache before each test
        TieredCache.dangerous_clear_all_tiers()

        # Sample test data
        self.session_id = "cs_test_123456789"
        self.payment_intent_id = "pi_test_123456789"
        self.invoice_id = "in_test_123456789"
        self.payment_method_id = "pm_test_123456789"

        # Sample response objects
        self.session_response = {"id": self.session_id, "object": "checkout.session"}
        self.payment_intent_response = {"id": self.payment_intent_id, "object": "payment_intent"}
        self.invoice_response = {"id": self.invoice_id, "object": "invoice"}
        self.payment_method_response = {"id": self.payment_method_id, "object": "payment_method"}


class TestStripeCheckoutSession(StripeApiFunctionsTests):
    """Tests for get_stripe_checkout_session function."""

    @mock.patch('enterprise_access.apps.customer_billing.stripe_api.stripe.checkout.Session.retrieve')
    def test_get_stripe_checkout_session_success(self, mock_retrieve):
        """Test successful retrieval of checkout session."""
        mock_retrieve.return_value = self.session_response

        # First call should hit the API
        result = get_stripe_checkout_session(self.session_id)

        mock_retrieve.assert_called_once_with(self.session_id)
        self.assertEqual(result, self.session_response)

    @mock.patch('enterprise_access.apps.customer_billing.stripe_api.stripe.checkout.Session.retrieve')
    @mock.patch('edx_django_utils.cache.TieredCache.get_cached_response')
    @mock.patch('edx_django_utils.cache.TieredCache.set_all_tiers')
    def test_get_stripe_checkout_session_cache_hit(self, mock_set, mock_get, mock_retrieve):
        """Test cache hit for checkout session."""
        # Setup cache hit
        mock_cached_response = mock.MagicMock()
        mock_cached_response.is_found = True
        mock_cached_response.value = self.session_response
        mock_get.return_value = mock_cached_response

        # Call function
        result = get_stripe_checkout_session(self.session_id)

        # Verify behavior
        mock_get.assert_called_once()
        mock_retrieve.assert_not_called()
        mock_set.assert_not_called()
        self.assertEqual(result, self.session_response)

    @mock.patch('enterprise_access.apps.customer_billing.stripe_api.stripe.checkout.Session.retrieve')
    @mock.patch('edx_django_utils.cache.TieredCache.get_cached_response')
    @mock.patch('edx_django_utils.cache.TieredCache.set_all_tiers')
    def test_get_stripe_checkout_session_cache_miss(self, mock_set, mock_get, mock_retrieve):
        """Test cache miss for checkout session."""
        # Setup cache miss
        mock_cached_response = mock.MagicMock()
        mock_cached_response.is_found = False
        mock_get.return_value = mock_cached_response

        # Setup API response
        mock_retrieve.return_value = self.session_response

        # Call function
        result = get_stripe_checkout_session(self.session_id)

        # Verify behavior
        mock_get.assert_called_once()
        mock_retrieve.assert_called_once_with(self.session_id)
        mock_set.assert_called_once()
        self.assertEqual(result, self.session_response)

    @mock.patch('enterprise_access.apps.customer_billing.stripe_api.stripe.checkout.Session.retrieve')
    def test_get_stripe_checkout_session_api_error(self, mock_retrieve):
        """Test API error handling for checkout session."""
        # Setup API error
        mock_retrieve.side_effect = stripe.error.StripeError("API Error")

        # Call function and verify exception is raised
        with self.assertRaises(stripe.error.StripeError):
            get_stripe_checkout_session(self.session_id)


class TestStripePaymentIntent(StripeApiFunctionsTests):
    """Tests for get_stripe_payment_intent function."""

    @mock.patch('enterprise_access.apps.customer_billing.stripe_api.stripe.PaymentIntent.retrieve')
    def test_get_stripe_payment_intent_success(self, mock_retrieve):
        """Test successful retrieval of payment intent."""
        mock_retrieve.return_value = self.payment_intent_response

        # First call should hit the API
        result = get_stripe_payment_intent(self.payment_intent_id)

        mock_retrieve.assert_called_once_with(self.payment_intent_id)
        self.assertEqual(result, self.payment_intent_response)

    @mock.patch('enterprise_access.apps.customer_billing.stripe_api.stripe.PaymentIntent.retrieve')
    @mock.patch('edx_django_utils.cache.TieredCache.get_cached_response')
    @mock.patch('edx_django_utils.cache.TieredCache.set_all_tiers')
    def test_get_stripe_payment_intent_cache_hit(self, mock_set, mock_get, mock_retrieve):
        """Test cache hit for payment intent."""
        # Setup cache hit
        mock_cached_response = mock.MagicMock()
        mock_cached_response.is_found = True
        mock_cached_response.value = self.payment_intent_response
        mock_get.return_value = mock_cached_response

        # Call function
        result = get_stripe_payment_intent(self.payment_intent_id)

        # Verify behavior
        mock_get.assert_called_once()
        mock_retrieve.assert_not_called()
        mock_set.assert_not_called()
        self.assertEqual(result, self.payment_intent_response)

    @mock.patch('enterprise_access.apps.customer_billing.stripe_api.stripe.PaymentIntent.retrieve')
    @mock.patch('edx_django_utils.cache.TieredCache.get_cached_response')
    @mock.patch('edx_django_utils.cache.TieredCache.set_all_tiers')
    def test_get_stripe_payment_intent_cache_miss(self, mock_set, mock_get, mock_retrieve):
        """Test cache miss for payment intent."""
        # Setup cache miss
        mock_cached_response = mock.MagicMock()
        mock_cached_response.is_found = False
        mock_get.return_value = mock_cached_response

        # Setup API response
        mock_retrieve.return_value = self.payment_intent_response

        # Call function
        result = get_stripe_payment_intent(self.payment_intent_id)

        # Verify behavior
        mock_get.assert_called_once()
        mock_retrieve.assert_called_once_with(self.payment_intent_id)
        mock_set.assert_called_once()
        self.assertEqual(result, self.payment_intent_response)

    @mock.patch('enterprise_access.apps.customer_billing.stripe_api.stripe.PaymentIntent.retrieve')
    def test_get_stripe_payment_intent_api_error(self, mock_retrieve):
        """Test API error handling for payment intent."""
        # Setup API error
        mock_retrieve.side_effect = stripe.error.StripeError("API Error")

        # Call function and verify exception is raised
        with self.assertRaises(stripe.error.StripeError):
            get_stripe_payment_intent(self.payment_intent_id)


class TestStripeInvoice(StripeApiFunctionsTests):
    """Tests for get_stripe_invoice function."""

    @mock.patch('enterprise_access.apps.customer_billing.stripe_api.stripe.Invoice.retrieve')
    def test_get_stripe_invoice_success(self, mock_retrieve):
        """Test successful retrieval of invoice."""
        mock_retrieve.return_value = self.invoice_response

        # First call should hit the API
        result = get_stripe_invoice(self.invoice_id)

        mock_retrieve.assert_called_once_with(self.invoice_id)
        self.assertEqual(result, self.invoice_response)

    @mock.patch('enterprise_access.apps.customer_billing.stripe_api.stripe.Invoice.retrieve')
    @mock.patch('edx_django_utils.cache.TieredCache.get_cached_response')
    @mock.patch('edx_django_utils.cache.TieredCache.set_all_tiers')
    def test_get_stripe_invoice_cache_hit(self, mock_set, mock_get, mock_retrieve):
        """Test cache hit for invoice."""
        # Setup cache hit
        mock_cached_response = mock.MagicMock()
        mock_cached_response.is_found = True
        mock_cached_response.value = self.invoice_response
        mock_get.return_value = mock_cached_response

        # Call function
        result = get_stripe_invoice(self.invoice_id)

        # Verify behavior
        mock_get.assert_called_once()
        mock_retrieve.assert_not_called()
        mock_set.assert_not_called()
        self.assertEqual(result, self.invoice_response)

    @mock.patch('enterprise_access.apps.customer_billing.stripe_api.stripe.Invoice.retrieve')
    @mock.patch('edx_django_utils.cache.TieredCache.get_cached_response')
    @mock.patch('edx_django_utils.cache.TieredCache.set_all_tiers')
    def test_get_stripe_invoice_cache_miss(self, mock_set, mock_get, mock_retrieve):
        """Test cache miss for invoice."""
        # Setup cache miss
        mock_cached_response = mock.MagicMock()
        mock_cached_response.is_found = False
        mock_get.return_value = mock_cached_response

        # Setup API response
        mock_retrieve.return_value = self.invoice_response

        # Call function
        result = get_stripe_invoice(self.invoice_id)

        # Verify behavior
        mock_get.assert_called_once()
        mock_retrieve.assert_called_once_with(self.invoice_id)
        mock_set.assert_called_once()
        self.assertEqual(result, self.invoice_response)

    @mock.patch('enterprise_access.apps.customer_billing.stripe_api.stripe.Invoice.retrieve')
    def test_get_stripe_invoice_api_error(self, mock_retrieve):
        """Test API error handling for invoice."""
        # Setup API error
        mock_retrieve.side_effect = stripe.error.StripeError("API Error")

        # Call function and verify exception is raised
        with self.assertRaises(stripe.error.StripeError):
            get_stripe_invoice(self.invoice_id)


class TestStripePaymentMethod(StripeApiFunctionsTests):
    """Tests for get_stripe_payment_method function."""

    @mock.patch('enterprise_access.apps.customer_billing.stripe_api.stripe.PaymentMethod.retrieve')
    def test_get_stripe_payment_method_success(self, mock_retrieve):
        """Test successful retrieval of payment method."""
        mock_retrieve.return_value = self.payment_method_response

        # First call should hit the API
        result = get_stripe_payment_method(self.payment_method_id)

        mock_retrieve.assert_called_once_with(self.payment_method_id)
        self.assertEqual(result, self.payment_method_response)

    @mock.patch('enterprise_access.apps.customer_billing.stripe_api.stripe.PaymentMethod.retrieve')
    @mock.patch('edx_django_utils.cache.TieredCache.get_cached_response')
    @mock.patch('edx_django_utils.cache.TieredCache.set_all_tiers')
    def test_get_stripe_payment_method_cache_hit(self, mock_set, mock_get, mock_retrieve):
        """Test cache hit for payment method."""
        # Setup cache hit
        mock_cached_response = mock.MagicMock()
        mock_cached_response.is_found = True
        mock_cached_response.value = self.payment_method_response
        mock_get.return_value = mock_cached_response

        # Call function
        result = get_stripe_payment_method(self.payment_method_id)

        # Verify behavior
        mock_get.assert_called_once()
        mock_retrieve.assert_not_called()
        mock_set.assert_not_called()
        self.assertEqual(result, self.payment_method_response)

    @mock.patch('enterprise_access.apps.customer_billing.stripe_api.stripe.PaymentMethod.retrieve')
    @mock.patch('edx_django_utils.cache.TieredCache.get_cached_response')
    @mock.patch('edx_django_utils.cache.TieredCache.set_all_tiers')
    def test_get_stripe_payment_method_cache_miss(self, mock_set, mock_get, mock_retrieve):
        """Test cache miss for payment method."""
        # Setup cache miss
        mock_cached_response = mock.MagicMock()
        mock_cached_response.is_found = False
        mock_get.return_value = mock_cached_response

        # Setup API response
        mock_retrieve.return_value = self.payment_method_response

        # Call function
        result = get_stripe_payment_method(self.payment_method_id)

        # Verify behavior
        mock_get.assert_called_once()
        mock_retrieve.assert_called_once_with(self.payment_method_id)
        mock_set.assert_called_once()
        self.assertEqual(result, self.payment_method_response)

    @mock.patch('enterprise_access.apps.customer_billing.stripe_api.stripe.PaymentMethod.retrieve')
    def test_get_stripe_payment_method_api_error(self, mock_retrieve):
        """Test API error handling for payment method."""
        # Setup API error
        mock_retrieve.side_effect = stripe.error.StripeError("API Error")

        # Call function and verify exception is raised
        with self.assertRaises(stripe.error.StripeError):
            get_stripe_payment_method(self.payment_method_id)


class TestStripeCacheDecorator(TestCase):
    """Tests for the stripe_cache decorator itself."""

    def setUp(self):
        """Set up test case."""
        TieredCache.dangerous_clear_all_tiers()

    @mock.patch('edx_django_utils.cache.TieredCache.get_cached_response', autospec=True)
    @mock.patch('edx_django_utils.cache.TieredCache.set_all_tiers', autospec=True)
    def test_stripe_cache_decorator_different_keys(self, mock_set, mock_get):
        """Test that different resource IDs create different cache keys."""
        # Setup cache miss for all calls
        mock_cached_response = mock.MagicMock()
        mock_cached_response.is_found = False
        mock_get.return_value = mock_cached_response

        # Mock the stripe API call
        with mock.patch('stripe.checkout.Session.retrieve') as mock_retrieve:
            mock_retrieve.return_value = {"id": "test1"}

            # Call with first ID
            get_stripe_checkout_session("test1")

            # Call with second ID
            get_stripe_checkout_session("test2")

        # Check that we got two different cache keys
        self.assertEqual(mock_get.call_count, 2)
        self.assertNotEqual(
            mock_get.call_args_list[0][0][0],  # First call's cache key
            mock_get.call_args_list[1][0][0],  # Second call's cache key
        )
        mock_set.assert_has_calls([
            mock.call('stripe_get_stripe_checkout_session_test1', {'id': 'test1'}, django_cache_timeout=60),
            mock.call('stripe_get_stripe_checkout_session_test2', {'id': 'test1'}, django_cache_timeout=60),
        ])

    @mock.patch('edx_django_utils.cache.TieredCache.get_cached_response')
    @mock.patch('edx_django_utils.cache.TieredCache.set_all_tiers')
    def test_stripe_cache_decorator_custom_timeout(self, mock_set, mock_get):
        """Test that the timeout parameter is passed correctly."""
        # Setup cache miss
        mock_cached_response = mock.MagicMock()
        mock_cached_response.is_found = False
        mock_get.return_value = mock_cached_response

        # Define a test function with custom timeout
        @stripe_cache(timeout=120)
        def test_function(resource_id):
            return {"id": resource_id}

        # Call the function
        test_function("test_id")

        # Check that set_all_tiers was called with the correct timeout
        mock_set.assert_called_once()

        # Third argument to set_all_tiers should be the timeout
        call_kwargs = mock_set.call_args[1]
        self.assertEqual(call_kwargs, {'django_cache_timeout': 120})
