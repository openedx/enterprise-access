"""
API client for calls to the LMS.
"""
import logging

import requests
from django.conf import settings

from enterprise_access.apps.api_client.base_oauth import BaseOAuthClient

logger = logging.getLogger(__name__)


class LmsApiClient(BaseOAuthClient):
    """
    API client for calls to the LMS service.
    """
    enterprise_api_base_url = settings.LMS_URL + '/enterprise/api/v1/'
    enterprise_learner_endpoint = enterprise_api_base_url + 'enterprise-learner/'
    enterprise_customer_endpoint = enterprise_api_base_url + 'enterprise-customer/'

    def get_enterprise_customer_data(self, enterprise_customer_uuid):
        """
        Gets the data for an EnterpriseCustomer for the given uuid.

        Arguments:
            enterprise_customer_uuid (string): id of the enterprise customer
        Returns:
            dictionary containing enterprise customer metadata
        """

        try:
            endpoint = f'{self.enterprise_customer_endpoint}{enterprise_customer_uuid}'
            response = self.client.get(endpoint, timeout=settings.LMS_CLIENT_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            logger.exception(exc)
            raise

    def get_enterprise_admin_users(self, enterprise_customer_uuid):
        """
        Gets a list of admin users for a given enterprise customer.
        Arguments:
            enterprise_customer_uuid (UUID): UUID of the enterprise customer associated with an enterprise
        Returns:
            A list of dicts in the form of
                {
                    'id': str,
                    'username': str,
                    'first_name': str,
                    'last_name': str,
                    'email': str,
                    'is_staff': bool,
                    'is_active': bool,
                    'date_joined': str,
                    'ecu_id': str,
                    'created': str
                }
        """

        query_params = f'?enterprise_customer_uuid={str(enterprise_customer_uuid)}&role=enterprise_admin'

        try:
            url = self.enterprise_learner_endpoint + query_params
            results = []

            while url:
                response = self.client.get(url, timeout=settings.LMS_CLIENT_TIMEOUT)
                response.raise_for_status()
                resp_json = response.json()
                url = resp_json['next']

                for result in resp_json['results']:
                    user_data = result['user']
                    user_data.update(ecu_id=result['id'], created=result['created'])
                    results.append(user_data)

        except requests.exceptions.HTTPError as exc:
            logger.error(
                'Failed to fetch enterprise admin users for %r because %r',
                enterprise_customer_uuid,
                response.text,
            )
            raise exc

        return results
