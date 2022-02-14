"""
API client for calls to the LMS.
"""
import logging

from django.conf import settings

from enterprise_access.apps.api_client.base_oauth import BaseOAuthClient


logger = logging.getLogger(__name__)


class LmsApiClient(BaseOAuthClient):
    """
    API client for calls to the LMS service.
    """
    enterprise_api_base_url = settings.LMS_URL + '/enterprise/api/v1/'
    enterprise_learner_endpoint = enterprise_api_base_url + 'enterprise-learner/'

    def get_enterprise_learner_data(self, lms_user_id):
        """
        Gets the data for an EnterpriseCustomerUser with a given lms_user_id.
        Arguments:
            lms_user_id (int): id of the enterprise customer associated with an enterprise
        Returns:
            response (dict): JSON response data
        """
        endpoint = self.enterprise_learner_endpoint + str(lms_user_id)
        response = self.client.get(endpoint, timeout=settings.LMS_CLIENT_TIMEOUT)
        return response.json()
