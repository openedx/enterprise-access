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

    @mock.patch('requests.Response.json')
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_get_enterprise_customer_user_data(self, mock_oauth_client, mock_json):
        """
        Verify client hits the right URL for entepriseCustomerUser data.
        """
        mock_json.return_value = {
            'next': None,
            'previous': None,
            'num_pages': 1,
            'current_page': 1,
            'start': 0,
            'count': 3,
            'results': [
                {
                    'user': {
                        'id': 1,
                        'email': 'user1@example.com',
                    },
                    'enterprise_customer': {
                        'contact_email': 'contact@example.com',
                    }
                },
                {
                    'user': {
                        'id': 2,
                        'email': 'user2@example.com',
                    },
                    'enterprise_customer': {
                        'contact_email': 'contact@example.com',
                    }
                },
                {
                    'user': {
                        'id': 3,
                        'email': 'user3@example.com',
                    },
                    'enterprise_customer': {
                        'contact_email': 'contact@example.com',
                    }
                },
            ]

        }
        mock_oauth_client.return_value.get.return_value = Response()

        client = LmsApiClient()
        learner_data = client.get_enterprise_learner_data([1,2,3])

        assert len(learner_data) == 3
        assert learner_data[1]['email'] == 'user1@example.com'
        assert learner_data[1]['enterprise_customer']['contact_email'] == 'contact@example.com'

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
