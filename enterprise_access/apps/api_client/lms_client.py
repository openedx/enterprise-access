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

    def get_enterprise_learner_data(self, lms_user_ids):
        """
        Gets the data for EnterpriseCustomerUsers with the given lms_user_ids.

        Arguments:
            lms_user_ids (list of int): ids of the lms users
        Returns:
            response (dict): Dictionary containing learner data with lms_user_id as the keys
        """

        try:
            user_ids = ','.join([str(user_id) for user_id in lms_user_ids])
            endpoint = f'{self.enterprise_learner_endpoint}?user_ids={user_ids}'
            response = self.client.get(endpoint, timeout=settings.LMS_CLIENT_TIMEOUT)
            results = response.json()['results']
            learner_data = {}
            for result in results:
                learner_data[result['user']['id']] = result['user']
                learner_data[result['user']['id']]['enterprise_customer'] = result['enterprise_customer']
            return learner_data
        except requests.exceptions.HTTPError as exc:
            logger.exception(exc)
            raise
