"""
Python API for interacting with the lms client
for use in the domain of SubsidyAccessPolicies.
"""
import logging

from django.conf import settings
from edx_django_utils.cache import TieredCache
from requests.exceptions import HTTPError

from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.cache_utils import versioned_cache_key

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TIMEOUT = settings.ENTERPRISE_USER_RECORD_CACHE_TIMEOUT


def get_and_cache_enterprise_learner_record(enterprise_customer_uuid, learner_id, timeout=DEFAULT_CACHE_TIMEOUT):
    """
    Fetches the enterprise learner record from the Lms client if it exists.
    Uses the `learner_id` and `enterprise_customer_uuid` to determine if
    the customer is linked to the enterprise.

    If a enterprise learner record is identified, we cache the response for 5 minutes.

    Returns: Enterprise learner record or None
    """
    cache_key = versioned_cache_key('get_enterprise_user', enterprise_customer_uuid, learner_id)
    cached_response = TieredCache.get_cached_response(cache_key)
    if cached_response.is_found:
        logger.info(f'Cache hit for customer {enterprise_customer_uuid} and learner id {learner_id}')
        return cached_response.value

    lms_client = LmsApiClient()
    try:
        enterprise_learner_record = lms_client.get_enterprise_user(
            enterprise_customer_uuid=enterprise_customer_uuid,
            learner_id=learner_id
        )
    except HTTPError as exc:
        raise exc

    logger.info(
        'Fetched enterprise customer learner record for customer %s and learner_id %s',
        enterprise_customer_uuid,
        learner_id
    )
    TieredCache.set_all_tiers(cache_key, enterprise_learner_record, timeout)
    return enterprise_learner_record
