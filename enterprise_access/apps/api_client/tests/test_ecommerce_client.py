"""
Tests for Ecommerce client.
"""

import mock
from django.conf import settings
from django.test import TestCase
from requests import Response

from enterprise_access.apps.api_client.ecommerce_client import EcommerceApiClient


class TestEcommerceClient(TestCase):
    """
    Test Ecommerce client.
    """

    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_get_coupon_from_overview(self, mock_oauth_client):
        """
        Verify client hits the right URL.
        """
        mock_get = mock.Mock()
        mock_get.return_value = Response()
        mock_oauth_client.get = mock_get

        ecommerce_client = EcommerceApiClient()
        ecommerce_client.get_coupon_overview('some_enterprise_uuid', 'some_coupon_id')

        expected_url = (
            'http://ecommerce.example.com'
            '/api/v2/'
            'enterprise/coupons/some_enterprise_uuid/overview/'
        )
        expected_params = {'coupon_id': 'some_coupon_id'}
        mock_oauth_client.return_value.get.assert_called_with(
            expected_url,
            params=expected_params,
            timeout=settings.ECOMMERCE_CLIENT_TIMEOUT,
        )
