"""
Tests for Discovery client.
"""

import mock
from django.conf import settings
from django.test import TestCase
from requests import Response

from enterprise_access.apps.api_client.discovery_client import DiscoveryApiClient


class TestDiscoveryApiClient(TestCase):
    """
    Test Discovery Api client.
    """

    @mock.patch('requests.Response.json')
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_get_course_data(self, mock_oauth_client, mock_json):
        mock_json.return_value = {
            'key': 'AB+CD101',
            'uuid': '31d82348-b8f4-417a-85b0-1a7640623810',
            'title': 'How to Bake a Pie: A Slice of Heaven',
            'course_runs': {
                'more_stuff_not_listed_here?': True
            },
            'enterprise_customer': {
                'contact_email': 'contact@example.com',
            },
            'image': None,
            'short_description': '',
            'url_slug': 'aa-test',
            'full_description': '',
            'level_type': None,
            'more_stuff_not_listed_here?': True
        }
        mock_oauth_client.return_value.get.return_value = Response()

        client = DiscoveryApiClient()
        course_data = client.get_course_data('AB+CD101')

        assert course_data['key'] == 'AB+CD101'
        assert course_data['title'] == 'How to Bake a Pie: A Slice of Heaven'

        expected_url = (
            'http://discovery.example.com/'
            'api/v1/'
            'courses/'
            'AB+CD101'
        )
        mock_oauth_client.return_value.get.assert_called_with(
            expected_url,
            timeout=settings.DISCOVERY_CLIENT_TIMEOUT,
        )
