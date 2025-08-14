"""
Tests for License Manager client.
"""
import uuid
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.test import RequestFactory, TestCase

from enterprise_access.apps.api_client.license_manager_client import (
    NEW_SUBSCRIPTION_CHANGE_REASON,
    LicenseManagerApiClient,
    LicenseManagerUserApiClient
)
from enterprise_access.apps.api_client.tests.test_utils import MockLicenseManagerMetadataMixin


class TestLicenseManagerApiClient(TestCase):
    """
    Test License Manager client.
    """

    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient', autospec=True)
    def test_get_subscription(self, mock_oauth_client):
        """
        Verify client hits the right URL.
        """
        mock_get = mock_oauth_client.return_value.get

        lm_client = LicenseManagerApiClient()
        result = lm_client.get_subscription_overview('some_subscription_uuid')

        self.assertEqual(result, mock_get.return_value.json.return_value)
        expected_url = (
            'http://license-manager.example.com'
            '/api/v1/'
            'subscriptions/some_subscription_uuid'
            '/licenses/overview'
        )
        mock_get.assert_called_once_with(
            expected_url,
            timeout=settings.LICENSE_MANAGER_CLIENT_TIMEOUT,
        )

    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient', autospec=True)
    def test_get_customer_agreement(self, mock_oauth_client):
        mock_get = mock_oauth_client.return_value.get
        mock_get.return_value.json.return_value = {
            'results': [{'foo': 'bar'}],
        }

        lm_client = LicenseManagerApiClient()
        result = lm_client.get_customer_agreement('some-customer-uuid')

        self.assertEqual(result, {'foo': 'bar'})
        expected_url = (
            'http://license-manager.example.com'
            '/api/v1/customer-agreement/?enterprise_customer_uuid=some-customer-uuid'
        )
        mock_get.assert_called_with(expected_url)

    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient', autospec=True)
    def test_create_customer_agreement(self, mock_oauth_client):
        mock_post = mock_oauth_client.return_value.post
        customer_uuid = uuid.uuid4()
        catalog_uuid = uuid.uuid4()
        lm_client = LicenseManagerApiClient()

        result = lm_client.create_customer_agreement(
            customer_uuid, 'customer-slug', default_catalog_uuid=catalog_uuid,
            disable_expiration_notifications=True,
        )

        self.assertEqual(result, mock_post.return_value.json.return_value)
        expected_url = (
            'http://license-manager.example.com'
            '/api/v1/provisioning-admins/customer-agreement/'
        )
        expected_payload = {
            'enterprise_customer_uuid': str(customer_uuid),
            'enterprise_customer_slug': 'customer-slug',
            'default_enterprise_catalog_uuid': str(catalog_uuid),
            'disable_expiration_notifications': True,
        }
        mock_post.assert_called_once_with(
            expected_url,
            json=expected_payload,
        )

    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient', autospec=True)
    def test_create_subscription_plan(self, mock_oauth_client):
        mock_post = mock_oauth_client.return_value.post
        customer_agreement_uuid = uuid.uuid4()
        enterprise_catalog_uuid = uuid.uuid4()
        salesforce_opportunity_line_item = '123abc'
        title = 'My new subscription plan'
        start_date = '2025-01-01'
        expiration_date = '2026-12-31'
        desired_num_licenses = 10

        lm_client = LicenseManagerApiClient()

        result = lm_client.create_subscription_plan(
            customer_agreement_uuid, salesforce_opportunity_line_item,
            title, start_date, expiration_date, desired_num_licenses,
            enterprise_catalog_uuid=enterprise_catalog_uuid, other_field='foo'
        )

        self.assertEqual(result, mock_post.return_value.json.return_value)
        expected_url = (
            'http://license-manager.example.com'
            '/api/v1/provisioning-admins/subscriptions/'
        )
        expected_payload = {
            'customer_agreement': str(customer_agreement_uuid),
            'enterprise_catalog_uuid': str(enterprise_catalog_uuid),
            'salesforce_opportunity_line_item': salesforce_opportunity_line_item,
            'title': title,
            'start_date': start_date,
            'expiration_date': expiration_date,
            'desired_num_licenses': desired_num_licenses,
            'change_reason': NEW_SUBSCRIPTION_CHANGE_REASON,
            'for_internal_use_only': settings.PROVISIONING_DEFAULTS['subscription']['for_internal_use_only'],
            'product': settings.PROVISIONING_DEFAULTS['subscription']['product_id'],
            'is_active': settings.PROVISIONING_DEFAULTS['subscription']['is_active'],
            'other_field': 'foo',
        }
        mock_post.assert_called_once_with(
            expected_url,
            json=expected_payload,
        )


class TestLicenseManagerUserApiClient(MockLicenseManagerMetadataMixin):
    """
     Test License Manager with BaseUserApiClient.
     """
    def setUp(self):
        super().setUp()
        self.api_base_url = LicenseManagerUserApiClient.api_base_url
        self.factory = RequestFactory()
        self.request_id_key = settings.REQUEST_ID_RESPONSE_HEADER

    @mock.patch('requests.Session.send')
    @mock.patch('crum.get_current_request')
    def test_get_subscription_licenses_for_learner(self, mock_crum_get_current_request, mock_send):
        expected_result = {
            **self.base_paginated_response,
            "count": 1,
            "num_pages": 1,
            "current_page": 1,
            "start": 0,
            "results": [
                self.mock_subscription_license,
            ],
            "customer_agreement": self.mock_customer_agreement,
        }
        expected_url = LicenseManagerUserApiClient.learner_licenses_endpoint

        request = self.factory.get(expected_url)

        request.headers = {
            "Authorization": 'test-auth',
            self.request_id_key: 'test-request-id'
        }
        context = {
            "request": request
        }

        mock_crum_get_current_request.return_value = request

        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = expected_result

        mock_send.return_value = mock_response

        lm_client = LicenseManagerUserApiClient(context['request'])
        result = lm_client.get_subscription_licenses_for_learner(self.mock_enterprise_customer_uuid)

        mock_send.assert_called_once()

        prepared_request = mock_send.call_args[0][0]
        prepared_request_kwargs = mock_send.call_args[1]

        # Assert base request URL/method is correct
        parsed_url = urlparse(prepared_request.url)
        self.assertEqual(f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}", expected_url)
        self.assertEqual(prepared_request.method, 'GET')

        # Assert query parameters are correctly set
        parsed_params = parse_qs(parsed_url.query)
        expected_params = {
            'enterprise_customer_uuid': [self.mock_enterprise_customer_uuid],
            'page_size': ['100'],
        }
        self.assertEqual(parsed_params, expected_params)

        # Assert headers are correctly set
        self.assertEqual(prepared_request.headers['Authorization'], 'test-auth')
        self.assertEqual(prepared_request.headers[self.request_id_key], 'test-request-id')

        # Assert timeout is set
        self.assertIn('timeout', prepared_request_kwargs)
        self.assertEqual(prepared_request_kwargs['timeout'], settings.LICENSE_MANAGER_CLIENT_TIMEOUT)

        # Assert result is as expected
        self.assertEqual(result, expected_result)

    @mock.patch('requests.Session.send')
    @mock.patch('crum.get_current_request')
    def test_activate_license(self, mock_crum_get_current_request, mock_send):
        expected_result = self.mock_subscription_license
        expected_url = LicenseManagerUserApiClient.license_activation_endpoint

        request = self.factory.post(expected_url)
        request.headers = {
            "Authorization": 'test-auth',
            self.request_id_key: "test-request-id"
        }
        context = {
            "request": request
        }

        mock_crum_get_current_request.return_value = request

        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = expected_result

        mock_send.return_value = mock_response

        lm_client = LicenseManagerUserApiClient(context['request'])
        result = lm_client.activate_license(self.mock_license_activation_key)

        mock_send.assert_called_once()

        prepared_request = mock_send.call_args[0][0]
        prepared_request_kwargs = mock_send.call_args[1]

        # Assert base request URL/method is correct
        parsed_url = urlparse(prepared_request.url)
        self.assertEqual(f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}", expected_url)
        self.assertEqual(prepared_request.method, 'POST')

        # Assert query parameters are correctly set
        parsed_params = parse_qs(parsed_url.query)
        expected_params = {'activation_key': [self.mock_license_activation_key]}
        self.assertEqual(parsed_params, expected_params)

        self.assertEqual(prepared_request.headers['Authorization'], 'test-auth')
        self.assertEqual(prepared_request.headers[self.request_id_key], 'test-request-id')

        self.assertIn('timeout', prepared_request_kwargs)
        self.assertEqual(prepared_request_kwargs['timeout'], settings.LICENSE_MANAGER_CLIENT_TIMEOUT)

        self.assertEqual(result, expected_result)

    @mock.patch('requests.Session.send')
    @mock.patch('crum.get_current_request')
    def test_auto_apply_license(self, mock_crum_get_current_request, mock_send):
        expected_result = self.mock_learner_license_auto_apply_response
        expected_url = LicenseManagerUserApiClient.auto_apply_license_endpoint(self, self.mock_customer_agreement_uuid)

        request = self.factory.post(expected_url)
        request.headers = {
            "Authorization": 'test-auth',
            self.request_id_key: "test-request-id"
        }
        context = {
            "request": request
        }

        mock_crum_get_current_request.return_value = request

        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = expected_result

        mock_send.return_value = mock_response

        lm_client = LicenseManagerUserApiClient(context['request'])
        result = lm_client.auto_apply_license(self.mock_customer_agreement_uuid)

        mock_send.assert_called_once()

        prepared_request = mock_send.call_args[0][0]
        prepared_request_kwargs = mock_send.call_args[1]

        # Assert base request URL/method is correct
        parsed_url = urlparse(prepared_request.url)
        self.assertEqual(f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}", expected_url)
        self.assertEqual(prepared_request.method, 'POST')

        self.assertEqual(prepared_request.headers['Authorization'], 'test-auth')
        self.assertEqual(prepared_request.headers[self.request_id_key], 'test-request-id')

        self.assertIn('timeout', prepared_request_kwargs)
        self.assertEqual(prepared_request_kwargs['timeout'], settings.LICENSE_MANAGER_CLIENT_TIMEOUT)

        self.assertEqual(result, expected_result)
