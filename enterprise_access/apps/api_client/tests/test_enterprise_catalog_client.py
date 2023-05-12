"""
Tests for Discovery client.
"""

from unittest import mock

from django.test import TestCase
from requests import Response

from enterprise_access.apps.api_client.enterprise_catalog_client import EnterpriseCatalogApiClient


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
            f'http://enterprise-catalog.example.com/api/v1/enterprise-catalogs/{ent_uuid}/contains_content_items/',
            params={'course_run_ids': ['AB+CD101']},
        )
