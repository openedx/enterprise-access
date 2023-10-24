"""
Python API for interacting with content metadata
for use in the domain of SubsidyAccessPolicies.
"""
import logging

from django.conf import settings
from edx_django_utils.cache import TieredCache
from requests.exceptions import HTTPError

from enterprise_access.cache_utils import versioned_cache_key

from ..api_client.enterprise_catalog_client import EnterpriseCatalogApiClient
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
