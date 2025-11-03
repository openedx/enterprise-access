"""
Python API for interacting with content metadata.
TODO: refactor subsidy_access_policy/content_metadata_api.py
into this module.
"""
import logging
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from edx_django_utils.cache import TieredCache

from enterprise_access.cache_utils import versioned_cache_key

from ..api_client.enterprise_catalog_client import EnterpriseCatalogApiClient, EnterpriseCatalogApiV1Client
from .constants import CENTS_PER_DOLLAR, DEFAULT_CONTENT_PRICE, CourseModes, ProductSources

logger = logging.getLogger(__name__)

CONTENT_MODES_BY_PRODUCT_SOURCE = {
    ProductSources.EDX.value: CourseModes.EDX_VERIFIED.value,
    # TODO: additionally support other course modes/types beyond Executive Education for the 2U product source
    ProductSources.TWOU.value: CourseModes.EXECUTIVE_EDUCATION.value,
}


def get_and_cache_catalog_content_metadata(
    enterprise_catalog_uuid,
    content_keys,
    timeout=settings.CONTENT_METADATA_CACHE_TIMEOUT,
):
    """
    Returns the metadata corresponding to the requested
    ``content_keys`` within the provided ``enterprise_catalog_uuid``,
    as told by the enterprise-access service.  Utilizes a cache per-content-record,
    that is, each combination of (enterprise_catalog_uuid, key) for key in content_keys
    is cached independently.

    Returns: A list of dictionaries containing content metadata for the given keys.
    Raises: An HTTPError if there's a problem getting the content metadata
      via the enterprise-catalog service.
    """
    # List of content metadata dicts we'll ultimately return
    metadata_results_list = []

    # We'll start with the assumption that we need to fetch every key
    # from the catalog service, and then prune down as we find records
    # in the cache
    keys_to_fetch = set(content_keys)

    # Maintains a mapping of cache keys for each content key
    cache_keys_by_content_key = {}
    for content_key in content_keys:
        cache_key = versioned_cache_key(
            'get_catalog_content_metadata',
            enterprise_catalog_uuid,
            content_key,
        )
        cache_keys_by_content_key[content_key] = cache_key

    # Use our computed cache keys to do a bulk get from the Django cache
    cached_content_metadata = cache.get_many(cache_keys_by_content_key.values())

    # Go through our cache hits, append data to results and prune
    # from the list of keys to fetch from the catalog service.
    for content_key, cache_key in cache_keys_by_content_key.items():
        if cache_key in cached_content_metadata:
            metadata_results_list.append(cached_content_metadata[cache_key])
            keys_to_fetch.remove(content_key)

    # Here's the list of results fetched from the catalog service
    fetched_metadata = []
    if keys_to_fetch:
        fetched_metadata = _fetch_catalog_content_metadata_with_client(enterprise_catalog_uuid, keys_to_fetch)

    # Do a bulk set into the cache of everything we just had to fetch from the catalog service
    content_metadata_to_cache = {}
    for fetched_record in fetched_metadata:
        cache_key = cache_keys_by_content_key.get(fetched_record.get('key'))
        content_metadata_to_cache[cache_key] = fetched_record

    cache.set_many(content_metadata_to_cache, timeout)

    # Add to our results list everything we just had to fetch
    metadata_results_list.extend(fetched_metadata)

    # Log a warning for any content key that the caller asked for metadata about,
    # but which was not found in cache OR from the catalog service.
    missing_keys = set(content_keys) - {record.get('key') for record in metadata_results_list}
    if missing_keys:
        logger.warning(
            'Could not fetch content keys %s from catalog %s',
            missing_keys,
            enterprise_catalog_uuid,
        )

    # Return our results list
    return metadata_results_list


def _fetch_catalog_content_metadata_with_client(enterprise_catalog_uuid, content_keys):
    """
    Helper to isolate the task of fetching content metadata via our client.
    """
    client = EnterpriseCatalogApiClient()
    response_payload = client.catalog_content_metadata(
        enterprise_catalog_uuid,
        list(content_keys),
    )
    results = response_payload['results']
    return results


def get_and_cache_content_metadata(
    content_identifier,
    coerce_to_parent_course=False,
    timeout=settings.CONTENT_METADATA_CACHE_TIMEOUT,
):
    """
    Fetch & cache content metadata from the enterprise-catalog catalog-/customer-agnostic endoint.

    Returns:
        dict: Serialized content metadata from the enterprise-catalog API.

    Raises:
        HTTPError: If there's a problem calling the enterprise-catalog API.
    """
    cache_key = versioned_cache_key(
        'get_and_cache_content_metadata',
        content_identifier,
        f'coerce_to_parent_course={coerce_to_parent_course}',
    )
    cached_response = TieredCache.get_cached_response(cache_key)
    if cached_response.is_found:
        return cached_response.value

    content_metadata = EnterpriseCatalogApiV1Client().content_metadata(
        content_identifier,
        coerce_to_parent_course=coerce_to_parent_course,
    )
    if content_metadata:
        TieredCache.set_all_tiers(
            cache_key,
            content_metadata,
            django_cache_timeout=timeout,
        )
    else:
        logger.warning('Could not fetch metadata for content %s', content_identifier)
    return content_metadata


def product_source_for_content(content_data):
    """
    Helps get the product source string, given a dict of ``content_data``.
    """
    if product_source := content_data.get('product_source'):
        source_name = product_source.get('slug')
        if source_name in CONTENT_MODES_BY_PRODUCT_SOURCE:
            return source_name
    return ProductSources.EDX.value


def mode_for_content(content_data):
    """
    Helper to extract the relevant enrollment mode for a piece of content metadata.
    """
    product_source = product_source_for_content(content_data)
    return CONTENT_MODES_BY_PRODUCT_SOURCE.get(product_source, CourseModes.EDX_VERIFIED.value)


def get_course_run(content_identifier, content_data):
    """
    Given a content_identifier (key, run key, uuid) extract the appropriate course_run.
    When given a run key or uuid for a run, extract that. When given a course key or
    course uuid, extract the advertised course_run.
    """
    if content_data.get('content_type') == 'courserun':
        return content_data

    course_run_identifier = content_identifier
    # if the supplied content_identifer refers to the course, look for an advertised run
    if content_identifier == content_data.get('key') or content_identifier == content_data.get('uuid'):
        course_run_identifier = content_data.get('advertised_course_run_uuid')
    for course_run in content_data.get('course_runs', []):
        if course_run_identifier == course_run.get('key') or course_run_identifier == course_run.get('uuid'):
            return course_run
    return {}


def price_for_content_fallback(content_data, course_run_data):
    """
    Fallback logic for `price_for_content` logic if the `normalized_metadata_by_run` field is None.
    The fallback logic is the original logic for determining the `content_price` before
    using normalized metadata as the first source of truth for `content_price`.
    """
    content_price = None

    product_source = product_source_for_content(content_data)
    if product_source == ProductSources.TWOU.value:
        enrollment_mode_for_content = mode_for_content(content_data)
        for entitlement in content_data.get('entitlements', []):
            if entitlement.get('mode') == enrollment_mode_for_content:
                content_price = entitlement.get('price')
    else:
        content_price = course_run_data.get('first_enrollable_paid_seat_price')

    if not content_price:
        logger.info(
            f"Could not determine price for content key {content_data.get('key')} "
            f"and course run key {course_run_data.get('key')}, setting to default."
        )
        content_price = DEFAULT_CONTENT_PRICE

    return content_price


def price_for_content(content_data, course_run_data):
    """
    Helper to return the "official" price for content.
    The endpoint at ``self.content_metadata_url`` will always return price fields
    as USD (dollars), possibly as a string or a float.  This method converts
    those values to USD cents as an integer.
    """
    content_price = None
    course_run_key = course_run_data.get('key')

    if course_run_key in content_data.get('normalized_metadata_by_run', {}):
        if normalized_price := content_data['normalized_metadata_by_run'][course_run_key].get('content_price'):
            content_price = normalized_price

    if not content_price:
        content_price = price_for_content_fallback(content_data, course_run_data)

    return int(Decimal(content_price) * CENTS_PER_DOLLAR)


def summary_data_for_content(content_identifier, content_data):
    """
    Returns a summary dict specifying the content_uuid, content_key, source, and content_price
    for a dict of content metadata.
    """
    course_run_content = get_course_run(content_identifier, content_data)
    content_mode = mode_for_content(content_data)
    return {
        'content_title': content_data.get('title'),
        'content_uuid': content_data.get('uuid'),
        'content_key': content_data.get('key'),
        'course_run_uuid': course_run_content.get('uuid'),
        'course_run_key': course_run_content.get('key'),
        'source': product_source_for_content(content_data),
        'mode': content_mode,
        'content_price': price_for_content(content_data, course_run_content),
    }


def get_canonical_content_price_from_metadata(content_identifier, content_data):
    """
    Main entry point: returns price in cents for the given content.
    """
    content_metadata = summary_data_for_content(content_identifier, content_data)
    return content_metadata['content_price']
