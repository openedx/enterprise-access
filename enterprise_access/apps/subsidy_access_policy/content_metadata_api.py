"""
Python API for interacting with content metadata
for use in the domain of SubsidyAccessPolicies.
"""
import logging
from decimal import Decimal

import requests
from django.conf import settings
from django.utils.dateparse import parse_datetime
from edx_django_utils.cache import TieredCache
from requests.exceptions import HTTPError

from enterprise_access.cache_utils import versioned_cache_key

from ..api_client.enterprise_catalog_client import EnterpriseCatalogApiClient
from .exceptions import ContentPriceNullException
from .utils import get_versioned_subsidy_client

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TIMEOUT = getattr(settings, 'CONTENT_METADATA_CACHE_TIMEOUT', 60 * 5)


def get_and_cache_content_metadata(enterprise_customer_uuid, content_key, timeout=None):
    """
    Returns the metadata for some customer and content key,
    as told by the enterprise-subsidy service.

    Returns: A dictionary containing content metadata for the given key
    Raises: An HTTPError if there's a problem getting the content metadata
      via the subsidy service.
    """
    cache_key = versioned_cache_key('get_subsidy_content_metadata', enterprise_customer_uuid, content_key)
    cached_response = TieredCache.get_cached_response(cache_key)
    if cached_response.is_found:
        logger.info(f'cache hit for customer {enterprise_customer_uuid} and content {content_key}')
        return cached_response.value

    client = get_versioned_subsidy_client()
    try:
        metadata = client.get_subsidy_content_data(
            enterprise_customer_uuid,
            content_key,
        )
    except HTTPError as exc:
        raise exc

    logger.info(
        'Fetched content metadata for customer %s and content_key %s',
        enterprise_customer_uuid,
        content_key,
    )
    TieredCache.set_all_tiers(cache_key, metadata, timeout or DEFAULT_CACHE_TIMEOUT)
    return metadata


def get_and_cache_catalog_contains_content(enterprise_catalog_uuid, content_key, timeout=None):
    """
    Returns a boolean indicating if the given content is in the given catalog.
    This value is cached in a ``TieredCache`` (meaning in both the RequestCache,
    _and_ the django cache for the configured expiration period).
    """
    cache_key = versioned_cache_key('contains_content_key', enterprise_catalog_uuid, content_key)
    cached_response = TieredCache.get_cached_response(cache_key)
    if cached_response.is_found:
        logger.info(f'cache hit for catalog {enterprise_catalog_uuid} and content {content_key}')
        return cached_response.value

    try:
        result = EnterpriseCatalogApiClient().contains_content_items(
            enterprise_catalog_uuid,
            [content_key],
        )
    except HTTPError as exc:
        raise exc

    logger.info(
        'Fetched catalog inclusion for catalog %s and content_key %s. Result = %s',
        enterprise_catalog_uuid,
        content_key,
        result,
    )
    TieredCache.set_all_tiers(cache_key, result, timeout or DEFAULT_CACHE_TIMEOUT)
    return result


def get_list_price_for_content(enterprise_customer_uuid, content_key, content_metadata=None):
    """
    Given a customer and content identifier, fetch content metadata and return a list price
    dictionary. If the caller already has a dictionary of ``content_metadata`` in scope, this
    function computes its return value from that.
    Returns:
        A dictionary of the form
        ```
        {
            "usd": 149.50, # the list price in US Dollars as a float
            "usd_cents": 14950 # the list price in USD Cents as an int
        }

    Raises:
        A ``ContentPriceNullException`` if we encountered an HTTPError fetch content metadata.
    """
    if not content_metadata:
        try:
            content_metadata = get_and_cache_content_metadata(enterprise_customer_uuid, content_key)
        except requests.exceptions.HTTPError as exc:
            logger.warning(
                f'{exc} when checking content metadata for {enterprise_customer_uuid} and {content_key}'
            )
            raise ContentPriceNullException(f'Could not determine list price for {content_key}') from exc

    # Note that the "content_price" key is guaranteed to exist, but the value may be None.
    return make_list_price_dict(integer_cents=content_metadata['content_price'])


def make_list_price_dict(decimal_dollars=None, integer_cents=None):
    """
    Helper to compute a list price dictionary given the non-negative price of the content.

    Usage:
        list_price_dict = make_list_price_dict(decimal_dollars=100.00)
        list_price_dict = make_list_price_dict(integer_cents=10000)

    Args:
        decimal_dollars (float): Content price in USD dollars. Must only be specified if ``integer_cents`` is None.
        integer_cents (int): Content price in USD cents. Must only be specified if ``decimal_dollars`` is None.

    Raises:
        ValueError if neither OR both arguments were specified.
    """
    if decimal_dollars is None and integer_cents is not None:
        if not isinstance(integer_cents, int):
            raise ValueError("`integer_cents` must be an integer.")
        return {
            "usd": Decimal(integer_cents) / 100,
            "usd_cents": integer_cents,
        }
    elif decimal_dollars is not None and integer_cents is None:
        return {
            "usd": decimal_dollars,
            "usd_cents": int(Decimal(decimal_dollars) * 100),
        }
    else:
        raise ValueError("Only one of `decimal_dollars` or `integer_cents` can be passed.")


def enroll_by_datetime(content_metadata):
    """
    Helper to return a datetime object representing
    the enrollment deadline for a content_metdata record.
    """
    if enroll_by_date := content_metadata.get('enroll_by_date'):
        return parse_datetime(enroll_by_date)
    return None
