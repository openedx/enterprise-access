"""
Unit tests for the ``provisioning.api`` module.
"""
from unittest import mock

from django.test import TestCase

from enterprise_access.apps.provisioning import api as provisioning_api
from test_utils import TEST_ENTERPRISE_UUID

TEST_USER_EMAILS = [
    'larry@stooges.com',
    'moe@stooges.com',
    'curly@stooges.com',
]


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


class TestGetOrCreateCustomerAgreement(TestCase):
    """
    Tests for the get_or_create_customer_agreement() function.
    """
    @mock.patch.object(provisioning_api, 'LicenseManagerApiClient', autospec=True)
    def test_get_existing_customer_agreement(self, mock_license_manager_client):
        mock_client = mock_license_manager_client.return_value
        existing_agreement = {'uuid': 'existing-uuid', 'customer_slug': 'test-slug'}
        mock_client.get_customer_agreement.return_value = existing_agreement

        result = provisioning_api.get_or_create_customer_agreement(
            enterprise_customer_uuid='enterprise-uuid',
            customer_slug='test-slug',
        )

        self.assertEqual(result, existing_agreement)
        mock_client.get_customer_agreement.assert_called_once_with('enterprise-uuid')

    @mock.patch.object(provisioning_api, 'LicenseManagerApiClient', autospec=True)
    def test_create_new_customer_agreement(self, mock_license_manager_client):
        mock_client = mock_license_manager_client.return_value
        mock_client.get_customer_agreement.return_value = None

        created_agreement = {
            'uuid': 'new-uuid',
            'customer_slug': 'new-slug',
            'default_catalog_uuid': 'catalog-uuid'
        }
        mock_client.create_customer_agreement.return_value = created_agreement

        result = provisioning_api.get_or_create_customer_agreement(
            enterprise_customer_uuid='enterprise-uuid',
            customer_slug='new-slug',
            default_catalog_uuid='catalog-uuid',
            extra_field='extra-value'
        )

        self.assertEqual(result, created_agreement)
        mock_client.get_customer_agreement.assert_called_once_with('enterprise-uuid')
        mock_client.create_customer_agreement.assert_called_once_with(
            'enterprise-uuid',
            'new-slug',
            default_catalog_uuid='catalog-uuid',
            extra_field='extra-value'
        )


class TestGetOrCreateSubscriptionPlan(TestCase):
    """
    Tests for the get_or_create_subscription_plan() function.
    """
    @mock.patch.object(provisioning_api, 'LicenseManagerApiClient', autospec=True)
    def test_get_existing_subscription_plan(self, mock_license_manager_client):
        existing_subscriptions = [
            {
                'uuid': 'sub-uuid',
                'title': 'Test Plan',
                'salesforce_opportunity_line_item': 'opp-line-item',
            },
        ]

        result = provisioning_api.get_or_create_subscription_plan(
            customer_agreement_uuid=None,
            existing_subscription_list=existing_subscriptions,
            plan_title='Test Plan',
            catalog_uuid='catalog-uuid',
            opp_line_item='opp-line-item',
            start_date='2025-05-01',
            expiration_date='2026-05-01',
            desired_num_licenses=100,
            product_id=None,
        )

        self.assertEqual(result, existing_subscriptions[0])
        mock_license_manager_client.assert_not_called()

    @mock.patch.object(provisioning_api, 'LicenseManagerApiClient', autospec=True)
    def test_create_new_subscription_plan(self, mock_license_manager_client):
        created_subscription = {
            'uuid': 'new-sub-uuid',
            'salesforce_opportunity_line_item': 'opp-line-item',
            'title': 'New Plan',
        }
        mock_client = mock_license_manager_client.return_value
        mock_client.create_subscription_plan.return_value = created_subscription

        result = provisioning_api.get_or_create_subscription_plan(
            customer_agreement_uuid='agreement-uuid',
            existing_subscription_list=[],
            plan_title='New Plan',
            catalog_uuid='catalog-uuid',
            opp_line_item='opp-line-item',
            start_date='2025-05-01',
            expiration_date='2026-05-01',
            desired_num_licenses=50,
            extra_field='extra-value',
            product_id='the-product',
        )

        self.assertEqual(result, created_subscription)
        mock_client.create_subscription_plan.assert_called_once_with(
            customer_agreement_uuid='agreement-uuid',
            enterprise_catalog_uuid='catalog-uuid',
            title='New Plan',
            salesforce_opportunity_line_item='opp-line-item',
            start_date='2025-05-01',
            expiration_date='2026-05-01',
            desired_num_licenses=50,
            product_id='the-product',
            extra_field='extra-value'
        )

    @mock.patch.object(provisioning_api, 'LicenseManagerApiClient', autospec=True)
    def test_create_subscription_plan_exception(self, mock_license_manager_client):
        mock_client = mock_license_manager_client.return_value
        mock_client.create_subscription_plan.side_effect = Exception('API error')

        with self.assertRaises(Exception) as context:
            provisioning_api.get_or_create_subscription_plan(
                customer_agreement_uuid='agreement-uuid',
                existing_subscription_list=[],
                plan_title='New Plan',
                catalog_uuid='catalog-uuid',
                opp_line_item='opp-line-item',
                start_date='2025-05-01',
                expiration_date='2026-05-01',
                desired_num_licenses=50,
                product_id=None,
            )

        # Assertions
        self.assertEqual(str(context.exception), 'API error')
        mock_client.create_subscription_plan.assert_called_once_with(
            customer_agreement_uuid='agreement-uuid',
            enterprise_catalog_uuid='catalog-uuid',
            title='New Plan',
            salesforce_opportunity_line_item='opp-line-item',
            start_date='2025-05-01',
            expiration_date='2026-05-01',
            desired_num_licenses=50,
            product_id=None,
        )
