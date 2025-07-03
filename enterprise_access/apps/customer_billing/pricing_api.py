"""
Python API for fetching and serializing Stripe pricing data.

This module provides a centralized way to fetch pricing information from Stripe
and serialize it in a consistent format for use across enterprise-access applications.

Basic Format Structure:
    {
        "usd": 100.00,           # Price in dollars (decimal)
        "usd_cents": 10000,      # Price in cents (integer)
        "currency": "usd",       # Currency code
        "recurring": {           # Present for subscription prices
            "interval": "year",
            "interval_count": 1
        },
        "product": {             # Product metadata when available
            "id": "prod_123",
            "name": "Product Name",
            "description": "...",
            "metadata": {}
        }
    }
"""
import logging
from typing import Dict, List, Optional, TypedDict

import stripe
from django.conf import settings
from edx_django_utils.cache import TieredCache

from enterprise_access.cache_utils import versioned_cache_key

logger = logging.getLogger(__name__)

# Initialize Stripe with API key from settings
stripe.api_key = settings.STRIPE_API_KEY


class StripePricingError(Exception):
    """Exception raised when there's an error fetching Stripe pricing data."""


class StripeRecurringData(TypedDict, total=False):
    """TypedDict for Stripe recurring billing data."""
    aggregate_usage: Optional[str]
    interval: str
    interval_count: int
    trial_period_days: Optional[int]
    usage_type: str


class StripePriceData(TypedDict, total=False):
    """
    TypedDict representing a Stripe Price object schema. This represents the shape
    of the price data we get back from the Stripe API.
    """
    id: str
    object: str
    active: bool
    billing_scheme: str
    created: int
    currency: str
    custom_unit_amount: Optional[Dict]
    livemode: bool
    lookup_key: Optional[str]
    metadata: Dict[str, str]
    nickname: Optional[str]
    product: str  # Product ID when not expanded
    recurring: Optional[StripeRecurringData]
    tax_behavior: str
    tiers_mode: Optional[str]
    transform_quantity: Optional[Dict]
    type: str
    unit_amount: int
    unit_amount_decimal: str


class SerializedRecurringData(TypedDict, total=False):
    """TypedDict for serialized recurring billing data."""
    interval: str
    interval_count: int


class SerializedProductData(TypedDict, total=False):
    """TypedDict for serialized product data."""
    id: str
    name: str
    description: str
    metadata: Dict[str, str]


class SerializedPriceData(TypedDict, total=False):
    """
    TypedDict for our serialized price data in basic format.

    Example:
        {
            "usd": 100.00,
            "usd_cents": 10000,
            "currency": "usd",
            "recurring": {
                "interval": "year",
                "interval_count": 1
            },
            "product": {
                "id": "prod_123",
                "name": "Product Name",
                "description": "...",
                "metadata": {}
            }
        }
    """
    currency: str
    recurring: SerializedRecurringData
    product: SerializedProductData
    # Dynamic currency fields like "usd" and "usd_cents" can't be defined in TypedDict
    # since the key names depend on the currency. We'll document this limitation.


def get_stripe_price_data(
    price_id: str,
    timeout: int = settings.CONTENT_METADATA_CACHE_TIMEOUT,
) -> Optional[Dict]:
    """
    Fetch and cache Stripe price data for a given Price ID.

    Args:
        price_id: Stripe Price ID to fetch
        timeout: Cache timeout in seconds

    Returns:
        Dict containing serialized price data in basic format, or None if not found

    Raises:
        StripePricingError: If there's an error fetching from Stripe
    """
    cache_key = versioned_cache_key(
        'stripe_price_data',
        price_id,
    )

    cached_response = TieredCache.get_cached_response(cache_key)
    if cached_response.is_found:
        logger.info(f'Cache hit for Stripe price {price_id}')
        return cached_response.value

    try:
        # Fetch price and associated product from Stripe
        stripe_price = stripe.Price.retrieve(price_id, expand=['product'])

        # Validate the response against our expected schema
        _validate_stripe_price_schema(stripe_price)

        # Serialize the price data in basic format
        serialized_data = _serialize_basic_format(stripe_price)

        if serialized_data:
            TieredCache.set_all_tiers(
                cache_key,
                serialized_data,
                django_cache_timeout=timeout,
            )
            logger.info(f'Cached Stripe price data for {price_id}')

        return serialized_data

    except stripe.error.StripeError as exc:
        logger.error(f'Stripe API error fetching price {price_id}: {exc}')
        raise StripePricingError(f'Failed to fetch price {price_id}: {exc}') from exc
    except Exception as exc:
        logger.error(f'Unexpected error fetching price {price_id}: {exc}')
        raise StripePricingError(f'Unexpected error fetching price {price_id}: {exc}') from exc


def _validate_stripe_price_schema(stripe_price: stripe.Price) -> None:
    """
    Validate that the Stripe price response matches our expected schema.

    Args:
        stripe_price: Stripe Price object to validate

    Raises:
        StripePricingError: If the schema doesn't match expectations
    """
    required_fields = ['id', 'currency', 'unit_amount', 'type']

    for field in required_fields:
        if not hasattr(stripe_price, field) or getattr(stripe_price, field) is None:
            raise StripePricingError(f'Missing required field in Stripe price response: {field}')

    # Validate currency is a string
    if not isinstance(stripe_price.currency, str):
        raise StripePricingError(f'Invalid currency type: expected str, got {type(stripe_price.currency)}')

    # Validate unit_amount is an integer
    if not isinstance(stripe_price.unit_amount, int):
        raise StripePricingError(f'Invalid unit_amount type: expected int, got {type(stripe_price.unit_amount)}')

    # If recurring data exists, validate its structure
    if stripe_price.recurring:
        if not hasattr(stripe_price.recurring, 'interval') or not stripe_price.recurring.interval:
            raise StripePricingError('Recurring price missing interval')
        if not hasattr(stripe_price.recurring, 'interval_count') or stripe_price.recurring.interval_count is None:
            raise StripePricingError('Recurring price missing interval_count')

    logger.debug(f'Stripe price {stripe_price.id} schema validation passed')


def get_multiple_stripe_prices(
    price_ids: List[str],
    timeout: int = settings.CONTENT_METADATA_CACHE_TIMEOUT,
) -> Dict[str, Dict]:
    """
    Fetch multiple Stripe prices efficiently with caching.

    Args:
        price_ids: List of Stripe Price IDs to fetch
        timeout: Cache timeout in seconds

    Returns:
        Dict mapping price_id to serialized price data
    """
    results = {}

    # Check cache for each price ID individually
    for price_id in price_ids:
        cache_key = versioned_cache_key(
            'stripe_price_data',
            price_id,
        )

        cached_response = TieredCache.get_cached_response(cache_key)
        if cached_response.is_found:
            logger.info(f'Cache hit for Stripe price {price_id}')
            results[price_id] = cached_response.value
        else:
            # Fetch uncached price from Stripe
            try:
                price_data = get_stripe_price_data(price_id, timeout)
                if price_data:
                    results[price_id] = price_data
            except StripePricingError:
                logger.warning(f'Failed to fetch price data for {price_id}')
                continue

    return results


def get_ssp_product_pricing() -> Dict[str, Dict]:
    """
    Get pricing data for all configured SSP products.

    Returns:
        Dict mapping SSP product keys to price data
    """
    ssp_pricing = {}
    for product_key, product_config in settings.SSP_PRODUCTS.items():
        price_id = product_config.get('stripe_price_id')
        if price_id:
            try:
                price_data = get_stripe_price_data(price_id)
                if price_data:
                    # Add SSP-specific metadata
                    price_data['ssp_product_key'] = product_key
                    price_data['quantity_range'] = product_config.get('quantity_range')
                    ssp_pricing[product_key] = price_data
            except Exception as exc:
                logger.error(f'Failed to fetch pricing for SSP product {product_key}: {exc}')
                raise

    return ssp_pricing


def calculate_subtotal(
    price_data: Dict,
    quantity: int,
    currency: str = 'usd'
) -> Optional[Dict]:
    """
    Calculate subtotal for a given price and quantity.

    Args:
        price_data: Serialized price data from pricing API
        quantity: Number of units
        currency: Currency code (default: 'usd')

    Returns:
        Dict with subtotal information or None if calculation fails
    """
    try:
        # Get recurring info if available
        if 'recurring' in price_data:
            interval = price_data['recurring']['interval']
            interval_count = price_data['recurring']['interval_count']
        else:
            interval = None
            interval_count = None

        # Get unit pricing from basic format
        currency_key = f'{currency}_cents'
        if currency not in price_data or currency_key not in price_data:
            logger.error(f'Cannot calculate subtotal: missing {currency} pricing data')
            return None

        unit_amount_cents = price_data[currency_key]
        unit_amount_decimal = price_data[currency]

        subtotal_cents = unit_amount_cents * quantity
        subtotal_decimal = unit_amount_decimal * quantity

        result = {
            'subtotal_cents': subtotal_cents,
            'subtotal_decimal': round(subtotal_decimal, 2),
            'currency': currency,
            'quantity': quantity,
            'unit_amount_cents': unit_amount_cents,
            'unit_amount_decimal': unit_amount_decimal,
        }

        if interval:
            result['billing_period'] = {
                'interval': interval,
                'interval_count': interval_count,
            }

        return result

    except (KeyError, TypeError, ValueError) as e:
        logger.error(f'Error calculating subtotal: {e}')
        return None


def format_price_display(
    price_data: Dict,
    currency: str = 'usd',
    include_currency_symbol: bool = True
) -> str:
    """
    Format price data for display to users.

    Args:
        price_data: Serialized price data
        currency: Currency code
        include_currency_symbol: Whether to include currency symbol

    Returns:
        Formatted price string (e.g., "$100.00/year")
    """
    try:
        # Get unit amount from basic format
        if currency not in price_data:
            return 'Price unavailable'

        amount = price_data[currency]

        # Format currency
        if include_currency_symbol and currency.lower() == 'usd':
            formatted_amount = f'${amount:.2f}'
        else:
            formatted_amount = f'{amount:.2f} {currency.upper()}'

        # Add billing period if recurring
        if 'recurring' in price_data:
            interval = price_data['recurring']['interval']
            interval_count = price_data['recurring']['interval_count']

            if interval_count == 1:
                period = f'/{interval}'
            else:
                period = f'/every {interval_count} {interval}s'

            formatted_amount += period

        return formatted_amount

    except (KeyError, TypeError, ValueError):
        return 'Price unavailable'


def _serialize_basic_format(stripe_price: stripe.Price) -> SerializedPriceData:
    """
    Serialize Stripe price in basic format matching Learner Credit APIs.

    Returns:
        SerializedPriceData with format: {"usd": 100.00, "usd_cents": 10000, ...}

    Note:
        The currency-specific fields (e.g., "usd", "usd_cents") are added dynamically
        and can't be typed in the TypedDict since field names vary by currency.
    """
    currency = stripe_price.currency.lower()
    unit_amount = stripe_price.unit_amount or 0
    unit_amount_decimal = float(unit_amount) / 100

    # Start with the typed base structure
    base_data: SerializedPriceData = {
        'currency': currency,
    }

    # Add dynamic currency fields (these can't be typed in TypedDict)
    base_data[f'{currency}'] = unit_amount_decimal  # type: ignore
    base_data[f'{currency}_cents'] = unit_amount  # type: ignore

    # Add recurring information if available
    if stripe_price.recurring:
        base_data['recurring'] = {
            'interval': stripe_price.recurring.interval,
            'interval_count': stripe_price.recurring.interval_count,
        }

    # Add product information if available
    if stripe_price.product:
        product = stripe_price.product
        base_data['product'] = {
            'id': product.id,
            'name': product.name,
            'description': product.description,
            'metadata': product.metadata,
        }

    return base_data
