"""
API client for calls to the license-manager service.
"""
from django.conf import settings

from enterprise_access.apps.api_client.base_oauth import BaseOAuthClient


class LicenseManagerApiClient(BaseOAuthClient):
    """
    API client for calls to the license-manager service.
    """
    api_base_url = settings.LICENSE_MANAGER_URL + '/api/v1/'
    subscriptions_endpoint = api_base_url + 'subscriptions/'

    def get_subscription_overview(self, subscription_uuid):
        """
        Call license-manager API for data about a SubscriptionPlan
        Arguments:
            subscription_uuid (UUID): UUID of the SubscriptionPlan in license-manager
        Returns:
            dict: Dictionary represention of json returned from API
        """
        endpoint = self.subscriptions_endpoint + str(subscription_uuid) + '/licenses/overview'
        response = self.client.get(endpoint, timeout=settings.LICENSE_MANAGER_CLIENT_TIMEOUT)
        return response.json()
