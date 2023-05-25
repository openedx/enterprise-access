"""
Python API for interacting with content metadata
for use in the domain of SubsidyAccessPolicies.
"""
import logging

from django.conf import settings
from edx_django_utils.cache import TieredCache
from requests.exceptions import HTTPError

from ..api_client.enterprise_catalog_client import EnterpriseCatalogApiClient
from .utils import get_versioned_subsidy_client, versioned_cache_key

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
        logger.info('[METADATA CACHE HIT] for key %s', cache_key)
        return cached_response.value

    logger.info('[METADATA CACHE MISS] for key %s', cache_key)

    client = get_versioned_subsidy_client()
    try:
        metadata = client.get_subsidy_content_data(
            enterprise_customer_uuid,
            content_key,
        )
    except HTTPError as exc:
        raise exc

    TieredCache.set_all_tiers(cache_key, metadata, timeout or DEFAULT_CACHE_TIMEOUT)
    logger.info('[METADATA CACHE SET] for key = %s, value = %s', cache_key, metadata)
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
        logger.info('[CATALOG INCLUSION CACHE HIT] for key %s', cache_key)
        return cached_response.value

    logger.info('[CATALOG INCLUSION CACHE MISS] for key %s', cache_key)
    try:
        result = EnterpriseCatalogApiClient().contains_content_items(
            enterprise_catalog_uuid,
            [content_key],
        )
    except HTTPError as exc:
        raise exc

    TieredCache.set_all_tiers(cache_key, result, timeout or DEFAULT_CACHE_TIMEOUT)
    logger.info('[CATALOG INCLUSION CACHE SET] for key = %s, value = %s', cache_key, result)
    return result
