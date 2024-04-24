"""
Tests for License Manager client.
"""
from datetime import datetime, timedelta
from unittest import mock
from uuid import uuid4

import ddt
import requests
from django.conf import settings
from django.test import TestCase
from rest_framework import status

from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.api_client.tests.test_utils import MockResponse
from test_utils import TEST_ENTERPRISE_UUID, TEST_USER_ID, TEST_USER_RECORD

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
    def test_get_enterprise_user(self, mock_oauth_client, mock_json):
        """
        Verify get_enterprise_user works as expected.
        """
        mock_oauth_client.return_value.get.return_value = requests.Response()
        mock_oauth_client.return_value.get.return_value.status_code = 200

        mock_json.return_value = {
            'results': [
                TEST_USER_RECORD
            ]
        }
        query_params = {'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID), 'user_ids': TEST_USER_ID}
        client = LmsApiClient()
        get_enterprise_user = client.get_enterprise_user(str(TEST_ENTERPRISE_UUID), TEST_USER_ID)
        assert get_enterprise_user == TEST_USER_RECORD

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

    @mock.patch('requests.Response.json')
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_get_pending_enterprise_group_memberships(self, mock_oauth_client, mock_json):
        """
        Verify get_pending_enterprise_group_memberships works as expected.
        """
        mock_oauth_client.return_value.get.return_value = requests.Response()
        mock_oauth_client.return_value.get.return_value.status_code = 200
        enterprise_group_membership_uuid = uuid4()
        recent_action = datetime.strftime(datetime.today() - timedelta(days=5), '%B %d, %Y')
        recent_action_no_reminder_needed = datetime.strftime(datetime.today() - timedelta(days=4), '%B %d, %Y')
        mock_json.return_value = {
            "next": None,
            "previous": None,
            "count": 1,
            "num_pages": 1,
            "current_page": 1,
            "start": 0,
            "results": [
                {
                    "pending_enterprise_customer_user_id": 1,
                    "enterprise_group_membership_uuid": enterprise_group_membership_uuid,
                    "member_details": {
                        "user_email": "test1@2u.com",
                        "user_name": " "
                    },
                    "recent_action": f'Invited: {recent_action}',
                    "member_status": "pending",
                },
                {
                    "pending_enterprise_customer_user_id": 2,
                    "enterprise_group_membership_uuid": enterprise_group_membership_uuid,
                    "member_details": {
                        "user_email": "test2@2u.com",
                        "user_name": " "
                    },
                    "recent_action": f"Invited: {recent_action_no_reminder_needed}",
                    "member_status": "pending",
                }
            ]
        }
        client = LmsApiClient()
        expected_return = [{
            "pending_enterprise_customer_user_id": 1,
            "user_email": "test1@2u.com",
            "recent_action": f'Invited: {recent_action}'}]
        pending_enterprise_group_memberships = (
            client.get_pending_enterprise_group_memberships(enterprise_group_membership_uuid))
        mock_oauth_client.return_value.get.assert_called_with(
            f'{settings.LMS_URL}/enterprise/api/v1/enterprise-group/' +
            f'{enterprise_group_membership_uuid}/learners/?pending_users_only=true',
            timeout=settings.LMS_CLIENT_TIMEOUT
        )
        assert pending_enterprise_group_memberships == expected_return
