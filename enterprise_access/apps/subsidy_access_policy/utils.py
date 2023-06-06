"""
Utils for subsidy_access_policy
"""
import hashlib

from django.conf import settings
from edx_django_utils.cache import RequestCache
from edx_enterprise_subsidy_client import get_enterprise_subsidy_api_client

from enterprise_access import __version__ as code_version

CACHE_KEY_SEP = ':'
CACHE_NAMESPACE = 'subsidy_access_policy'

LEDGERED_SUBSIDY_IDEMPOTENCY_KEY_PREFIX = 'ledger-for-subsidy'
TRANSACTION_METADATA_KEYS = {
    'lms_user_id',
    'content_key',
    'subsidy_access_policy_uuid',
    'historical_redemptions_uuids',
}


def get_versioned_subsidy_client():
    """
    Returns an instance of the enterprise subsidy client as the version specified by the
    Django setting `ENTERPRISE_SUBSIDY_API_CLIENT_VERSION`, if any.
    """
    kwargs = {}
    if getattr(settings, 'ENTERPRISE_SUBSIDY_API_CLIENT_VERSION', None):
        kwargs['version'] = int(settings.ENTERPRISE_SUBSIDY_API_CLIENT_VERSION)
    return get_enterprise_subsidy_api_client(**kwargs)


def versioned_cache_key(*args):
    """
    Utility to produce a versioned cache key, which includes
    an optional settings variable and the current code version,
    so that we can perform key-based cache invalidation.
    """
    components = [str(arg) for arg in args]
    components.append(code_version)
    if stamp_from_settings := getattr(settings, 'CACHE_KEY_VERSION_STAMP', None):
        components.append(stamp_from_settings)
    decoded_cache_key = CACHE_KEY_SEP.join(components)
    return hashlib.sha512(decoded_cache_key.encode()).hexdigest()


def request_cache():
    """
    Helper that returns a namespaced RequestCache instance.
    """
    return RequestCache(namespace=CACHE_NAMESPACE)


def create_idempotency_key_for_transaction(subsidy_uuid, **metadata):
    """
    Create a key that allows a transaction to be created idempotently.
    """
    idpk_data = {
        tx_key: value
        for tx_key, value in metadata.items()
        if tx_key in TRANSACTION_METADATA_KEYS
    }
    hashed_metadata = hashlib.md5(str(idpk_data).encode()).hexdigest()
    return f'{LEDGERED_SUBSIDY_IDEMPOTENCY_KEY_PREFIX}-{subsidy_uuid}-{hashed_metadata}'
