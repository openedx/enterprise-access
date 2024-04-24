"""
Python API for interacting with the lms client
for use in the domain of SubsidyAccessPolicies.
"""
import logging

from django.conf import settings
from edx_django_utils.cache import TieredCache
from requests.exceptions import HTTPError

from enterprise_access.cache_utils import versioned_cache_key

from enterprise_access.apps.api_client.lms_client import LmsApiClient

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TIMEOUT = getattr(settings, 'LMS_CLIENT_CACHE_TIMEOUT', 60 * 5)


def get_and_cache_enterprise_contains_learner(enterprise_customer_uuid, learner_id, timeout=None):
    """
    Determines if the learner identified by the 'learner_id is linked
    to a specified enterprise identified by the 'enterprise_customer_uuid

    Returns: A bool to specify if the learner is associated to the enterprise
    """

    cache_key = versioned_cache_key('get_enterprise_contains_learner', enterprise_customer_uuid, learner_id)
    cached_response = TieredCache.get_cached_response(cache_key)
    if cached_response.is_found:
        logger.info(f'Cache hit for customer {enterprise_customer_uuid} and learner id {learner_id}')
        return cached_response.value

    lms_client = LmsApiClient()
    try:
        is_learner_linked_to_enterprise = lms_client.enterprise_contains_learner(
            enterprise_customer_uuid,
            learner_id
        )
    except HTTPError as exc:
        raise exc

    logger.info(
        'Fetched enterprise contains learner for customer %s and learner_id %s',
        enterprise_customer_uuid,
        learner_id
    )
    TieredCache.set_all_tiers(cache_key, is_learner_linked_to_enterprise, timeout or DEFAULT_CACHE_TIMEOUT)
    return is_learner_linked_to_enterprise


