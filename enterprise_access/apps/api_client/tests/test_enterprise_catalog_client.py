"""
Tests for Discovery client.
"""

from unittest import mock
from uuid import uuid4

import ddt
from django.test import TestCase
from requests import Response
from requests.exceptions import HTTPError

from enterprise_access.apps.api_client.enterprise_catalog_client import (
    EnterpriseCatalogApiClient,
    EnterpriseCatalogApiV1Client
)


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
