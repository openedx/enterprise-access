"""
Tests for Subsidy client.
"""

import mock
from django.conf import settings
from django.test import TestCase
from requests import Response
from requests.exceptions import HTTPError

from enterprise_access.apps.api_client.subsidy_client import EnterpriseSubsidyApiClient


class TestEnterpriseSubsidyApiClient(TestCase):
    """
    Tests for EnterpriseSubsidyApiClient.
    """

    @mock.patch('requests.Response.json')
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_get_subsidies(self, mock_oauth_client, mock_json):
        """
        Validate the behavior of `get_subsidies` method on the subsidy client.
        """
        mock_json.return_value = {
            'count': 1,
            'next': None,
            'previous': None,
            'results': [
                {
                    'uuid': '0ad52747-3029-4e00-ac09-4bb11a05dc6b',
                    'title': 'Test Subsidy',
                    'enterprise_customer_uuid': 'd1338c35-5d8a-43fa-9350-c563195d62ce',
                    'active_datetime': '2023-03-09T07:40:29Z',
                    'expiration_datetime': '2024-03-09T07:40:33Z',
                    'unit': 'usd_cents',
                    'reference_id': 'test-subsidy',
                    'reference_type': 'opportunity_product_id',
                }
            ]
        }

        request_response = Response()
        request_response.status_code = 200
        mock_oauth_client.return_value.get.return_value = request_response

        client = EnterpriseSubsidyApiClient()
        subsidies = client.get_subsidies()

        assert subsidies

        mock_oauth_client.return_value.get.assert_called_with(
            'http://enterprise-subsidy.example.com/api/v1/subsidies',
            params=None,
            timeout=settings.SUBSIDY_CLIENT_TIMEOUT
        )

    @mock.patch('enterprise_access.apps.api_client.subsidy_client.logger')
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_get_subsidies_error(self, mock_oauth_client, mock_logger):
        """
        Validate the behavior of `get_subsidies` method on the subsidy client.
        """
        request_response = Response()
        request_response.status_code = 200
        mock_oauth_client.return_value.get.return_value = request_response

        client = EnterpriseSubsidyApiClient()
        client.client = mock.Mock()
        client.client.get.side_effect = HTTPError()

        with self.assertRaises(HTTPError):
            client.get_subsidies()

        assert mock_logger.exception.call_count == 1

    @mock.patch('requests.Response.json')
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_get_transactions(self, mock_oauth_client, mock_json):
        """
        Validate the behavior of `get_transactions` method on the subsidy client.
        """
        mock_json.return_value = {
            'count': 1,
            'next': None,
            'previous': None,
            'results': [
                {
                    "uuid": "258019f0-0183-4f74-82ff-218aaff8e410",
                    "state": "created",
                    "idempotency_key": "test-transaction",
                    "lms_user_id": 3,
                    "content_key": "course-v1:edX+DemoX+Demo_Course",
                    "quantity": 1,
                    "unit": "usd_cents",
                    "reference_id": "test-reference",
                    "reference_type": "learner_credit_enterprise_course_enrollment_id",
                    "subsidy_access_policy_uuid": "0ad52747-3029-4e00-ac09-4bb11a05dc6b",
                    "metadata": None,
                    "created": "2023-03-09T07:43:30.346704Z",
                    "modified": "2023-03-09T07:43:30.346704Z",
                    "reversal": None
                }
            ]
        }

        request_response = Response()
        request_response.status_code = 200
        mock_oauth_client.return_value.get.return_value = request_response

        client = EnterpriseSubsidyApiClient()
        subsidies = client.get_transactions(filters={'state': 'created'})

        assert subsidies

        mock_oauth_client.return_value.get.assert_called_with(
            'http://enterprise-subsidy.example.com/api/v1/transactions',
            params={'state': 'created'},
            timeout=settings.SUBSIDY_CLIENT_TIMEOUT
        )

    @mock.patch('enterprise_access.apps.api_client.subsidy_client.logger')
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_get_transactions_error(self, mock_oauth_client, mock_logger):
        """
        Validate the behavior of `get_subsidies` method on the subsidy client.
        """
        request_response = Response()
        request_response.status_code = 200
        mock_oauth_client.return_value.get.return_value = request_response

        client = EnterpriseSubsidyApiClient()
        client.client = mock.Mock()
        client.client.get.side_effect = HTTPError()

        with self.assertRaises(HTTPError):
            client.get_transactions()

        assert mock_logger.exception.call_count == 1

    @mock.patch('requests.Response.json')
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_get_subsidy_data(self, mock_oauth_client, mock_json):
        """
        Validate the behavior of `get_subsidy_data` method on the subsidy client.
        """
        mock_json.return_value = {
            "uuid": "0ad52747-3029-4e00-ac09-4bb11a05dc6b",
            "title": "Test Subsidy",
            "enterprise_customer_uuid": "d1338c35-5d8a-43fa-9350-c563195d62ce",
            "active_datetime": "2023-03-09T07:40:29Z",
            "expiration_datetime": "2024-03-09T07:40:33Z",
            "unit": "usd_cents",
            "reference_id": "test-subsidy",
            "reference_type": "opportunity_product_id"
        }

        request_response = Response()
        request_response.status_code = 200
        mock_oauth_client.return_value.get.return_value = request_response

        client = EnterpriseSubsidyApiClient()
        subsidies = client.get_subsidy_data('0ad52747-3029-4e00-ac09-4bb11a05dc6b')

        assert subsidies

        mock_oauth_client.return_value.get.assert_called_with(
            'http://enterprise-subsidy.example.com/api/v1/subsidies/0ad52747-3029-4e00-ac09-4bb11a05dc6b/',
            timeout=settings.SUBSIDY_CLIENT_TIMEOUT
        )

    @mock.patch('enterprise_access.apps.api_client.subsidy_client.logger')
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_get_subsidy_data_error(self, mock_oauth_client, mock_logger):
        """
        Validate the behavior of `get_subsidies` method on the subsidy client.
        """
        request_response = Response()
        request_response.status_code = 200
        mock_oauth_client.return_value.get.return_value = request_response

        client = EnterpriseSubsidyApiClient()
        client.client = mock.Mock()
        client.client.get.side_effect = HTTPError()

        with self.assertRaises(HTTPError):
            client.get_subsidy_data('test-uuid')

        assert mock_logger.exception.call_count == 1

    @mock.patch('requests.Response.json')
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_get_transaction_data(self, mock_oauth_client, mock_json):
        """
        Validate the behavior of `get_transaction_data` method on the subsidy client.
        """
        mock_json.return_value = {
            "uuid": "258019f0-0183-4f74-82ff-218aaff8e410",
            "state": "created",
            "idempotency_key": "test-transaction",
            "lms_user_id": 3,
            "content_key": "course-v1:edX+DemoX+Demo_Course",
            "quantity": 1,
            "unit": "usd_cents",
            "reference_id": "test-reference",
            "reference_type": "learner_credit_enterprise_course_enrollment_id",
            "subsidy_access_policy_uuid": "0ad52747-3029-4e00-ac09-4bb11a05dc6b",
            "metadata": None,
            "created": "2023-03-09T07:43:30.346704Z",
            "modified": "2023-03-09T07:43:30.346704Z",
            "reversal": None
        }

        request_response = Response()
        request_response.status_code = 200
        mock_oauth_client.return_value.get.return_value = request_response

        client = EnterpriseSubsidyApiClient()
        subsidies = client.get_transaction_data('258019f0-0183-4f74-82ff-218aaff8e410')

        assert subsidies

        mock_oauth_client.return_value.get.assert_called_with(
            'http://enterprise-subsidy.example.com/api/v1/transactions/258019f0-0183-4f74-82ff-218aaff8e410/',
            timeout=settings.SUBSIDY_CLIENT_TIMEOUT
        )

    @mock.patch('enterprise_access.apps.api_client.subsidy_client.logger')
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_get_transactions_data_error(self, mock_oauth_client, mock_logger):
        """
        Validate the behavior of `get_subsidies` method on the subsidy client.
        """
        request_response = Response()
        request_response.status_code = 200
        mock_oauth_client.return_value.get.return_value = request_response

        client = EnterpriseSubsidyApiClient()
        client.client = mock.Mock()
        client.client.get.side_effect = HTTPError()

        with self.assertRaises(HTTPError):
            client.get_transaction_data('test-uuid')

        assert mock_logger.exception.call_count == 1

    @mock.patch('requests.Response.json')
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_create_transaction(self, mock_oauth_client, mock_json):
        """
        Validate the behavior of `create_transaction` method on the subsidy client.
        """
        data = {
            "state": 'created',
            "lms_user_id": 4,
            "learner_id": 4,
            "subsidy_uuid": '0ad52747-3029-4e00-ac09-4bb11a05dc6b',
            "content_key": "course-v1:edX+DemoX+Demo_Course",
            "quantity": 1,
            "reference_id": "",
            "access_policy_uuid": '0ad52747-3029-4e00-ac09-4bb11a05dc6b',
            "metadata": ""
        }

        mock_json.return_value = {
            "uuid": "258019f0-0183-4f74-82ff-218aaff8e410",
            "state": "created",
            "idempotency_key": "test-transaction",
            "lms_user_id": 3,
            "content_key": "course-v1:edX+DemoX+Demo_Course",
            "quantity": 1,
            "unit": "usd_cents",
            "reference_id": "test-reference",
            "reference_type": "learner_credit_enterprise_course_enrollment_id",
            "subsidy_access_policy_uuid": "0ad52747-3029-4e00-ac09-4bb11a05dc6b",
            "metadata": None,
            "created": "2023-03-09T07:43:30.346704Z",
            "modified": "2023-03-09T07:43:30.346704Z",
            "reversal": None
        }

        request_response = Response()
        request_response.status_code = 200
        mock_oauth_client.return_value.get.return_value = request_response

        client = EnterpriseSubsidyApiClient()
        subsidies = client.create_transaction(payload=data)

        assert subsidies

        mock_oauth_client.return_value.post.assert_called_with(
            'http://enterprise-subsidy.example.com/api/v1/transactions/',
            data,
            timeout=settings.SUBSIDY_CLIENT_TIMEOUT
        )

    @mock.patch('enterprise_access.apps.api_client.subsidy_client.logger')
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_create_transaction_error(self, mock_oauth_client, mock_logger):
        """
        Validate the behavior of `get_subsidies` method on the subsidy client.
        """
        request_response = Response()
        request_response.status_code = 200
        mock_oauth_client.return_value.get.return_value = request_response

        client = EnterpriseSubsidyApiClient()
        client.client = mock.Mock()
        client.client.post.side_effect = HTTPError()

        with self.assertRaises(HTTPError):
            client.create_transaction({})

        assert mock_logger.exception.call_count == 1
