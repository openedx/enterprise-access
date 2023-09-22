"""
Tests for License Manager client.
"""

from unittest import mock
from uuid import uuid4

import ddt
import requests
from django.conf import settings
from django.test import TestCase
from rest_framework import status

from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.api_client.tests.test_utils import MockResponse

TEST_ENTERPRISE_UUID = uuid4()
TEST_USER_EMAILS = [
    'larry@stooges.com',
    'moe@stooges.com',
    'curly@stooges.com',
]


@ddt.ddt
class TestLmsApiClient(TestCase):
    """
    Test LMS Api client.
    """

    def setUp(self):
        super().setUp()
        self.enterprise_learner_list_view_response = {
            'next': None,
            'previous': None,
            'num_pages': 1,
            'current_page': 1,
            'start': 0,
            'count': 3,
            'results': [
                {
                    'id': 10,
                    'created': '2021-06-17T18:57:59.056286Z',
                    'user': {
                        'id': 1,
                        'email': 'user1@example.com',
                    },
                    'enterprise_customer': {
                        'contact_email': 'contact@example.com',
                        'uuid': 'ent-customer-uuid',
                    }
                },
                {
                    'id': 20,
                    'created': '2021-06-18T18:57:59.056286Z',
                    'user': {
                        'id': 2,
                        'email': 'user2@example.com',
                    },
                    'enterprise_customer': {
                        'contact_email': 'contact@example.com',
                        'uuid': 'ent-customer-uuid',
                    }
                },
                {
                    'id': 30,
                    'created': '2021-06-20T18:57:59.056286Z',
                    'user': {
                        'id': 3,
                        'email': 'user3@example.com',
                    },
                    'enterprise_customer': {
                        'contact_email': 'contact@example.com',
                        'uuid': 'ent-customer-uuid',
                    }
                },
            ]
        }

    @mock.patch('requests.Response.json')
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_get_enterprise_admin_users(self, mock_oauth_client, mock_json):
        """
        Verify client hits the right URL for entepriseCustomerUser data.
        """
        mock_json.return_value = self.enterprise_learner_list_view_response
        mock_oauth_client.return_value.get.return_value = requests.Response()
        mock_oauth_client.return_value.get.return_value.status_code = 200

        client = LmsApiClient()
        learner_data = client.get_enterprise_admin_users('some-uuid')

        assert len(learner_data) == 3
        assert learner_data[1]['email'] == 'user2@example.com'
        assert learner_data[1]['ecu_id'] == 20
        assert learner_data[1]['created'] == '2021-06-18T18:57:59.056286Z'

        expected_url = (
            'http://edx-platform.example.com/'
            'enterprise/api/v1/'
            'enterprise-learner/'
            '?enterprise_customer_uuid=some-uuid&role=enterprise_admin'
        )
        mock_oauth_client.return_value.get.assert_called_with(
            expected_url,
            timeout=settings.LMS_CLIENT_TIMEOUT,
        )

    @mock.patch('requests.Response.json')
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_get_enterprise_customer_data(self, mock_oauth_client, mock_json):
        """
        Verify client hits the right URL for entepriseCustomer data.
        """
        mock_json.return_value = {
            'uuid': 'some-uuid',
            'slug': 'some-test-slug',
        }
        mock_oauth_client.return_value.get.return_value = requests.Response()
        mock_oauth_client.return_value.get.return_value.status_code = 200

        client = LmsApiClient()
        customer_data = client.get_enterprise_customer_data('some-uuid')

        assert customer_data['uuid'] == 'some-uuid'
        assert customer_data['slug'] == 'some-test-slug'

        expected_url = (
            'http://edx-platform.example.com/'
            'enterprise/api/v1/'
            'enterprise-customer/'
            'some-uuid/'
        )
        mock_oauth_client.return_value.get.assert_called_with(
            expected_url,
            timeout=settings.LMS_CLIENT_TIMEOUT,
        )

    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_unlink_users_from_enterprise(self, mock_oauth_client):
        """
        Verify client hits the right URL to unlink users from an enterprise.
        """

        mock_enterprise_uuid = uuid4()
        mock_user_emails = ['abc@email.com', 'efg@email.com']
        mock_oauth_client.return_value.get.return_value = requests.Response()
        mock_oauth_client.return_value.get.return_value.status_code = 200

        client = LmsApiClient()
        client.unlink_users_from_enterprise(
            mock_enterprise_uuid,
            mock_user_emails,
        )

        expected_url = (
            'http://edx-platform.example.com/enterprise/api/v1/'
            f'enterprise-customer/{mock_enterprise_uuid}/unlink_users/'
        )
        expected_payload = {
            "user_emails": mock_user_emails,
            "is_relinkable": True
        }
        mock_oauth_client.return_value.post.assert_called_with(
            expected_url,
            expected_payload
        )

    @mock.patch('requests.Response.json')
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_enterprise_contains_learner(self, mock_oauth_client, mock_json):
        """
        Verify enterprise_contains_learner works as expected.
        """
        mock_enterprise_uuid = str(uuid4())
        user_id = 1234
        mock_oauth_client.return_value.get.return_value = requests.Response()
        mock_oauth_client.return_value.get.return_value.status_code = 200

        mock_json.return_value = {
            'results': [
                {
                    'enterprise_customer': {
                        'uuid': mock_enterprise_uuid
                    },
                    'user': {
                        'id': user_id
                    }
                }
            ]
        }

        query_params = {'enterprise_customer_uuid': mock_enterprise_uuid, 'user_ids': user_id}
        client = LmsApiClient()
        enterprise_contains_learner = client.enterprise_contains_learner(mock_enterprise_uuid, user_id)
        assert enterprise_contains_learner

        mock_oauth_client.return_value.get.assert_called_with(
            'http://edx-platform.example.com/enterprise/api/v1/enterprise-learner/',
            params=query_params,
            timeout=settings.LMS_CLIENT_TIMEOUT
        )

    @ddt.data(
        {
            'mock_response_status': status.HTTP_204_NO_CONTENT,
            'mock_response_json': [],
        },
        {
            'mock_response_status': status.HTTP_201_CREATED,
            'mock_response_json': [
                {'enterprise_customer': str(TEST_ENTERPRISE_UUID), 'user_email': TEST_USER_EMAILS[0]},
                {'enterprise_customer': str(TEST_ENTERPRISE_UUID), 'user_email': TEST_USER_EMAILS[1]},
                {'enterprise_customer': str(TEST_ENTERPRISE_UUID), 'user_email': TEST_USER_EMAILS[2]},
            ],
        },
        {
            'mock_response_status': status.HTTP_400_BAD_REQUEST,
            'mock_response_json': {'detail': 'Bad Request'},
        },
    )
    @ddt.unpack
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_create_pending_enterprise_users(self, mock_oauth_client, mock_response_status, mock_response_json):
        """
        Test the ``create_pending_enterprise_users`` method.
        """
        # Mock the response from the enterprise API.
        mock_oauth_client.return_value.post.return_value = MockResponse(
            mock_response_json,
            mock_response_status,
        )

        client = LmsApiClient()

        if mock_response_status >= 400:
            with self.assertRaises(requests.exceptions.HTTPError):
                response = client.create_pending_enterprise_users(str(TEST_ENTERPRISE_UUID), TEST_USER_EMAILS)
        else:
            response = client.create_pending_enterprise_users(str(TEST_ENTERPRISE_UUID), TEST_USER_EMAILS)
            assert response.status_code == mock_response_status
            assert response.json() == mock_response_json

        mock_oauth_client.return_value.post.assert_called_once_with(
            client.pending_enterprise_learner_endpoint,
            json=[
                {'enterprise_customer': str(TEST_ENTERPRISE_UUID), 'user_email': TEST_USER_EMAILS[0]},
                {'enterprise_customer': str(TEST_ENTERPRISE_UUID), 'user_email': TEST_USER_EMAILS[1]},
                {'enterprise_customer': str(TEST_ENTERPRISE_UUID), 'user_email': TEST_USER_EMAILS[2]},
            ],
        )
