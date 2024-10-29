"""
Tests for License Manager client.
"""

from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.test import RequestFactory, TestCase
from faker import Faker
from requests import Response

from enterprise_access.apps.api_client.constants import LicenseStatuses
from enterprise_access.apps.api_client.license_manager_client import (
    LicenseManagerApiClient,
    LicenseManagerUserApiClient
)
from enterprise_access.apps.api_client.tests.test_constants import DATE_FORMAT_ISO_8601, DATE_FORMAT_ISO_8601_MS
from enterprise_access.utils import _days_from_now


class TestLicenseManagerApiClient(TestCase):
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


class TestLicenseManagerUserApiClient(TestCase):
    """
     Test License Manager with BaseUserApiClient.
     """
    def setUp(self):
        super().setUp()
        self.api_base_url = LicenseManagerUserApiClient.api_base_url
        self.factory = RequestFactory()
        self.faker = Faker()
        self.request_id_key = settings.REQUEST_ID_RESPONSE_HEADER

        self.mock_enterprise_customer_uuid = self.faker.uuid4()
        self.mock_enterprise_customer_slug = "test_slug"
        self.mock_enterprise_catalog_uuid = self.faker.uuid4()
        self.mock_user_email = self.faker.email()
        self.mock_learner_license_uuid = self.faker.uuid4()
        self.mock_learner_license_activation_uuid = self.faker.uuid4()
        self.mock_license_activation_key = self.faker.uuid4()
        self.mock_auto_apply_uuid = self.faker.uuid4()
        self.mock_subscription_plan_uuid = self.faker.uuid4()
        self.mock_customer_agreement_uuid = self.faker.uuid4()

        self.base_paginated_response = {
            "next": None,
            "previous": None,
            "count": 0,
            "results": [],
        }
        self.mock_subscription_plan = {
            "title": "mock_title",
            "uuid": self.mock_subscription_plan_uuid,
            "start_date": _days_from_now(-50, DATE_FORMAT_ISO_8601),
            "expiration_date": _days_from_now(50, DATE_FORMAT_ISO_8601),
            "enterprise_customer_uuid": self.mock_enterprise_customer_uuid,
            "enterprise_catalog_uuid": self.mock_enterprise_catalog_uuid,
            "is_active": True,
            "is_current": True,
            "is_revocation_cap_enabled": False,
            "days_until_expiration": _days_from_now(50, DATE_FORMAT_ISO_8601),
            "days_until_expiration_including_renewals": _days_from_now(50, DATE_FORMAT_ISO_8601),
            "is_locked_for_renewal_processing": False,
            "should_auto_apply_licenses": False,
            "created": _days_from_now(-60, DATE_FORMAT_ISO_8601)
        }
        self.mock_customer_agreement = {
            "uuid": self.mock_customer_agreement_uuid,
            "enterprise_customer_uuid": self.mock_enterprise_customer_uuid,
            "enterprise_customer_slug": "mock_slug",
            "default_enterprise_catalog_uuid": self.mock_enterprise_catalog_uuid,
            "disable_expiration_notifications": False,
            "net_days_until_expiration": _days_from_now(50, DATE_FORMAT_ISO_8601),
            "subscription_for_auto_applied_licenses": self.mock_subscription_plan_uuid,
            "available_subscription_catalogs": [
                self.mock_enterprise_catalog_uuid,
            ],
            "enable_auto_applied_subscriptions_with_universal_link": False,
            "has_custom_license_expiration_messaging": False,
            "modal_header_text": None,
            "expired_subscription_modal_messaging": None,
            "button_label_in_modal": None,
            "url_for_button_in_modal": None
        }
        self.mock_learner_license_result_response = {
            "uuid": self.mock_learner_license_uuid,
            "status": LicenseStatuses.ACTIVATED,
            "user_email": self.mock_user_email,
            "activation_date": _days_from_now(-10, DATE_FORMAT_ISO_8601_MS),
            "last_remind_date": _days_from_now(-5, DATE_FORMAT_ISO_8601_MS),
            "subscription_plan_uuid": self.mock_subscription_plan_uuid,
            "revoked_date": None,
            "activation_key": self.mock_license_activation_key,
            "subscription_plan": self.mock_subscription_plan
        }
        self.mock_learner_license_activation_response = {
            "uuid": self.mock_learner_license_activation_uuid,
            "status": LicenseStatuses.ACTIVATED,
            "user_email": self.mock_user_email,
            "activation_date": _days_from_now(-10, DATE_FORMAT_ISO_8601_MS),
            "last_remind_date": _days_from_now(-5, DATE_FORMAT_ISO_8601_MS),
            "subscription_plan_title": "mock_subscription_plan_title",
            "subscription_plan_expiration_date": _days_from_now(50, DATE_FORMAT_ISO_8601),
            "activation_link": self.faker.url()
        }
        self.mock_auto_apply_response = {
            "uuid": self.mock_auto_apply_uuid,
            "enterprise_customer_uuid": self.mock_enterprise_customer_uuid,
            "enterprise_customer_slug": self.mock_enterprise_customer_slug,
            "default_enterprise_catalog_uuid": self.mock_enterprise_catalog_uuid,
            "disable_expiration_notifications": True,
            "net_days_until_expiration": _days_from_now(50, DATE_FORMAT_ISO_8601),
            "subscription_for_auto_applied_licenses": self.mock_subscription_plan_uuid,
            "available_subscription_catalogs": [
                self.mock_enterprise_catalog_uuid,
            ],
            "enable_auto_applied_subscriptions_with_universal_link": False,
            "has_custom_license_expiration_messaging": False,
            "modal_header_text": None,
            "expired_subscription_modal_messaging": None,
            "button_label_in_modal": None,
            "url_for_button_in_modal": None,
            "subscriptions": [
                self.mock_subscription_plan
            ]
        }

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
                self.mock_learner_license_result_response,
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

        mock_request = mock.MagicMock()
        mock_request.headers.get.return_value = request.headers.get(self.request_id_key)
        mock_crum_get_current_request.return_value = mock_request

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
        expected_params = {'enterprise_customer_uuid': [self.mock_enterprise_customer_uuid]}
        self.assertEqual(parsed_params, expected_params)

        self.assertEqual(prepared_request.headers['Authorization'], 'test-auth')
        self.assertEqual(prepared_request.headers[self.request_id_key], 'test-request-id')

        self.assertIn('timeout', prepared_request_kwargs)
        self.assertEqual(prepared_request_kwargs['timeout'], settings.LICENSE_MANAGER_CLIENT_TIMEOUT)

        self.assertEqual(result, expected_result)

    @mock.patch('requests.Session.send')
    @mock.patch('crum.get_current_request')
    def test_activate_license(self, mock_crum_get_current_request, mock_send):
        expected_result = self.mock_learner_license_activation_response
        expected_url = LicenseManagerUserApiClient.license_activation_endpoint

        request = self.factory.post(expected_url)
        request.headers = {
            "Authorization": 'test-auth',
            self.request_id_key: "test-request-id"
        }
        context = {
            "request": request
        }

        mock_request = mock.MagicMock()
        mock_request.headers.get.return_value = request.headers.get(self.request_id_key)
        mock_crum_get_current_request.return_value = mock_request

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
        expected_result = self.mock_auto_apply_response
        expected_url = LicenseManagerUserApiClient.auto_apply_license_endpoint(self, self.mock_customer_agreement_uuid)

        request = self.factory.post(expected_url)
        request.headers = {
            "Authorization": 'test-auth',
            self.request_id_key: "test-request-id"
        }
        context = {
            "request": request
        }

        mock_request = mock.MagicMock()
        mock_request.headers.get.return_value = request.headers.get(self.request_id_key)
        mock_crum_get_current_request.return_value = mock_request

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
