from django.conf import settings
from edx_enterprise_subsidy_client import get_enterprise_subsidy_api_client

def get_versioned_subsidy_client():
    kwargs = {}
    if getattr(settings, 'ENTERPRISE_SUBSIDY_API_CLIENT_VERSION', None):
        kwargs['version'] = int(settings.ENTERPRISE_SUBSIDY_API_CLIENT_VERSION)
    return get_enterprise_subsidy_api_client(**kwargs)
