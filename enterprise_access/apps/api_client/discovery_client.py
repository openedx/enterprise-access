"""
API client for calls to Discovery.
"""
import logging

import requests
from django.conf import settings

from enterprise_access.apps.api_client.base_oauth import BaseOAuthClient

logger = logging.getLogger(__name__)


class DiscoveryApiClient(BaseOAuthClient):
    """
    API client for calls to the Discovery service.
    """
    discovery_api_base_url = settings.DISCOVERY_URL + '/api/v1/'
    courses_endpoint = discovery_api_base_url + 'courses'

    def get_course_data(self, course_id):
        """
        Gets the data for a course for the given course_id.

        Arguments:
            course_id (string): id of the course (e.g. AB+CD101). Note, NOT a course_run id
        Returns:
            response (dict): Dictionary containing course data
        """

        try:
            endpoint = f'{self.courses_endpoint}/{course_id}'
            response = self.client.get(endpoint, timeout=settings.DISCOVERY_CLIENT_TIMEOUT)
        except requests.exceptions.HTTPError as exc:
            logger.exception(exc)
            raise

        return response.json()
