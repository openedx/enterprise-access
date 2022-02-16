"""
Tests for License Manager client.
"""

import mock
from django.conf import settings
from django.test import TestCase
from requests import Response

from enterprise_access.apps.api_client.lms_client import LmsApiClient


class TestLmsApiClient(TestCase):
    """
    Test LMS Api client.
    """

    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_get_enterprise_customer_user_data(self, mock_oauth_client):
        """
        Verify client hits the right URL for entepriseCustomerUser data.
        """
        mock_get = mock.Mock()
        mock_get.return_value = Response()
        mock_oauth_client.get = mock_get

        client = LmsApiClient()
        client.get_enterprise_learner_data([1,2,3])

        expected_url = (
            'http://edx-platform.example.com/'
            'enterprise/api/v1/'
            'enterprise-learner/'
            '?user_ids=1,2,3'
        )
        mock_oauth_client.return_value.get.assert_called_with(
            expected_url,
            timeout=settings.LMS_CLIENT_TIMEOUT,
        )
