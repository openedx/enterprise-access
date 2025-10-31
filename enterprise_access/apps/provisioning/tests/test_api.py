"""
Unit tests for the ``provisioning.api`` module.
"""
from unittest import mock

import ddt
import requests
from django.conf import settings
from django.test import TestCase
from rest_framework import status

from enterprise_access.apps.api_client.exceptions import APIClientException
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
        mock_client.create_enterprise_admin_user.side_effect = [
            {'admin_user_id': 123},
            {'admin_user_id': 456},
        ]

        requested_user_emails = [
            'existing-admin@example.com',
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
            ],
        })

        mock_client.get_enterprise_admin_users.assert_called_once_with(TEST_ENTERPRISE_UUID)
        mock_client.create_enterprise_admin_user.assert_has_calls([
            mock.call(TEST_ENTERPRISE_UUID, 'new-admin-1@example.com'),
            mock.call(TEST_ENTERPRISE_UUID, 'new-admin-2@example.com'),
        ], any_order=True)

    @mock.patch.object(provisioning_api, 'LmsApiClient', autospec=True)
    def test_all_admins_already_exist(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_client.get_enterprise_admin_users.return_value = [
            {'email': 'existing-admin@example.com'},
            {'email': 'other-existing-admin@example.com'},
        ]

        requested_user_emails = [
            'existing-admin@example.com',
            'other-existing-admin@example.com',
        ]

        result = provisioning_api.get_or_create_enterprise_admin_users(
            TEST_ENTERPRISE_UUID,
            requested_user_emails,
        )

        self.assertEqual(result, {
            'created_admins': [],
            'existing_admins': [
                {'user_email': 'existing-admin@example.com'},
                {'user_email': 'other-existing-admin@example.com'},
            ],
        })
        mock_client.get_enterprise_admin_users.assert_called_once_with(TEST_ENTERPRISE_UUID)
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
    def test_create_admin_error_raised(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_client.get_enterprise_admin_users.return_value = []
        mock_client.create_enterprise_admin_user.side_effect = Exception('create admin error')

        requested_user_emails = ['new-admin@example.com']
        with self.assertRaisesRegex(Exception, 'create admin error'):
            provisioning_api.get_or_create_enterprise_admin_users(
                TEST_ENTERPRISE_UUID,
                requested_user_emails,
            )

        mock_client.get_enterprise_admin_users.assert_called_once_with(TEST_ENTERPRISE_UUID)
        mock_client.create_enterprise_admin_user.assert_called_once_with(
            TEST_ENTERPRISE_UUID, 'new-admin@example.com'
        )

    @mock.patch.object(provisioning_api, 'LmsApiClient', autospec=True)
    def test_create_admin_not_found_error_handled(self, mock_client_class):
        """
        Tests that we try to create a pending admin record if a 404 is returned
        when attempting to create the *concrete* admin record.
        """
        mock_client = mock_client_class.return_value
        mock_client.get_enterprise_admin_users.return_value = []

        def raise_api_client_exception(*args, **kwargs):
            exc = requests.exceptions.HTTPError('no user found')
            exc.response = mock.MagicMock(status_code=status.HTTP_404_NOT_FOUND)
            raise APIClientException('Failed to create admin user', exc) from exc

        mock_client.create_enterprise_admin_user.side_effect = raise_api_client_exception

        requested_user_emails = ['new-admin@example.com']
        provisioning_api.get_or_create_enterprise_admin_users(
            TEST_ENTERPRISE_UUID,
            requested_user_emails,
        )

        mock_client.get_enterprise_admin_users.assert_called_once_with(TEST_ENTERPRISE_UUID)
        mock_client.create_enterprise_admin_user.assert_called_once_with(
            TEST_ENTERPRISE_UUID, 'new-admin@example.com',
        )
        mock_client.create_enterprise_pending_admin_user.assert_called_once_with(
            TEST_ENTERPRISE_UUID, 'new-admin@example.com',
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


@ddt.ddt
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
                'product': 1,
            },
        ]

        result = provisioning_api.get_or_create_subscription_plan(
            customer_agreement_uuid='customer-agreement-uuid',
            existing_subscription_list=existing_subscriptions,
            plan_title='Test Plan',
            catalog_uuid='catalog-uuid',
            opp_line_item='opp-line-item',
            start_date='2025-05-01',
            expiration_date='2026-05-01',
            desired_num_licenses=100,
            product_id=1,
        )

        self.assertEqual(result, existing_subscriptions[0])
        mock_license_manager_client.assert_not_called()

    @ddt.data(
        ###################################
        # Base tests with non-null values #
        ###################################

        # Same opp_line_item, same product_id.
        # This should cause the existing subscription plan to be returned.
        {
            'existing_opp_line_item': 'opp-line-item-1',
            'existing_product_id': 1,
            'requesting_opp_line_item': 'opp-line-item-1',
            'requesting_product_id': 1,
            'created': False,
        },
        # Same opp_line_item, different product_id.
        # This should cause the existing subscription plan to be returned.
        {
            'existing_opp_line_item': 'opp-line-item-1',
            'existing_product_id': 1,
            'requesting_opp_line_item': 'opp-line-item-1',
            'requesting_product_id': 2,
            'created': False,
        },
        # Different opp_line_item, same product_id.
        # This should cause a new subscription plan to be created.
        {
            'existing_opp_line_item': 'opp-line-item-1',
            'existing_product_id': 1,
            'requesting_opp_line_item': 'opp-line-item-2',
            'requesting_product_id': 1,
            'created': True,
        },
        # Different opp_line_item, different product_id.
        # This should cause a new subscription plan to be created.
        {
            'existing_opp_line_item': 'opp-line-item-1',
            'existing_product_id': 1,
            'requesting_opp_line_item': 'opp-line-item-2',
            'requesting_product_id': 2,
            'created': True,
        },

        ############################################################################
        # Make sure requesting product_id=None falls back to a configured default. #
        ############################################################################
        {
            'existing_opp_line_item': 'opp-line-item-1',
            'existing_product_id': 999,
            'requesting_opp_line_item': 'opp-line-item-2',
            'requesting_product_id': None,  # fallback to settings.PROVISIONING_DEFAULTS['subscription']['product_id']
            'created': True,
        },

        ###########################################################################
        # Make sure requesting opp_line_item=None is treated as a distinct value. #
        ###########################################################################

        # None == None should result in the existing subscription plan to be returned.
        {
            'existing_opp_line_item': None,
            'existing_product_id': 1,
            'requesting_opp_line_item': None,
            'requesting_product_id': 1,
            'created': False,
        },
        # non-None != None should result in creation.
        {
            'existing_opp_line_item': 'opp-line-item-1',
            'existing_product_id': 1,
            'requesting_opp_line_item': None,
            'requesting_product_id': 1,
            'created': True,
        },
        # None != non-None should result in creation.
        {
            'existing_opp_line_item': None,
            'existing_product_id': 1,
            'requesting_opp_line_item': 'opp-line-item-1',
            'requesting_product_id': 1,
            'created': True,
        },
    )
    @ddt.unpack
    @mock.patch.object(provisioning_api, 'LicenseManagerApiClient', autospec=True)
    def test_create_new_subscription_plan(
        self,
        mock_license_manager_client,
        existing_opp_line_item,
        existing_product_id,
        requesting_opp_line_item,
        requesting_product_id,
        created,
    ):
        existing_subscription = {
            'uuid': 'sub-uuid',
            'title': 'Test Plan',
            'salesforce_opportunity_line_item': existing_opp_line_item,
            'product': existing_product_id,
            'start_date': '2026-01-01T00:00Z',
        }
        created_subscription = {
            'uuid': 'new-sub-uuid',
            'salesforce_opportunity_line_item': requesting_opp_line_item,
            'title': 'New Plan',
            # Simulate the fallback logic within LicenseManagerApiClient.create_subscription_plan().
            'product': requesting_product_id or settings.PROVISIONING_DEFAULTS['subscription']['product_id'],
            'start_date': '2026-01-01T00:00Z',
        }
        mock_client = mock_license_manager_client.return_value
        mock_client.create_subscription_plan.return_value = created_subscription

        result = provisioning_api.get_or_create_subscription_plan(
            customer_agreement_uuid='agreement-uuid',
            existing_subscription_list=[existing_subscription],
            plan_title='New Plan',
            catalog_uuid='catalog-uuid',
            opp_line_item=requesting_opp_line_item,
            start_date='2025-05-01',
            expiration_date='2026-05-01',
            desired_num_licenses=50,
            extra_field='extra-value',
            product_id=requesting_product_id,
        )

        self.assertEqual(result, created_subscription if created else existing_subscription)
        if created:
            mock_client.create_subscription_plan.assert_called_once_with(
                customer_agreement_uuid='agreement-uuid',
                enterprise_catalog_uuid='catalog-uuid',
                title='New Plan',
                salesforce_opportunity_line_item=requesting_opp_line_item,
                start_date='2025-05-01',
                expiration_date='2026-05-01',
                desired_num_licenses=50,
                product_id=requesting_product_id,
                extra_field='extra-value'
            )
        else:
            mock_client.create_subscription_plan.assert_not_called()

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
