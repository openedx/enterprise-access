"""
Tests for Discovery client.
"""

from unittest import mock
from urllib.parse import urlparse
from uuid import uuid4

import ddt
from django.conf import settings
from django.test import RequestFactory, TestCase
from faker import Faker
from requests import Response
from requests.exceptions import HTTPError

from enterprise_access.apps.api_client.enterprise_catalog_client import (
    EnterpriseCatalogApiClient,
    EnterpriseCatalogApiV1Client,
    EnterpriseCatalogUserV1ApiClient
)
from enterprise_access.apps.api_client.tests.test_constants import DATE_FORMAT_ISO_8601
from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.utils import _days_from_now


class TestEnterpriseCatalogApiClient(TestCase):
    """
    Test Enterprise Catalog Api client.
    """

    @mock.patch('requests.Response.json')
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_contains_content_items(self, mock_oauth_client, mock_json):
        mock_json.return_value = {
            "contains_content_items": True
        }
        request_response = Response()
        request_response.status_code = 200
        mock_oauth_client.return_value.get.return_value = request_response

        ent_uuid = '31d82348-b8f4-417a-85b0-1a7640623810'
        client = EnterpriseCatalogApiClient()
        contains_content_items = client.contains_content_items(ent_uuid, ['AB+CD101'])

        assert contains_content_items

        mock_oauth_client.return_value.get.assert_called_with(
            f'http://enterprise-catalog.example.com/api/v2/enterprise-catalogs/{ent_uuid}/contains_content_items/',
            params={'course_run_ids': ['AB+CD101']},
        )

    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_catalog_content_metadata(self, mock_oauth_client):
        content_keys = ['course+A', 'course+B']
        mock_response_json = {
            'next': None,
            'results': [
                {
                    'key': content_keys[0],
                    'other_metadata': 'foo',
                },
                {
                    'key': content_keys[1],
                    'other_metadata': 'bar',
                }
            ]
        }

        request_response = Response()
        request_response.status_code = 200
        mock_oauth_client.return_value.get.return_value.json.return_value = mock_response_json

        customer_uuid = uuid4()
        client = EnterpriseCatalogApiClient()
        fetched_metadata = client.catalog_content_metadata(customer_uuid, content_keys)

        self.assertEqual(fetched_metadata['results'], mock_response_json['results'])
        mock_oauth_client.return_value.get.assert_called_with(
            f'http://enterprise-catalog.example.com/api/v2/enterprise-catalogs/{customer_uuid}/get_content_metadata/',
            params={
                'content_keys': content_keys,
                'traverse_pagination': True,
            },
        )

    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_catalog_content_metadata_raises_http_error(self, mock_oauth_client):
        content_keys = ['course+A', 'course+B']
        request_response = Response()
        request_response.status_code = 400

        mock_oauth_client.return_value.get.return_value = request_response

        customer_uuid = uuid4()
        client = EnterpriseCatalogApiClient()

        with self.assertRaises(HTTPError):
            client.catalog_content_metadata(customer_uuid, content_keys)

        mock_oauth_client.return_value.get.assert_called_with(
            f'http://enterprise-catalog.example.com/api/v2/enterprise-catalogs/{customer_uuid}/get_content_metadata/',
            params={
                'content_keys': content_keys,
                'traverse_pagination': True,
            },
        )

    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_get_content_metadata_count(self, mock_oauth_client):
        mock_response_json = {
            'count': 2
        }
        request_response = Response()
        request_response.status_code = 200
        mock_oauth_client.return_value.get.return_value.json.return_value = mock_response_json

        catalog_uuid = uuid4()
        client = EnterpriseCatalogApiClient()
        fetched_metadata = client.get_content_metadata_count(catalog_uuid)

        self.assertEqual(fetched_metadata, mock_response_json['count'])
        mock_oauth_client.return_value.get.assert_called_with(
            f'http://enterprise-catalog.example.com/api/v2/enterprise-catalogs/{catalog_uuid}/get_content_metadata/',
        )


@ddt.ddt
class TestEnterpriseCatalogApiV1Client(TestCase):
    """
    Test EnterpriseCatalogApiV1Client.
    """

    @ddt.data(
        {'coerce_to_parent_course': False},
        {'coerce_to_parent_course': True},
    )
    @ddt.unpack
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_content_metadata(self, mock_oauth_client, coerce_to_parent_course):
        content_key = 'course+A'
        mock_response_json = {
            'key': content_key,
            'other_metadata': 'foo',
        }

        request_response = Response()
        request_response.status_code = 200
        mock_oauth_client.return_value.get.return_value.json.return_value = mock_response_json

        client = EnterpriseCatalogApiV1Client()
        fetched_metadata = client.content_metadata(content_key, coerce_to_parent_course=coerce_to_parent_course)

        self.assertEqual(fetched_metadata, mock_response_json)
        expected_query_params_kwarg = {}
        if coerce_to_parent_course:
            expected_query_params_kwarg |= {'params': {'coerce_to_parent_course': True}}
        mock_oauth_client.return_value.get.assert_called_with(
            f'http://enterprise-catalog.example.com/api/v1/content-metadata/{content_key}',
            **expected_query_params_kwarg,
        )

    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_content_metadata_raises_http_error(self, mock_oauth_client):
        content_key = 'course+A'
        request_response = Response()
        request_response.status_code = 400

        mock_oauth_client.return_value.get.return_value = request_response

        client = EnterpriseCatalogApiV1Client()

        with self.assertRaises(HTTPError):
            client.content_metadata(content_key)

        mock_oauth_client.return_value.get.assert_called_with(
            f'http://enterprise-catalog.example.com/api/v1/content-metadata/{content_key}',
        )


@ddt.ddt
class TestEnterpriseCatalogUserV1ApiClient(TestCase):
    """
    Test EnterpriseCatalogUserV1ApiClient
    """

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.faker = Faker()
        self.request_id_key = settings.REQUEST_ID_RESPONSE_HEADER

        self.user = UserFactory()
        self.mock_enterprise_customer_uuid = self.faker.uuid4()
        self.mock_catalog_uuid = self.faker.uuid4()
        self.mock_catalog_query_uuid = self.faker.uuid4()

    @ddt.data(
        {'enterprise_customer_uuid': 'test_uuid'},
        {'enterprise_customer_uuid': None},
    )
    @ddt.unpack
    def test_secured_algolia_api_key_endpoint(self, enterprise_customer_uuid):
        expected_url = (
            f'http://enterprise-catalog.example.com/api/v1'
            f'/enterprise-customer/{enterprise_customer_uuid}/secured-algolia-api-key/'
        )
        request = self.factory.get(expected_url)
        request.headers = {
            "Authorization": 'test-auth',
            self.request_id_key: 'test-request-id'
        }
        request.user = self.user
        context = {
            "request": request
        }
        client = EnterpriseCatalogUserV1ApiClient(context['request'])
        if enterprise_customer_uuid is None:
            with self.assertRaises(ValueError):
                client.secured_algolia_api_key_endpoint(
                    enterprise_customer_uuid=enterprise_customer_uuid
                )
        else:
            secured_algolia_api_key_url = client.secured_algolia_api_key_endpoint(
                enterprise_customer_uuid=enterprise_customer_uuid
            )
            self.assertEqual(secured_algolia_api_key_url, expected_url)

    @mock.patch('requests.Session.send')
    @mock.patch('crum.get_current_request')
    def test_secured_algolia_api_key(self, mock_crum_get_current_request, mock_send):
        expected_url = (
            f'http://enterprise-catalog.example.com/api/v1'
            f'/enterprise-customer/{self.mock_enterprise_customer_uuid}/secured-algolia-api-key/'
        )
        request = self.factory.get(expected_url)
        request.headers = {
            "Authorization": 'test-auth',
            self.request_id_key: 'test-request-id'
        }
        request.user = self.user
        context = {
            "request": request
        }

        mock_crum_get_current_request.return_value = request

        expected_result = {
            "algolia": {
                "secured_api_key": "Th15I54Fak341gOlI4K3y",
                "valid_until": _days_from_now(1, DATE_FORMAT_ISO_8601),
            },
            'catalog_uuids_to_catalog_query_uuids': {
                self.mock_catalog_uuid: self.mock_catalog_query_uuid,
            }
        }
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = expected_result

        mock_send.return_value = mock_response

        client = EnterpriseCatalogUserV1ApiClient(context['request'])
        result = client.get_secured_algolia_api_key(enterprise_customer_uuid=self.mock_enterprise_customer_uuid)
        prepared_request = mock_send.call_args[0][0]

        # Assert base request URL/method is correct
        parsed_url = urlparse(prepared_request.url)
        self.assertEqual(f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}", expected_url)
        self.assertEqual(prepared_request.method, 'GET')

        # Assert headers are correctly set
        self.assertEqual(prepared_request.headers['Authorization'], 'test-auth')
        self.assertEqual(prepared_request.headers[self.request_id_key], 'test-request-id')

        # Assert the response is as expected
        self.assertEqual(result, expected_result)
