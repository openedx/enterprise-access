"""
Unit tests for the pricing_api module.
"""
from unittest import mock

from django.test import TestCase, override_settings
from edx_django_utils.cache import TieredCache
from stripe.error import InvalidRequestError

from enterprise_access.apps.customer_billing import pricing_api


@override_settings(
    SSP_PRODUCTS={
        'quarterly_license_plan': {
            'stripe_price_id': 'price_ABC',
            'stripe_product_id': 'prod_ABC',
            'quantity_range': (5, 30),
        },
        'yearly_license_plan': {
            'stripe_price_id': 'price_XYZ',
            'stripe_product_id': 'prod_XYZ',
            'quantity_range': (5, 30),
        },
    },
)
class TestStripePricingAPI(TestCase):
    """
    Tests for the Stripe pricing API functions.
    """

    def setUp(self):
        # Clear cache before each test
        TieredCache.dangerous_clear_all_tiers()

    def tearDown(self):
        # Clear cache after each test
        TieredCache.dangerous_clear_all_tiers()

    def _create_mock_stripe_price(
        self,
        price_id='price_123',
        unit_amount=10000,
        currency='usd',
        product_id='prod_123',
        product_name='Test Product',
        recurring=None,
    ):
        """Helper to create mock Stripe price object."""
        mock_product = mock.MagicMock()
        mock_product.id = product_id
        mock_product.name = product_name
        mock_product.description = 'Test product description'
        mock_product.metadata = {'test': 'value'}

        mock_price = mock.MagicMock()
        mock_price.id = price_id
        mock_price.unit_amount = unit_amount
        mock_price.currency = currency
        mock_price.product = mock_product
        mock_price.recurring = recurring

        return mock_price

    @mock.patch('enterprise_access.apps.customer_billing.pricing_api.stripe')
    def test_get_stripe_price_data_basic_format(self, mock_stripe):
        """Test fetching price data in basic format."""
        mock_price = self._create_mock_stripe_price()
        mock_stripe.Price.retrieve.return_value = mock_price

        result = pricing_api.get_stripe_price_data('price_123')

        expected = {
            'usd': 100.0,
            'usd_cents': 10000,
            'currency': 'usd',
            'product': {
                'id': 'prod_123',
                'name': 'Test Product',
                'description': 'Test product description',
                'metadata': {'test': 'value'},
            }
        }

        self.assertEqual(result, expected)
        mock_stripe.Price.retrieve.assert_called_once_with('price_123', expand=['product'])

    @mock.patch('enterprise_access.apps.customer_billing.pricing_api.stripe')
    def test_get_stripe_price_data_with_recurring(self, mock_stripe):
        """Test fetching price data with recurring billing info."""
        mock_recurring = mock.MagicMock()
        mock_recurring.interval = 'year'
        mock_recurring.interval_count = 1

        mock_price = self._create_mock_stripe_price(recurring=mock_recurring)
        mock_stripe.Price.retrieve.return_value = mock_price

        result = pricing_api.get_stripe_price_data('price_123')

        self.assertIn('recurring', result)
        self.assertEqual(result['recurring']['interval'], 'year')
        self.assertEqual(result['recurring']['interval_count'], 1)

    @mock.patch('enterprise_access.apps.customer_billing.pricing_api.stripe')
    def test_get_stripe_price_data_caching(self, mock_stripe):
        """Test that price data is properly cached."""
        mock_price = self._create_mock_stripe_price()
        mock_stripe.Price.retrieve.return_value = mock_price

        # First call should hit Stripe
        result1 = pricing_api.get_stripe_price_data('price_123')

        # Second call should hit cache
        result2 = pricing_api.get_stripe_price_data('price_123')

        self.assertEqual(result1, result2)
        # Stripe should only be called once
        mock_stripe.Price.retrieve.assert_called_once()

    @mock.patch('enterprise_access.apps.customer_billing.pricing_api.stripe.Price')
    def test_get_stripe_price_data_stripe_error(self, mock_stripe_price):
        """Test handling of Stripe API errors."""
        mock_stripe_price.retrieve.side_effect = InvalidRequestError(
            'No such price', 'price_123'
        )

        with self.assertRaises(pricing_api.StripePricingError):
            pricing_api.get_stripe_price_data('price_123')

    @mock.patch('enterprise_access.apps.customer_billing.pricing_api.get_stripe_price_data')
    def test_get_multiple_stripe_prices(self, mock_get_price):
        """Test fetching multiple prices."""
        mock_get_price.side_effect = [
            {'usd': 100.0, 'usd_cents': 10000},
            {'usd': 200.0, 'usd_cents': 20000},
        ]

        result = pricing_api.get_multiple_stripe_prices(['price_1', 'price_2'])

        expected = {
            'price_1': {'usd': 100.0, 'usd_cents': 10000},
            'price_2': {'usd': 200.0, 'usd_cents': 20000},
        }

        self.assertEqual(result, expected)
        self.assertEqual(mock_get_price.call_count, 2)

    @mock.patch('enterprise_access.apps.customer_billing.pricing_api.stripe')
    def test_get_ssp_product_pricing(self, mock_stripe):
        """Test fetching SSP product pricing."""
        mock_price = self._create_mock_stripe_price()
        mock_stripe.Price.retrieve.return_value = mock_price

        result = pricing_api.get_ssp_product_pricing()

        # Should have entries for configured SSP products
        self.assertIn('quarterly_license_plan', result)
        self.assertIn('yearly_license_plan', result)

        # Check that SSP-specific metadata is added
        quarterly_data = result['quarterly_license_plan']
        self.assertEqual(quarterly_data['ssp_product_key'], 'quarterly_license_plan')
        self.assertEqual(quarterly_data['quantity_range'], (5, 30))

    def test_calculate_subtotal_basic_format(self):
        """Test subtotal calculation with basic format."""
        price_data = {
            'usd': 100.0,
            'usd_cents': 10000,
            'currency': 'usd',
            'recurring': {
                'interval': 'year',
                'interval_count': 1,
            }
        }

        result = pricing_api.calculate_subtotal(price_data, 5)

        expected = {
            'subtotal_cents': 50000,
            'subtotal_decimal': 500.0,
            'currency': 'usd',
            'quantity': 5,
            'unit_amount_cents': 10000,
            'unit_amount_decimal': 100.0,
            'billing_period': {
                'interval': 'year',
                'interval_count': 1,
            }
        }

        self.assertEqual(result, expected)

    def test_calculate_subtotal_missing_currency_data(self):
        """Test subtotal calculation with missing currency data."""
        price_data = {
            'eur': 85.0,  # Missing USD data
            'eur_cents': 8500,
            'currency': 'eur',
        }

        result = pricing_api.calculate_subtotal(price_data, 3, currency='usd')
        self.assertIsNone(result)

    def test_format_price_display_basic_format(self):
        """Test price display formatting with basic format."""
        price_data = {
            'usd': 100.0,
            'usd_cents': 10000,
            'currency': 'usd',
            'recurring': {
                'interval': 'year',
                'interval_count': 1,
            }
        }

        result = pricing_api.format_price_display(price_data)
        self.assertEqual(result, '$100.00/year')

    def test_format_price_display_without_currency_symbol(self):
        """Test price display formatting without currency symbol."""
        price_data = {
            'usd': 100.0,
            'usd_cents': 10000,
            'currency': 'usd',
        }

        result = pricing_api.format_price_display(price_data, include_currency_symbol=False)
        self.assertEqual(result, '100.00 USD')

    def test_format_price_display_multi_interval(self):
        """Test price display with multi-interval recurring."""
        price_data = {
            'usd': 100.0,
            'usd_cents': 10000,
            'currency': 'usd',
            'recurring': {
                'interval': 'month',
                'interval_count': 3,
            }
        }

        result = pricing_api.format_price_display(price_data)
        self.assertEqual(result, '$100.00/every 3 months')

    def test_format_price_display_missing_currency(self):
        """Test price display with missing currency data."""
        price_data = {
            'eur': 85.0,  # Missing USD data
            'currency': 'eur',
        }

        result = pricing_api.format_price_display(price_data, currency='usd')
        self.assertEqual(result, 'Price unavailable')

    def test_serialize_basic_format_edge_cases(self):
        """Test serialization edge cases for basic format."""
        # Test with zero amount
        mock_price = self._create_mock_stripe_price(unit_amount=0)
        result = pricing_api._serialize_basic_format(mock_price)  # pylint: disable=protected-access

        self.assertEqual(result['usd'], 0.0)
        self.assertEqual(result['usd_cents'], 0)

    def test_serialize_basic_format_no_product(self):
        """Test serialization when product is not expanded."""
        mock_price = self._create_mock_stripe_price()
        mock_price.product = None  # No expanded product data

        result = pricing_api._serialize_basic_format(mock_price)  # pylint: disable=protected-access

        self.assertNotIn('product', result)
        self.assertEqual(result['usd'], 100.0)
        self.assertEqual(result['usd_cents'], 10000)

    def test_validate_stripe_price_schema_missing_field(self):
        """Test schema validation with missing required field."""
        mock_price = self._create_mock_stripe_price()
        # Remove required field
        del mock_price.currency

        with mock.patch('enterprise_access.apps.customer_billing.pricing_api.stripe.Price') as mock_stripe_price:
            mock_stripe_price.retrieve.return_value = mock_price

            with self.assertRaises(pricing_api.StripePricingError) as cm:
                pricing_api.get_stripe_price_data('price_123')

            self.assertIn('Missing required field', str(cm.exception))

    def test_validate_stripe_price_schema_invalid_type(self):
        """Test schema validation with invalid field type."""
        mock_price = self._create_mock_stripe_price()
        # Set invalid type for unit_amount
        mock_price.unit_amount = "invalid"

        with mock.patch('enterprise_access.apps.customer_billing.pricing_api.stripe.Price') as mock_stripe_price:
            mock_stripe_price.retrieve.return_value = mock_price

            with self.assertRaises(pricing_api.StripePricingError) as cm:
                pricing_api.get_stripe_price_data('price_123')

            self.assertIn('Invalid unit_amount type', str(cm.exception))

    def test_validate_stripe_price_schema_invalid_recurring(self):
        """Test schema validation with invalid recurring data."""
        mock_recurring = mock.MagicMock()
        mock_recurring.interval = None  # Invalid - should be a string
        mock_recurring.interval_count = 1

        mock_price = self._create_mock_stripe_price(recurring=mock_recurring)

        with mock.patch('enterprise_access.apps.customer_billing.pricing_api.stripe.Price') as mock_stripe_price:
            mock_stripe_price.retrieve.return_value = mock_price

            with self.assertRaises(pricing_api.StripePricingError) as cm:
                pricing_api.get_stripe_price_data('price_123')

            self.assertIn('Recurring price missing interval', str(cm.exception))
