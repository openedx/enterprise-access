"""
Utils for subsidy_access_policy
"""
from django.conf import settings
from edx_django_utils.cache import RequestCache
from edx_enterprise_subsidy_client import get_enterprise_subsidy_api_client

from enterprise_access import __version__ as code_version

CACHE_KEY_SEP = ':'
CACHE_NAMESPACE = 'subsidy_access_policy'


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
    return CACHE_KEY_SEP.join(components)


def request_cache():
    """
    Helper that returns a namespaced RequestCache instance.
    """
    return RequestCache(namespace=CACHE_NAMESPACE)
