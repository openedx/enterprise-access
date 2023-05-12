"""
Tests for License Manager client.
"""

from unittest import mock

from django.conf import settings
from django.test import TestCase
from requests import Response

from enterprise_access.apps.api_client.license_manager_client import LicenseManagerApiClient


class TestLicenseManagerClient(TestCase):
    """
    Test License Manager client.
    """

    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_get_subscription(self, mock_oauth_client):
        """
        Verify client hits the right URL.
        """
        mock_get = mock.Mock()
        mock_get.return_value = Response()
        mock_oauth_client.get = mock_get

        lm_client = LicenseManagerApiClient()
        lm_client.get_subscription_overview('some_subscription_uuid')

        expected_url = (
            'http://license-manager.example.com'
            '/api/v1/'
            'subscriptions/some_subscription_uuid'
            '/licenses/overview'
        )
        mock_oauth_client.return_value.get.assert_called_with(
            expected_url,
            timeout=settings.LICENSE_MANAGER_CLIENT_TIMEOUT,
        )
