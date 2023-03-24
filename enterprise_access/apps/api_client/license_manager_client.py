"""
API client for calls to the license-manager service.
"""
import logging

import requests
from django.conf import settings

from enterprise_access.apps.api_client.base_oauth import BaseOAuthClient

logger = logging.getLogger(__name__)


class LicenseManagerApiClient(BaseOAuthClient):
    """
    API client for calls to the license-manager service.
    """
    api_base_url = settings.LICENSE_MANAGER_URL + '/api/v1/'
    subscriptions_endpoint = api_base_url + 'subscriptions/'

    def get_subscription_overview(self, subscription_uuid):
        """
        Call license-manager API for data about a SubscriptionPlan.

        Arguments:
            subscription_uuid (UUID): UUID of the SubscriptionPlan in license-manager
        Returns:
            dict: Dictionary represention of json returned from API

        Example response:
        [
            { "status": "assigned", "count": 5 },
            { "status": "activated", "count": 20 },
        ]
        """
        try:
            endpoint = self.subscriptions_endpoint + str(subscription_uuid) + '/licenses/overview'
            response = self.client.get(endpoint, timeout=settings.LICENSE_MANAGER_CLIENT_TIMEOUT)
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(exc)
            raise

    def assign_licenses(self, user_emails, subscription_uuid):
        """
        Given a list of emails, assign each email a license under the given subscription.

        Arguments:
            user_emails (list of str): Emails to assign licenses to
        """

        try:
            endpoint = f'{self.subscriptions_endpoint}{subscription_uuid}/licenses/assign/'
            payload = {
                'user_emails': user_emails,
                # Skip license assignment email since we have a request approved email
                'notify_users': False
            }
            response = self.client.post(endpoint, json=payload)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(exc)
            raise
