"""
Utils for subsidy_access_policy
"""
from django.conf import settings
from edx_enterprise_subsidy_client import get_enterprise_subsidy_api_client


def get_versioned_subsidy_client():
    """
    Returns an instance of the enterprise subsidy client as the version specified by the
    Django setting `ENTERPRISE_SUBSIDY_API_CLIENT_VERSION`, if any.
    """
    kwargs = {}
    if getattr(settings, 'ENTERPRISE_SUBSIDY_API_CLIENT_VERSION', None):
        kwargs['version'] = int(settings.ENTERPRISE_SUBSIDY_API_CLIENT_VERSION)
    return get_enterprise_subsidy_api_client(**kwargs)
