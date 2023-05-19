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
            endpoint = f'{self.enterprise_customer_endpoint}{enterprise_customer_uuid}/'
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

    def unlink_users_from_enterprise(self, enterprise_customer_uuid, user_emails, is_relinkable=True):
        """
        Unlink users with the given emails from the enterprise.

        Arguments:
            enterprise_customer_uuid (str): id of the enterprise customer
            emails (list of str): emails of the users to remove

        Returns:
            None
        """

        try:
            endpoint = f'{self.enterprise_customer_endpoint}{enterprise_customer_uuid}/unlink_users/'
            payload = {
                "user_emails": user_emails,
                "is_relinkable": is_relinkable
            }
            response = self.client.post(endpoint, payload)
            response.raise_for_status()
        except requests.exceptions.HTTPError:
            msg = 'Failed to unlink users from %s.'
            logger.exception(msg, enterprise_customer_uuid)
            raise

    def enterprise_contains_learner(self, enterprise_customer_uuid, learner_id):
        """
        Verify if `learner_id` is a part of an enterprise represented by `enterprise_customer_uuid`.

        Arguments:
            enterprise_customer_uuid (UUID): UUID of the enterprise customer.
            learner_id (int): LMS user id of a learner.

        Returns:
            bool: True if learner is linked with enterprise else False
        """

        result = False
        ec_uuid = str(enterprise_customer_uuid)
        query_params = {'enterprise_customer_uuid': ec_uuid, 'user_ids': learner_id}

        try:
            url = self.enterprise_learner_endpoint
            response = self.client.get(url, params=query_params, timeout=settings.LMS_CLIENT_TIMEOUT)
            response.raise_for_status()
            json_response = response.json()
            results = json_response.get('results')
            results = results and results[0]
            if results and results['enterprise_customer']['uuid'] == ec_uuid and results['user']['id'] == learner_id:
                result = True
        except requests.exceptions.HTTPError:
            logger.exception('Failed to fetch data from LMS. URL: [%s].', url)
        except KeyError:
            logger.exception('Incorrect data received from LMS. [%s]', url)

        return result
