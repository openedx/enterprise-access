"""
Unit tests for the ``provisioning.api`` module.
"""
from unittest import mock

import ddt
from django.test import TestCase

from enterprise_access.apps.provisioning import api as provisioning_api
from test_utils import TEST_ENTERPRISE_UUID

TEST_USER_EMAILS = [
    'larry@stooges.com',
    'moe@stooges.com',
    'curly@stooges.com',
]


@ddt.ddt
class TestGetCreateCatalog(TestCase):
    """
    Tests for the ``get_or_create_enterprise_catalog()`` function.
    """

    @mock.patch.object(provisioning_api, 'LmsApiClient', autospec=True)
    def test_catalog_already_exists(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_client.get_enterprise_catalogs.return_value = [
            {'uuid': 'the-existing-catalog-uuid'},
        ]

        result = provisioning_api.get_or_create_enterprise_catalog(
            TEST_ENTERPRISE_UUID,
            'SOME TITLE',
            123,
        )
        self.assertEqual(
            result,
            {'uuid': 'the-existing-catalog-uuid'},
        )

        mock_client.get_enterprise_catalogs.assert_called_once_with(
            enterprise_customer_uuid=TEST_ENTERPRISE_UUID,
            catalog_query_id=123,
        )
        self.assertFalse(mock_client.create_enterprise_catalog.called)

    @mock.patch.object(provisioning_api, 'LmsApiClient', autospec=True)
    def test_catalog_is_created(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_client.get_enterprise_catalogs.return_value = []
        mock_client.create_enterprise_catalog.return_value = {
            'uuid': 'the-newly-created-catalog',
        }

        result = provisioning_api.get_or_create_enterprise_catalog(
            TEST_ENTERPRISE_UUID,
            'SOME TITLE',
            123,
        )

        self.assertEqual(
            result,
            {'uuid': 'the-newly-created-catalog'},
        )

        mock_client.get_enterprise_catalogs.assert_called_once_with(
            enterprise_customer_uuid=TEST_ENTERPRISE_UUID,
            catalog_query_id=123,
        )
        mock_client.create_enterprise_catalog.assert_called_once_with(
            enterprise_customer_uuid=TEST_ENTERPRISE_UUID,
            catalog_title='SOME TITLE',
            catalog_query_id=123,
        )

    @mock.patch.object(provisioning_api, 'LmsApiClient', autospec=True)
    def test_fetch_error_is_raised(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_client.get_enterprise_catalogs.side_effect = Exception('fetch error')

        with self.assertRaisesRegex(Exception, 'fetch error'):
            provisioning_api.get_or_create_enterprise_catalog(
                TEST_ENTERPRISE_UUID,
                'SOME TITLE',
                123,
            )

        mock_client.get_enterprise_catalogs.assert_called_once_with(
            enterprise_customer_uuid=TEST_ENTERPRISE_UUID,
            catalog_query_id=123,
        )
        self.assertFalse(mock_client.create_enterprise_catalog.called)

    @mock.patch.object(provisioning_api, 'LmsApiClient', autospec=True)
    def test_create_error_is_raised(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_client.get_enterprise_catalogs.return_value = []
        mock_client.create_enterprise_catalog.side_effect = Exception('create error')

        with self.assertRaisesRegex(Exception, 'create error'):
            provisioning_api.get_or_create_enterprise_catalog(
                TEST_ENTERPRISE_UUID,
                'SOME TITLE',
                123,
            )

        mock_client.get_enterprise_catalogs.assert_called_once_with(
            enterprise_customer_uuid=TEST_ENTERPRISE_UUID,
            catalog_query_id=123,
        )
        mock_client.create_enterprise_catalog.assert_called_once_with(
            enterprise_customer_uuid=TEST_ENTERPRISE_UUID,
            catalog_title='SOME TITLE',
            catalog_query_id=123,
        )


@ddt.ddt
class TestGetCreateAdmins(TestCase):
    """
    Tests for the ``get_or_create_enterprise_admin_users()`` function.
    """

    @mock.patch.object(provisioning_api, 'LmsApiClient', autospec=True)
    def test_some_admins_already_exist(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_client.get_enterprise_admin_users.return_value = [
            {'email': 'existing-admin@example.com'},
        ]
        mock_client.get_enterprise_pending_admin_users.return_value = [
            {'user_email': 'existing-pending-admin@example.com'},
        ]
        mock_client.create_enterprise_admin_user.side_effect = [
            {'user_email': 'new-admin-1@example.com'},
            {'user_email': 'new-admin-2@example.com'},
        ]

        requested_user_emails = [
            'existing-admin@example.com',
            'existing-pending-admin@example.com',
            'new-admin-1@example.com',
            'new-admin-2@example.com',
        ]

        result = provisioning_api.get_or_create_enterprise_admin_users(
            TEST_ENTERPRISE_UUID,
            requested_user_emails,
        )

        self.assertEqual(result, {
            'created_admins': [
                {'user_email': 'new-admin-1@example.com'},
                {'user_email': 'new-admin-2@example.com'},
            ],
            'existing_admins': [
                {'user_email': 'existing-admin@example.com'},
                {'user_email': 'existing-pending-admin@example.com'},
            ],
        })

        mock_client.get_enterprise_admin_users.assert_called_once_with(TEST_ENTERPRISE_UUID)
        mock_client.get_enterprise_pending_admin_users.assert_called_once_with(TEST_ENTERPRISE_UUID)
        mock_client.create_enterprise_admin_user.assert_has_calls([
            mock.call(TEST_ENTERPRISE_UUID, 'new-admin-1@example.com'),
            mock.call(TEST_ENTERPRISE_UUID, 'new-admin-2@example.com'),
        ], any_order=True)

    @mock.patch.object(provisioning_api, 'LmsApiClient', autospec=True)
    def test_all_admins_already_exist(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_client.get_enterprise_admin_users.return_value = [
            {'email': 'existing-admin@example.com'},
        ]
        mock_client.get_enterprise_pending_admin_users.return_value = [
            {'user_email': 'existing-pending-admin@example.com'},
        ]

        requested_user_emails = [
            'existing-admin@example.com',
            'existing-pending-admin@example.com',
        ]

        result = provisioning_api.get_or_create_enterprise_admin_users(
            TEST_ENTERPRISE_UUID,
            requested_user_emails,
        )

        self.assertEqual(result, {
            'created_admins': [],
            'existing_admins': [
                {'user_email': 'existing-admin@example.com'},
                {'user_email': 'existing-pending-admin@example.com'},
            ],
        })
        mock_client.get_enterprise_admin_users.assert_called_once_with(TEST_ENTERPRISE_UUID)
        mock_client.get_enterprise_pending_admin_users.assert_called_once_with(TEST_ENTERPRISE_UUID)
        self.assertFalse(mock_client.create_enterprise_admin_user.called)

    @mock.patch.object(provisioning_api, 'LmsApiClient', autospec=True)
    def test_get_admin_error_raised(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_client.get_enterprise_admin_users.side_effect = Exception('get admins error')

        requested_user_emails = ['existing-admin@example.com']
        with self.assertRaisesRegex(Exception, 'get admins error'):
            provisioning_api.get_or_create_enterprise_admin_users(
                TEST_ENTERPRISE_UUID,
                requested_user_emails,
            )

        mock_client.get_enterprise_admin_users.assert_called_once_with(TEST_ENTERPRISE_UUID)
        self.assertFalse(mock_client.get_enterprise_pending_admin_users.called)
        self.assertFalse(mock_client.create_enterprise_admin_user.called)

    @mock.patch.object(provisioning_api, 'LmsApiClient', autospec=True)
    def test_get_pending_admin_error_raised(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_client.get_enterprise_admin_users.return_value = []
        mock_client.get_enterprise_pending_admin_users.side_effect = Exception('get pending admins error')

        requested_user_emails = ['existing-admin@example.com']
        with self.assertRaisesRegex(Exception, 'get pending admins error'):
            provisioning_api.get_or_create_enterprise_admin_users(
                TEST_ENTERPRISE_UUID,
                requested_user_emails,
            )

        mock_client.get_enterprise_admin_users.assert_called_once_with(TEST_ENTERPRISE_UUID)
        mock_client.get_enterprise_pending_admin_users.assert_called_once_with(TEST_ENTERPRISE_UUID)
        self.assertFalse(mock_client.create_enterprise_admin_user.called)

    @mock.patch.object(provisioning_api, 'LmsApiClient', autospec=True)
    def test_create_admin_error_raised(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_client.get_enterprise_admin_users.return_value = []
        mock_client.get_enterprise_pending_admin_users.return_value = []
        mock_client.create_enterprise_admin_user.side_effect = Exception('create admin error')

        requested_user_emails = ['new-admin@example.com']
        with self.assertRaisesRegex(Exception, 'create admin error'):
            provisioning_api.get_or_create_enterprise_admin_users(
                TEST_ENTERPRISE_UUID,
                requested_user_emails,
            )

        mock_client.get_enterprise_admin_users.assert_called_once_with(TEST_ENTERPRISE_UUID)
        mock_client.get_enterprise_pending_admin_users.assert_called_once_with(TEST_ENTERPRISE_UUID)
        mock_client.create_enterprise_admin_user.assert_called_once_with(
            TEST_ENTERPRISE_UUID, 'new-admin@example.com'
        )


@ddt.ddt
class TestGetCreateCustomer(TestCase):
    """
    Tests for the ``get_or_create_enterprise_customer()`` function.
    """
    @mock.patch.object(provisioning_api, 'LmsApiClient', autospec=True)
    def test_customer_already_exists(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_client.get_enterprise_customer_data.return_value = {
            'name': 'Test Customer',
            'uuid': TEST_ENTERPRISE_UUID,
            'slug': 'test-customer',
            'country': 'AU',
        }

        result = provisioning_api.get_or_create_enterprise_customer(
            name='Test Customer',
            slug='test-customer',
            country='AU',
        )

        mock_client.get_enterprise_customer_data.assert_called_once_with(
            enterprise_customer_slug='test-customer',
        )
        self.assertFalse(mock_client.create_enterprise_customer.called)
        self.assertEqual({
            'name': 'Test Customer',
            'uuid': TEST_ENTERPRISE_UUID,
            'slug': 'test-customer',
            'country': 'AU',
        }, result)

    @mock.patch.object(provisioning_api, 'LmsApiClient', autospec=True)
    def test_customer_created(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_client.get_enterprise_customer_data.return_value = {}
        mock_client.create_enterprise_customer.return_value = {
            'name': 'Test Customer',
            'uuid': TEST_ENTERPRISE_UUID,
            'slug': 'test-customer',
            'country': 'AU',
        }

        result = provisioning_api.get_or_create_enterprise_customer(
            name='Test Customer',
            slug='test-customer',
            country='AU',
        )

        mock_client.get_enterprise_customer_data.assert_called_once_with(
            enterprise_customer_slug='test-customer',
        )
        mock_client.create_enterprise_customer.assert_called_once_with(
            name='Test Customer', slug='test-customer', country='AU',
        )
        self.assertEqual({
            'name': 'Test Customer',
            'uuid': TEST_ENTERPRISE_UUID,
            'slug': 'test-customer',
            'country': 'AU',
        }, result)

    @mock.patch.object(provisioning_api, 'LmsApiClient', autospec=True)
    def test_customer_creation_error(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_client.get_enterprise_customer_data.return_value = {}
        mock_client.create_enterprise_customer.side_effect = Exception('creation error')

        with self.assertRaisesRegex(Exception, 'creation error'):
            provisioning_api.get_or_create_enterprise_customer(
                name='Test Customer',
                slug='test-customer',
                country='AU',
            )

        mock_client.get_enterprise_customer_data.assert_called_once_with(
            enterprise_customer_slug='test-customer',
        )
        mock_client.create_enterprise_customer.assert_called_once_with(
            name='Test Customer', slug='test-customer', country='AU',
        )
