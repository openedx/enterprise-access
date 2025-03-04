"""
Tests for the content_metadata/api.py functions.
"""

from unittest import mock
from uuid import uuid4

from django.test import TestCase
from edx_django_utils.cache import TieredCache
from requests.exceptions import HTTPError

from enterprise_access.apps.content_metadata import api


class TestContentMetadataApi(TestCase):
    """
    Tests the content_metadata/api.py functions.
    """
    def tearDown(self):
        super().tearDown()
        # Clear the cache to prevent cache bleed across tests.
        TieredCache.dangerous_clear_all_tiers()

    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient', autospec=True)
    def test_get_and_cache_catalog_content_metadata_happy_path(self, mock_client_class):
        content_keys = ['course+A', 'course+B']
        catalog_uuid = uuid4()
        # Mock results from the catalog content metadata API endpoint.
        mock_result = {
            'count': 2,
            'results': [
                {'key': 'course+A', 'data': 'things'}, {'key': 'course+B', 'data': 'stuff'},
            ],
        }
        mock_client = mock_client_class.return_value
        mock_client.catalog_content_metadata.return_value = mock_result

        metadata_list = api.get_and_cache_catalog_content_metadata(catalog_uuid, content_keys)

        self.assertEqual(mock_client.catalog_content_metadata.call_count, 1)
        # tease apart the call args, because the client is passed a set() of content keys to fetch
        call_args, _ = mock_client.catalog_content_metadata.call_args_list[0]
        self.assertEqual(call_args[0], catalog_uuid)
        self.assertEqual(sorted(call_args[1]), sorted(content_keys))
        self.assertEqual(metadata_list, mock_result['results'])

        # ask again to hit the cache, ensure that we're still at only one client fetch
        metadata_list = api.get_and_cache_catalog_content_metadata(catalog_uuid, content_keys)

        self.assertEqual(mock_client.catalog_content_metadata.call_count, 1)
        self.assertEqual(metadata_list, mock_result['results'])

        # Now get-and-cache again, but this time request keys that meet the following criteria:
        # 1. One key that should already be in the cache.
        # 2. One key that's not cached, and that the mock client will return data about.
        # 3. One key that's not cached, and for which the mock client won't return any data.
        # one of which the mock server doesn't know about

        new_content_keys = ['course+B', 'course+C', 'course+D']

        # first, setup the mock client to give us data on one of the requested keys
        mock_client.catalog_content_metadata.return_value = {
            'count': 1,
            'results': [
                {'key': 'course+C', 'data': 'etc'},
            ],
        }

        new_metadata_list = api.get_and_cache_catalog_content_metadata(catalog_uuid, new_content_keys)

        # Should only have requested courses C and D via the client
        # tease apart the call args, because the client is passed a set() of content keys to fetch
        self.assertEqual(mock_client.catalog_content_metadata.call_count, 2)
        call_args, _ = mock_client.catalog_content_metadata.call_args_list[1]
        self.assertEqual(call_args[0], catalog_uuid)
        self.assertEqual(sorted(call_args[1]), ['course+C', 'course+D'])

        # And there will be results for courses B and C, but no result for course D
        self.assertEqual(
            sorted(new_metadata_list, key=lambda record: record['key']),
            [
                {'key': 'course+B', 'data': 'stuff'},
                {'key': 'course+C', 'data': 'etc'}
            ]
        )

    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient', autospec=True)
    def test_get_and_cache_catalog_content_metadata_http_error(self, mock_client_class):
        content_keys = ['course+A', 'course+B']
        catalog_uuid = uuid4()
        mock_client = mock_client_class.return_value
        mock_client.catalog_content_metadata.side_effect = HTTPError('oh barnacles')

        with self.assertRaisesRegex(HTTPError, 'oh barnacles'):
            api.get_and_cache_catalog_content_metadata(catalog_uuid, content_keys)

        # tease apart the call args, because the client is passed a set() of content keys to fetch
        call_args, _ = mock_client.catalog_content_metadata.call_args_list[0]
        self.assertEqual(call_args[0], catalog_uuid)
        self.assertEqual(sorted(call_args[1]), sorted(content_keys))

    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiV1Client', autospec=True)
    def test_get_and_cache_content_metadata_happy_path(self, mock_client_class):
        content_key = 'course+A'
        # Mock results from the content metadata API endpoint.
        mock_result = {
            'key': 'course+A',
            'data': 'things',
        }
        mock_client = mock_client_class.return_value
        mock_client.content_metadata.return_value = mock_result

        metadata = api.get_and_cache_content_metadata(content_key)

        self.assertEqual(mock_client.content_metadata.call_count, 1)
        call_args, _ = mock_client.content_metadata.call_args_list[0]
        self.assertEqual(call_args[0], content_key)
        self.assertEqual(metadata, mock_result)

        # ask again to hit the cache, ensure that we're still at only one client fetch
        metadata = api.get_and_cache_content_metadata(content_key)

        self.assertEqual(mock_client.content_metadata.call_count, 1)
        self.assertEqual(metadata, mock_result)

        # Now get-and-cache again, but this time request key is different.
        new_content_key = 'course+B'
        new_mock_result = {
            'key': 'course+B',
            'data': 'things',
        }
        mock_client.content_metadata.return_value = new_mock_result

        new_metadata = api.get_and_cache_content_metadata(new_content_key)

        self.assertEqual(mock_client.content_metadata.call_count, 2)
        call_args, _ = mock_client.content_metadata.call_args_list[-1]
        self.assertEqual(call_args[0], new_content_key)
        self.assertEqual(new_metadata, new_mock_result)

    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiV1Client', autospec=True)
    def test_get_and_cache_content_metadata_http_error(self, mock_client_class):
        content_key = 'course+A'
        mock_client = mock_client_class.return_value
        mock_client.content_metadata.side_effect = HTTPError('oh barnacles')

        with self.assertRaisesRegex(HTTPError, 'oh barnacles'):
            api.get_and_cache_content_metadata(content_key)

        call_args, _ = mock_client.content_metadata.call_args_list[0]
        self.assertEqual(call_args[0], content_key)
