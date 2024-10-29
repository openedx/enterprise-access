"""
API client for calls to the license-manager service.
"""
import logging

import requests
from django.conf import settings

from enterprise_access.apps.api_client.base_oauth import BaseOAuthClient
from enterprise_access.apps.api_client.base_user import BaseUserApiClient

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


class LicenseManagerUserApiClient(BaseUserApiClient):
    """
    API client for calls to the license-manager service. This client is used for user-specific calls,
    passing the original Authorization header from the originating request.
    """

    api_base_url = f"{settings.LICENSE_MANAGER_URL}/api/v1/"
    learner_licenses_endpoint = f"{api_base_url}learner-licenses/"
    license_activation_endpoint = f"{api_base_url}license-activation/"

    def auto_apply_license_endpoint(self, customer_agreement_uuid):
        return f"{self.api_base_url}customer-agreement/{customer_agreement_uuid}/auto-apply/"

    def get_subscription_licenses_for_learner(self, enterprise_customer_uuid):
        """
        Get subscription licenses for a learner.

        Arguments:
            enterprise_customer_uuid (str): UUID of the enterprise customer
        Returns:
            dict: Dictionary representation of json returned from API
        """
        query_params = {
            'enterprise_customer_uuid': enterprise_customer_uuid,
        }
        url = self.learner_licenses_endpoint
        try:
            response = self.get(url, params=query_params, timeout=settings.LICENSE_MANAGER_CLIENT_TIMEOUT)
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(f"Failed to get subscription licenses for learner: {exc}")
            raise

    def activate_license(self, activation_key):
        """
        Activate a license.

        Arguments:
            license_uuid (str): UUID of the license to activate
        """
        try:
            url = self.license_activation_endpoint
            query_params = {
                'activation_key': activation_key,
            }
            response = self.post(url, params=query_params, timeout=settings.LICENSE_MANAGER_CLIENT_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(f"Failed to activate license: {exc}")
            raise

    def auto_apply_license(self, customer_agreement_uuid):
        """
        Activate a license.

        Arguments:
            license_uuid (str): UUID of the license to activate
        """
        try:
            url = self.auto_apply_license_endpoint(customer_agreement_uuid=customer_agreement_uuid)
            response = self.post(url, timeout=settings.LICENSE_MANAGER_CLIENT_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(f"Failed to auto-apply license: {exc}")
            raise
