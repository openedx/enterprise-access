"""
Utility functions for Enterprise Access API.
"""

from uuid import UUID

from edx_django_utils.cache.utils import DEFAULT_TIMEOUT, TieredCache, get_cache_key
from rest_framework.exceptions import ParseError


def get_enterprise_uuid_from_query_params(request):
    """
    Extracts enterprise_customer_uuid from query params.
    """

    enterprise_customer_uuid = request.query_params.get('enterprise_customer_uuid')

    if not enterprise_customer_uuid:
        return None

    try:
        return UUID(enterprise_customer_uuid)
    except ValueError as ex:
        raise ParseError('{} is not a valid uuid.'.format(enterprise_customer_uuid)) from ex

def get_enterprise_uuid_from_request_data(request):
    """
    Extracts enterprise_customer_uuid from the request payload.
    """

    enterprise_customer_uuid = request.data.get('enterprise_customer_uuid')

    if not enterprise_customer_uuid:
        return None

    try:
        return UUID(enterprise_customer_uuid)
    except ValueError as ex:
        raise ParseError('{} is not a valid uuid.'.format(enterprise_customer_uuid)) from ex


# Can use this to replace above logic in other utils functions,
# but not yet to avoid merge conflicts
def validate_uuid(uuid):
    """ Check if UUID is valid. If not, raise an error. """
    try:
        return UUID(uuid)
    except ValueError as ex:
        raise ParseError('{} is not a valid uuid.'.format(uuid)) from ex

def acquire_subsidy_policy_lock(subsidy_policy_uuid, django_cache_timeout=DEFAULT_TIMEOUT, **cache_key_kwargs):
    """
    Acquires a lock for the provided subsidy policy.  Returns True if the lock was
    acquired, False otherwise.
    """
    cache_key = get_cache_key(resource='subsidy_policy', subsidy_policy_id=subsidy_policy_uuid, **cache_key_kwargs)
    cached_response = TieredCache.get_cached_response(cache_key)
    if cached_response.is_found:
        return False
    TieredCache.set_all_tiers(cache_key, 'ACQUIRED', django_cache_timeout)
    return True

def release_subsidy_policy_lock(subsidy_policy_uuid, **cache_key_kwargs):
    """
    Releases a lock for the provided subsidy policy.
    Returns True unless an exception is raised.
    """
    cache_key = get_cache_key(resource='subsidy_policy', subsidy_policy_id=subsidy_policy_uuid, **cache_key_kwargs)
    TieredCache.delete_all_tiers(cache_key)
    return True
