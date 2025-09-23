"""
Tests for the provisioning views.
"""
import random
import uuid
from datetime import timedelta
from unittest import mock

import ddt
from django.contrib.auth import get_user_model
from django.utils import timezone
from edx_rbac.constants import ALL_ACCESS_CONTEXT
from rest_framework import status
from rest_framework.reverse import reverse

from enterprise_access.apps.core.constants import (
    SYSTEM_ENTERPRISE_ADMIN_ROLE,
    SYSTEM_ENTERPRISE_LEARNER_ROLE,
    SYSTEM_ENTERPRISE_OPERATOR_ROLE,
    SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE
)
from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.constants import CheckoutIntentState
from enterprise_access.apps.customer_billing.models import CheckoutIntent
from enterprise_access.apps.provisioning.models import (
    GetCreateCustomerStep,
    GetCreateEnterpriseAdminUsersStep,
    GetCreateSubscriptionPlanStep,
    ProvisionNewCustomerWorkflow
)
from test_utils import APITest

User = get_user_model()

PROVISIONING_CREATE_ENDPOINT = reverse('api:v1:provisioning-create')

TEST_ENTERPRISE_UUID = uuid.uuid4()

TEST_CATALOG_UUID = uuid.uuid4()

TEST_AGREEMENT_UUID = uuid.uuid4()

TEST_SUBSCRIPTION_UUID = uuid.uuid4()

DEFAULT_CUSTOMER_RECORD = {
    "uuid": str(TEST_ENTERPRISE_UUID),
    "name": "Test customer",
    "country": "US",
    "slug": "test-customer",
}

DEFAULT_CATALOG_RECORD = {
    'uuid': str(TEST_CATALOG_UUID),
    'enterprise_customer': str(TEST_ENTERPRISE_UUID),
    'title': 'Test catalog',
    'enterprise_catalog_query': 2,
}

DEFAULT_SUBSCRIPTION_PLAN_RECORD = {
    "uuid": str(TEST_SUBSCRIPTION_UUID),
    "title": "provisioning test 1",
    "salesforce_opportunity_line_item": "00k000000000000123",
    "created": "2025-05-16T15:20:19.159640+00:00",
    "start_date": "2025-06-01T00:00:00+00:00",
    "expiration_date": "2026-03-31T00:00:00+00:00",
    "is_active": True,
    "is_current": False,
    "plan_type": "Standard Paid",
    "enterprise_catalog_uuid": str(TEST_CATALOG_UUID),
}

DEFAULT_AGREEMENT_RECORD = {
    "uuid": str(TEST_AGREEMENT_UUID),
    "enterprise_customer_uuid": str(TEST_ENTERPRISE_UUID),
    "default_catalog_uuid": None,
    "subscriptions": [DEFAULT_SUBSCRIPTION_PLAN_RECORD],
}

DEFAULT_REQUEST_PAYLOAD = {
    'enterprise_customer': {
        'name': 'Test customer',
        'slug': 'test-customer',
        'country': 'US',
    },
    'pending_admins': [],
    'enterprise_catalog': {
        'title': 'Test catalog',
        'catalog_query_id': 2,
    },
    'customer_agreement': {},
    'subscription_plan': {
        'title': 'provisioning test 1',
        'salesforce_opportunity_line_item': '00k000000000000123',
        'start_date': '2025-06-01T00:00:00Z',
        'expiration_date': '2026-03-31T00:00:00Z',
        'product_id': 1,
        'desired_num_licenses': 5,
    },
}

EXPECTED_CATALOG_RESPONSE = {
    'uuid': str(TEST_CATALOG_UUID),
    'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID),
    'title': 'Test catalog',
    'catalog_query_id': 2,
}


@ddt.ddt
class TestProvisioningAuth(APITest):
    """
    Tests Authentication and Permission checking for provisioning.
    """
    def tearDown(self):
        super().tearDown()
        GetCreateCustomerStep.objects.all().delete()
        GetCreateEnterpriseAdminUsersStep.objects.all().delete()
        ProvisionNewCustomerWorkflow.objects.all().delete()

    @ddt.data(
        # A role that's not mapped to any feature perms will get you a 403.
        (
            {'system_wide_role': 'some-other-role', 'context': str(TEST_ENTERPRISE_UUID)},
            status.HTTP_403_FORBIDDEN,
        ),
        # A good learner role, AND in the correct context/customer STILL gets you a 403.
        # Provisioning APIs are inaccessible to all learners.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': ALL_ACCESS_CONTEXT},
            status.HTTP_403_FORBIDDEN,
        ),
        # An admin role is not authorized to provision.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': ALL_ACCESS_CONTEXT},
            status.HTTP_403_FORBIDDEN,
        ),
        # No JWT based auth, no soup for you.
        (
            None,
            status.HTTP_401_UNAUTHORIZED,
        ),
    )
    @ddt.unpack
    def test_provisioning_create_view_forbidden(self, role_context_dict, expected_response_code):
        """
        Tests that we get expected 40x responses for the provisioning create view..
        """
        # Set the JWT-based auth that we'll use for every request
        if role_context_dict:
            self.set_jwt_cookie([role_context_dict])

        response = self.client.post(PROVISIONING_CREATE_ENDPOINT)
        assert response.status_code == expected_response_code

    @ddt.data(
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': ALL_ACCESS_CONTEXT},
            status.HTTP_201_CREATED,
        ),
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE, 'context': ALL_ACCESS_CONTEXT},
            status.HTTP_201_CREATED,
        ),
    )
    @ddt.unpack
    @mock.patch('enterprise_access.apps.provisioning.models.get_or_create_customer_agreement')
    @mock.patch('enterprise_access.apps.provisioning.models.get_or_create_enterprise_catalog')
    @mock.patch('enterprise_access.apps.provisioning.models.get_or_create_enterprise_admin_users')
    @mock.patch('enterprise_access.apps.provisioning.models.get_or_create_enterprise_customer')
    def test_provisioning_create_allowed_for_provisioning_admins(
        self, role_context_dict, expected_response_code, mock_create_customer,
        mock_create_admins, mock_create_catalog, mock_create_agreement,
    ):
        """
        Tests that we get expected 200 response for the provisioning create view when
        the requesting user has the correct system role and provides a valid request payload.
        """
        self.set_jwt_cookie([role_context_dict])

        mock_create_customer.return_value = DEFAULT_CUSTOMER_RECORD
        mock_create_admins.return_value = {
            "created_admins": [{
                "user_email": "test-admin@example.com",
            }],
            "existing_admins": [],
        }
        mock_create_catalog.return_value = DEFAULT_CATALOG_RECORD
        mock_create_agreement.return_value = DEFAULT_AGREEMENT_RECORD

        request_payload = {**DEFAULT_REQUEST_PAYLOAD}
        request_payload['pending_admins'] = [
            {
                "user_email": "test-admin@example.com",
            },
        ]

        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=request_payload)
        assert response.status_code == expected_response_code

        mock_create_customer.assert_called_once_with(
            **request_payload['enterprise_customer'],
        )

        created_customer = mock_create_customer.return_value
        mock_create_admins.assert_called_once_with(
            enterprise_customer_uuid=created_customer['uuid'],
            user_emails=['test-admin@example.com'],
        )


@ddt.ddt
class TestProvisioningEndToEnd(APITest):
    """
    Tests end-to-end calls to provisioning endpoints through mocked-out calls
    to downstream services.
    """
    def setUp(self):
        super().setUp()
        self.set_jwt_cookie([
            {
                'system_wide_role': SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE,
                'context': ALL_ACCESS_CONTEXT,
            },
        ])

    @ddt.data(
        # Data representing the state where a net-new customer is created.
        {
            'existing_customer_data': None,
            'created_customer_data': DEFAULT_CUSTOMER_RECORD,
            'expected_get_customer_kwargs': {
                'enterprise_customer_slug': 'test-customer',
            },
            'create_customer_called': True,
            'expected_create_customer_kwargs': {
                'name': DEFAULT_CUSTOMER_RECORD['name'],
                'slug': DEFAULT_CUSTOMER_RECORD['slug'],
                'country': DEFAULT_CUSTOMER_RECORD['country'],
            },
        },
        # Data representing the state where a customer with the given slug exists.
        {
            'existing_customer_data': DEFAULT_CUSTOMER_RECORD,
            'created_customer_data': None,
            'expected_get_customer_kwargs': {
                'enterprise_customer_slug': 'test-customer',
            },
            'create_customer_called': False,
            'expected_create_customer_kwargs': None
        },
    )
    @mock.patch('enterprise_access.apps.provisioning.models.get_or_create_customer_agreement')
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_get_or_create_customer_and_admins_created(self, test_data, mock_lms_api_client, mock_create_agreement):
        """
        Tests cases where admins don't exist and customer is fetched or created.
        """
        mock_client = mock_lms_api_client.return_value
        mock_client.get_enterprise_customer_data.return_value = test_data['existing_customer_data']
        mock_client.get_enterprise_admin_users.return_value = []
        mock_client.get_enterprise_pending_admin_users.return_value = []

        if test_data['created_customer_data']:
            mock_client.create_enterprise_customer.return_value = test_data['created_customer_data']

        mock_client.create_enterprise_admin_user.side_effect = [
            {'user_email': 'alice@foo.com', 'enterprise_customer_uuid': TEST_ENTERPRISE_UUID},
            {'user_email': 'bob@foo.com', 'enterprise_customer_uuid': TEST_ENTERPRISE_UUID},
        ]
        mock_client.get_enterprise_catalogs.return_value = [DEFAULT_CATALOG_RECORD]
        mock_create_agreement.return_value = DEFAULT_AGREEMENT_RECORD

        request_payload = {**DEFAULT_REQUEST_PAYLOAD}
        request_payload['pending_admins'] = [
            {'user_email': 'alice@foo.com'},
            {'user_email': 'bob@foo.com'},
        ]
        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=request_payload)
        assert response.status_code == status.HTTP_201_CREATED

        actual_response_payload = response.json()
        self.assertEqual(
            actual_response_payload['enterprise_customer'],
            DEFAULT_CUSTOMER_RECORD,
        )
        self.assertEqual(
            actual_response_payload['customer_admins'],
            {
                'created_admins': [
                    {'user_email': 'alice@foo.com'},
                    {'user_email': 'bob@foo.com'},
                ],
                'existing_admins': [],
            },
        )

        mock_client.get_enterprise_customer_data.assert_called_once_with(
            **test_data['expected_get_customer_kwargs'],
        )
        if test_data['create_customer_called']:
            mock_client.create_enterprise_customer.assert_called_once_with(
                **test_data['expected_create_customer_kwargs'],
            )
        else:
            self.assertFalse(mock_client.create_enterprise_customer.called)

        mock_client.get_enterprise_admin_users.assert_called_once_with(str(TEST_ENTERPRISE_UUID))
        mock_client.get_enterprise_pending_admin_users.assert_called_once_with(str(TEST_ENTERPRISE_UUID))
        mock_client.create_enterprise_admin_user.assert_has_calls([
            mock.call(str(TEST_ENTERPRISE_UUID), 'alice@foo.com'),
            mock.call(str(TEST_ENTERPRISE_UUID), 'bob@foo.com'),
        ], any_order=True)

        # Assertions about workflow record count and state
        self.assertEqual(ProvisionNewCustomerWorkflow.objects.count(), 1)
        self.assertEqual(GetCreateCustomerStep.objects.count(), 1)
        self.assertEqual(GetCreateEnterpriseAdminUsersStep.objects.count(), 1)
        workflow_record = ProvisionNewCustomerWorkflow.objects.first()
        self.assertEqual(
            workflow_record.output_data['create_customer_output']['uuid'],
            str(TEST_ENTERPRISE_UUID),
        )
        self.assertEqual(
            workflow_record.output_data['create_enterprise_admin_users_output']['enterprise_customer_uuid'],
            str(TEST_ENTERPRISE_UUID),
        )
        self.assertEqual(
            workflow_record.output_data['create_enterprise_admin_users_output']['existing_admins'],
            [],
        )
        self.assertCountEqual(
            workflow_record.output_data['create_enterprise_admin_users_output']['created_admins'],
            [{'user_email': 'alice@foo.com'}, {'user_email': 'bob@foo.com'}],
        )

    @ddt.data(
        # No admin users exist, two pending admins created.
        {
            'existing_admin_users': [],
            'existing_pending_admin_users': [],
            'create_pending_admins_called': True,
            'create_admin_user_side_effect': [
                {'user_email': 'alice@foo.com', 'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)},
                {'user_email': 'bob@foo.com', 'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)},
            ],
            'expected_create_pending_admin_calls': [
                mock.call(str(TEST_ENTERPRISE_UUID), 'alice@foo.com'),
                mock.call(str(TEST_ENTERPRISE_UUID), 'bob@foo.com'),
            ],
        },
        # One pending admin exists, one new one created.
        {
            'existing_admin_users': [],
            'existing_pending_admin_users': [
                {'user_email': 'alice@foo.com', 'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)},
            ],
            'create_pending_admins_called': True,
            'create_admin_user_side_effect': [
                {'user_email': 'bob@foo.com', 'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)},
            ],
            'expected_create_pending_admin_calls': [
                mock.call(str(TEST_ENTERPRISE_UUID), 'bob@foo.com'),
            ],
        },
        # One full admin exists, one new pending admin created.
        {
            'existing_admin_users': [
                {'email': 'alice@foo.com', 'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)},
            ],
            'existing_pending_admin_users': [],
            'create_pending_admins_called': True,
            'create_admin_user_side_effect': [
                {'user_email': 'bob@foo.com', 'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)},
            ],
            'expected_create_pending_admin_calls': [
                mock.call(str(TEST_ENTERPRISE_UUID), 'bob@foo.com'),
            ],
        },
        # One full admin exists, one pending exists, none created.
        {
            'existing_admin_users': [
                {'email': 'alice@foo.com', 'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)},
            ],
            'existing_pending_admin_users': [
                {'user_email': 'bob@foo.com', 'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)},
            ],
            'create_pending_admins_called': False,
            'create_admin_user_side_effect': [],
            'expected_create_pending_admin_calls': [],
        },
    )
    @mock.patch('enterprise_access.apps.provisioning.api.LicenseManagerApiClient')
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_customer_fetched_admins_fetched_or_created(
        self, test_data, mock_lms_api_client, mock_license_manager_client
    ):
        """
        Tests cases where [pending]admins are fetched or created, but the customer
        already exists
        """
        mock_client = mock_lms_api_client.return_value
        mock_client.get_enterprise_customer_data.return_value = DEFAULT_CUSTOMER_RECORD
        mock_client.get_enterprise_admin_users.return_value = test_data['existing_admin_users']
        mock_client.get_enterprise_pending_admin_users.return_value = test_data['existing_pending_admin_users']
        mock_client.create_enterprise_admin_user.side_effect = test_data['create_admin_user_side_effect']
        mock_client.get_enterprise_catalogs.return_value = [DEFAULT_CATALOG_RECORD]
        mock_license_client = mock_license_manager_client.return_value
        mock_license_client.get_customer_agreement.return_value = DEFAULT_AGREEMENT_RECORD

        request_payload = {**DEFAULT_REQUEST_PAYLOAD}
        request_payload['pending_admins'] = [
            {'user_email': 'alice@foo.com'},
            {'user_email': 'bob@foo.com'},
        ]
        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=request_payload)
        assert response.status_code == status.HTTP_201_CREATED

        existing_emails = sorted(
            [record['email'] for record in test_data['existing_admin_users']] +
            [record['user_email'] for record in test_data['existing_pending_admin_users']]
        )
        expected_existing_admins = [{'user_email': email} for email in existing_emails]
        expected_created_admins = [
            {'user_email': record['user_email']}
            for record in test_data['create_admin_user_side_effect']
        ]

        actual_response_payload = response.json()
        self.assertEqual(
            actual_response_payload['customer_admins'],
            {
                'created_admins': expected_created_admins,
                'existing_admins': expected_existing_admins,
            },
        )

        mock_client.get_enterprise_customer_data.assert_called_once_with(
            enterprise_customer_slug='test-customer',
        )
        self.assertFalse(mock_client.create_enterprise_customer.called)

        mock_client.get_enterprise_admin_users.assert_called_once_with(str(TEST_ENTERPRISE_UUID))
        mock_client.get_enterprise_pending_admin_users.assert_called_once_with(str(TEST_ENTERPRISE_UUID))
        if test_data['create_pending_admins_called']:
            mock_client.create_enterprise_admin_user.assert_has_calls(
                test_data['expected_create_pending_admin_calls'],
                any_order=True,
            )
        else:
            self.assertFalse(mock_client.create_enterprise_admin_user.called)

        # Assertions about workflow record count and state
        self.assertEqual(ProvisionNewCustomerWorkflow.objects.count(), 1)
        self.assertEqual(GetCreateCustomerStep.objects.count(), 1)
        self.assertEqual(GetCreateEnterpriseAdminUsersStep.objects.count(), 1)
        workflow_record = ProvisionNewCustomerWorkflow.objects.first()
        self.assertEqual(
            workflow_record.output_data['create_customer_output']['uuid'],
            str(TEST_ENTERPRISE_UUID),
        )
        self.assertEqual(
            workflow_record.output_data['create_enterprise_admin_users_output']['enterprise_customer_uuid'],
            str(TEST_ENTERPRISE_UUID),
        )
        self.assertCountEqual(
            workflow_record.output_data['create_enterprise_admin_users_output']['existing_admins'],
            expected_existing_admins,
        )
        self.assertCountEqual(
            workflow_record.output_data['create_enterprise_admin_users_output']['created_admins'],
            expected_created_admins,
        )

    @ddt.data(
        {
            'existing_catalogs': [],
            'catalog_to_create': DEFAULT_CATALOG_RECORD,
        },
        {
            'existing_catalogs': [DEFAULT_CATALOG_RECORD],
            'catalog_to_create': {},
        },
    )
    @mock.patch('enterprise_access.apps.provisioning.models.get_or_create_customer_agreement')
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_catalog_fetched_or_created(self, test_data, mock_lms_api_client, mock_create_agreement):
        """
        Tests cases where the customer exists, no admins are needed, and we
        either fetch or create a catalog record
        """
        mock_client = mock_lms_api_client.return_value
        mock_client.get_enterprise_customer_data.return_value = DEFAULT_CUSTOMER_RECORD
        mock_client.get_enterprise_admin_users.return_value = []
        mock_client.get_enterprise_pending_admin_users.return_value = []
        mock_client.get_enterprise_catalogs.return_value = test_data['existing_catalogs']
        if test_data['catalog_to_create']:
            mock_client.create_enterprise_catalog.return_value = test_data['catalog_to_create']

        mock_create_agreement.return_value = DEFAULT_AGREEMENT_RECORD

        request_payload = {**DEFAULT_REQUEST_PAYLOAD}
        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=request_payload)

        assert response.status_code == status.HTTP_201_CREATED
        actual_response_payload = response.json()
        self.assertEqual(
            actual_response_payload['enterprise_catalog'],
            EXPECTED_CATALOG_RESPONSE,
        )

        mock_client.get_enterprise_catalogs.assert_called_once_with(
            enterprise_customer_uuid=str(TEST_ENTERPRISE_UUID),
            catalog_query_id=2,
        )
        if test_data['catalog_to_create']:
            mock_client.create_enterprise_catalog.assert_called_once_with(
                enterprise_customer_uuid=str(TEST_ENTERPRISE_UUID),
                catalog_title='Test catalog',
                catalog_query_id=2,
            )
        else:
            self.assertFalse(mock_client.create_enterprise_catalog.called)

    @mock.patch('enterprise_access.apps.provisioning.models.get_or_create_customer_agreement')
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_catalog_created_with_generated_title_and_inferred_query_id(
        self, mock_lms_api_client, mock_create_agreement
    ):
        """
        Tests the case where no enterprise_catalog is provided in the request payload.
        The catalog title should be generated from the customer name, and the
        catalog_query_id should be inferred from the subscription plan's product_id.
        """
        # Setup mocks
        mock_client = mock_lms_api_client.return_value
        mock_client.get_enterprise_customer_data.return_value = DEFAULT_CUSTOMER_RECORD
        mock_client.get_enterprise_admin_users.return_value = []
        mock_client.get_enterprise_pending_admin_users.return_value = []
        mock_client.get_enterprise_catalogs.return_value = []  # No existing catalogs

        # Expected catalog record with generated title and inferred query_id
        expected_created_catalog = {
            'uuid': str(TEST_CATALOG_UUID),
            'enterprise_customer': str(TEST_ENTERPRISE_UUID),
            'title': 'Test customer Subscription Catalog',  # Generated from customer name
            'enterprise_catalog_query': 42,   # Inferred from product_id 1 mapping, see settings/test.py
        }
        mock_client.create_enterprise_catalog.return_value = expected_created_catalog

        mock_create_agreement.return_value = DEFAULT_AGREEMENT_RECORD

        # Create request payload WITHOUT enterprise_catalog section
        request_payload = {**DEFAULT_REQUEST_PAYLOAD}
        request_payload.pop('enterprise_catalog')

        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=request_payload)

        # Assertions
        assert response.status_code == status.HTTP_201_CREATED
        actual_response_payload = response.json()

        expected_catalog_response = {
            'uuid': str(TEST_CATALOG_UUID),
            'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID),
            'title': 'Test customer Subscription Catalog',
            'catalog_query_id': 42,
        }

        self.assertEqual(
            actual_response_payload['enterprise_catalog'],
            expected_catalog_response,
        )
        mock_client.get_enterprise_catalogs.assert_called_once_with(
            enterprise_customer_uuid=str(TEST_ENTERPRISE_UUID),
            catalog_query_id=42,  # Should use the inferred catalog_query_id
        )
        mock_client.create_enterprise_catalog.assert_called_once_with(
            enterprise_customer_uuid=str(TEST_ENTERPRISE_UUID),
            catalog_title='Test customer Subscription Catalog',  # Should use generated title
            catalog_query_id=42,  # Should use the inferred catalog_query_id
        )

    @ddt.data(
        # Case: No agreement exists, must create
        {
            'existing_agreement': None,
            'created_agreement': DEFAULT_AGREEMENT_RECORD,
        },
        # Case: Agreement already exists
        {
            'existing_agreement': DEFAULT_AGREEMENT_RECORD,
            'created_agreement': None,
        },
    )
    @mock.patch('enterprise_access.apps.provisioning.api.LicenseManagerApiClient')
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_customer_agreement_fetched_or_created(
        self, test_data, mock_lms_api_client, mock_license_manager_client
    ):
        # Mock customer and catalog step as in existing test
        mock_lms_client = mock_lms_api_client.return_value
        mock_lms_client.get_enterprise_customer_data.return_value = DEFAULT_CUSTOMER_RECORD
        mock_lms_client.get_enterprise_admin_users.return_value = []
        mock_lms_client.get_enterprise_pending_admin_users.return_value = []
        mock_lms_client.get_enterprise_catalogs.return_value = [DEFAULT_CATALOG_RECORD]
        # Customer Agreement API mocks
        mock_license_client = mock_license_manager_client.return_value
        mock_license_client.get_customer_agreement.return_value = test_data['existing_agreement']

        if test_data['created_agreement']:
            mock_license_client.create_customer_agreement.return_value = test_data['created_agreement']
            mock_license_client.create_subscription_plan.return_value = DEFAULT_SUBSCRIPTION_PLAN_RECORD

        request_payload = {**DEFAULT_REQUEST_PAYLOAD}
        if test_data['created_agreement']:
            request_payload['customer_agreement'] = {
                'default_catalog_uuid': str(TEST_CATALOG_UUID),
            }

        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=request_payload)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Response JSON must include the created or fetched agreement
        actual_response_payload = response.json()
        self.assertEqual(
            actual_response_payload['customer_agreement'],
            DEFAULT_AGREEMENT_RECORD,
        )

        # Workflow record/step assertions
        workflow = ProvisionNewCustomerWorkflow.objects.all()[0]
        self.assertIsNotNone(workflow.get_create_customer_agreement_step())

        # API call assertions
        mock_license_client.get_customer_agreement.assert_called_once_with(str(TEST_ENTERPRISE_UUID))
        if test_data['created_agreement']:
            mock_license_client.create_customer_agreement.assert_called_once_with(
                str(TEST_ENTERPRISE_UUID),
                'test-customer',
                default_catalog_uuid=str(TEST_CATALOG_UUID),
            )
        else:
            self.assertFalse(mock_license_client.create_customer_agreement.called)

    @mock.patch('enterprise_access.apps.provisioning.api.LicenseManagerApiClient')
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_new_subscription_plan_created(self, mock_lms_api_client, mock_license_manager_client):
        # Setup mocks for prior workflow steps
        mock_lms_client = mock_lms_api_client.return_value
        mock_lms_client.get_enterprise_customer_data.return_value = DEFAULT_CUSTOMER_RECORD
        mock_lms_client.get_enterprise_admin_users.return_value = []
        mock_lms_client.get_enterprise_pending_admin_users.return_value = []
        mock_lms_client.get_enterprise_catalogs.return_value = [DEFAULT_CATALOG_RECORD]

        # Agreement and subscription plan creation
        mock_license_client = mock_license_manager_client.return_value
        mock_license_client.get_customer_agreement.return_value = None
        mock_license_client.create_customer_agreement.return_value = {
            **DEFAULT_AGREEMENT_RECORD, "subscriptions": []
        }
        mock_license_client.create_subscription_plan.return_value = DEFAULT_SUBSCRIPTION_PLAN_RECORD

        # Make the provisioning request
        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=DEFAULT_REQUEST_PAYLOAD)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        actual_response = response.json()
        # The subscription_plan in the response should match what the license manager returned
        self.assertIn('subscription_plan', actual_response)
        self.assertEqual(
            actual_response['subscription_plan']['uuid'],
            DEFAULT_SUBSCRIPTION_PLAN_RECORD['uuid'],
        )
        self.assertEqual(
            actual_response['subscription_plan']['title'],
            DEFAULT_SUBSCRIPTION_PLAN_RECORD['title'],
        )
        self.assertEqual(
            actual_response['subscription_plan']['salesforce_opportunity_line_item'],
            DEFAULT_SUBSCRIPTION_PLAN_RECORD['salesforce_opportunity_line_item'],
        )
        self.assertTrue(actual_response['subscription_plan']['is_active'])

        # Workflow record/step assertions
        workflow = ProvisionNewCustomerWorkflow.objects.all()[0]
        self.assertIsNotNone(workflow.get_create_subscription_plan_step())

        # LicenseManagerApiClient should be called to create agreement and subscription plan
        mock_license_client.get_customer_agreement.assert_called_once_with(
            str(TEST_ENTERPRISE_UUID)
        )
        mock_license_client.create_customer_agreement.assert_called_once_with(
            str(TEST_ENTERPRISE_UUID),
            'test-customer',
            default_catalog_uuid=None,
        )
        mock_license_client.create_subscription_plan.assert_called_once_with(
            customer_agreement_uuid=str(TEST_AGREEMENT_UUID),
            title='provisioning test 1',
            salesforce_opportunity_line_item='00k000000000000123',
            start_date='2025-06-01T00:00:00+00:00',
            expiration_date='2026-03-31T00:00:00+00:00',
            desired_num_licenses=5,
            enterprise_catalog_uuid=str(TEST_CATALOG_UUID),
            product_id=1,
        )


@ddt.ddt
class TestCheckoutIntentSynchronization(APITest):
    """
    Tests for checkout intent synchronization during provisioning workflow.
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.enterprise_slug = f'test-checkout-enterprise-{random.randint(1, 10000)}'
        self.set_jwt_cookie([
            {
                'system_wide_role': SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE,
                'context': ALL_ACCESS_CONTEXT,
            },
        ])

    def tearDown(self):
        super().tearDown()

        CheckoutIntent.objects.all().delete()
        GetCreateCustomerStep.objects.all().delete()
        GetCreateEnterpriseAdminUsersStep.objects.all().delete()
        GetCreateSubscriptionPlanStep.objects.all().delete()
        ProvisionNewCustomerWorkflow.objects.all().delete()
        User.objects.filter(email__endswith='@test-factory.com').delete()

    def _create_checkout_intent(self, state=CheckoutIntentState.PAID, enterprise_slug=None):
        """Helper to create a checkout intent for testing."""
        return CheckoutIntent.objects.create(
            user=self.user,
            enterprise_slug=enterprise_slug or self.enterprise_slug,
            enterprise_name='Test Enterprise',
            quantity=10,
            state=state,
            expires_at=timezone.now() + timedelta(hours=1),
        )

    def _get_base_request_payload(self):
        """Helper to get base request payload with test enterprise slug."""
        payload = {**DEFAULT_REQUEST_PAYLOAD}
        payload['enterprise_customer']['slug'] = self.enterprise_slug
        return payload

    @ddt.data(*CheckoutIntent.FULFILLABLE_STATES)
    @mock.patch('enterprise_access.apps.provisioning.api.LicenseManagerApiClient')
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_checkout_intent_synchronized_on_success(
        self, intent_state, mock_lms_api_client, mock_license_manager_client,
    ):
        """
        Test that a fulfillable checkout intent is linked to workflow and marked as FULFILLED on success.
        """
        checkout_intent = self._create_checkout_intent(state=intent_state)
        self.assertEqual(checkout_intent.state, intent_state)
        self.assertIsNone(checkout_intent.workflow)

        # Setup mocks for successful provisioning
        mock_lms_client = mock_lms_api_client.return_value
        mock_lms_client.get_enterprise_customer_data.return_value = {
            **DEFAULT_CUSTOMER_RECORD,
            'slug': self.enterprise_slug,
        }
        mock_lms_client.get_enterprise_admin_users.return_value = []
        mock_lms_client.get_enterprise_pending_admin_users.return_value = []
        mock_lms_client.get_enterprise_catalogs.return_value = [DEFAULT_CATALOG_RECORD]

        mock_license_client = mock_license_manager_client.return_value
        mock_license_client.get_customer_agreement.return_value = None
        mock_license_client.create_customer_agreement.return_value = {
            **DEFAULT_AGREEMENT_RECORD, "subscriptions": []
        }
        mock_license_client.create_subscription_plan.return_value = DEFAULT_SUBSCRIPTION_PLAN_RECORD

        # Make provisioning request
        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=self._get_base_request_payload())
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Refresh checkout intent and verify it was synchronized
        checkout_intent.refresh_from_db()
        self.assertEqual(checkout_intent.state, CheckoutIntentState.FULFILLED)
        self.assertIsNotNone(checkout_intent.workflow)
        self.assertIsNone(checkout_intent.last_provisioning_error)

        # Verify workflow was created and linked
        workflow = ProvisionNewCustomerWorkflow.objects.first()
        self.assertIsNotNone(workflow)
        self.assertEqual(checkout_intent.workflow, workflow)

    @mock.patch('enterprise_access.apps.provisioning.api.LicenseManagerApiClient')
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_checkout_intent_synchronized_on_error(self, mock_lms_api_client, mock_license_manager_client):
        """
        Test that a PAID checkout intent is marked as ERRORED_PROVISIONING with error message on failure.
        """
        # Create a checkout intent in PAID state
        checkout_intent = self._create_checkout_intent(state=CheckoutIntentState.PAID)

        # Setup mocks for provisioning up until subscription plan creation
        mock_lms_client = mock_lms_api_client.return_value
        mock_lms_client.get_enterprise_customer_data.return_value = {
            **DEFAULT_CUSTOMER_RECORD,
            'slug': self.enterprise_slug,
        }
        mock_lms_client.get_enterprise_admin_users.return_value = []
        mock_lms_client.get_enterprise_pending_admin_users.return_value = []
        mock_lms_client.get_enterprise_catalogs.return_value = [DEFAULT_CATALOG_RECORD]

        mock_license_client = mock_license_manager_client.return_value
        mock_license_client.get_customer_agreement.return_value = None
        mock_license_client.create_customer_agreement.return_value = {
            **DEFAULT_AGREEMENT_RECORD, "subscriptions": []
        }
        # Make subscription plan creation fail
        error_message = "License Manager API error"
        mock_license_client.create_subscription_plan.side_effect = Exception(error_message)

        # Make provisioning request (should fail at subscription plan step)
        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=self._get_base_request_payload())
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

        # Refresh checkout intent and verify it was synchronized with error
        checkout_intent.refresh_from_db()
        self.assertEqual(checkout_intent.state, CheckoutIntentState.ERRORED_PROVISIONING)
        self.assertIsNotNone(checkout_intent.workflow)
        self.assertEqual(checkout_intent.last_provisioning_error, error_message)

    @ddt.data(
        CheckoutIntentState.CREATED,
        CheckoutIntentState.FULFILLED,
        CheckoutIntentState.ERRORED_STRIPE_CHECKOUT,
        CheckoutIntentState.EXPIRED,
    )
    @mock.patch('enterprise_access.apps.provisioning.api.LicenseManagerApiClient')
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_checkout_intent_wrong_state_ignored(
        self, intent_state, mock_lms_api_client, mock_license_manager_client
    ):
        """
        Test that non-fulfillable checkout intents are ignored during synchronization.
        """
        # Create a checkout intent in various non-PAID states
        checkout_intent = self._create_checkout_intent(state=intent_state)
        original_state = checkout_intent.state
        original_workflow = checkout_intent.workflow

        # Setup mocks for successful provisioning
        mock_lms_client = mock_lms_api_client.return_value
        mock_lms_client.get_enterprise_customer_data.return_value = {
            **DEFAULT_CUSTOMER_RECORD,
            'slug': self.enterprise_slug,
        }
        mock_lms_client.get_enterprise_admin_users.return_value = []
        mock_lms_client.get_enterprise_pending_admin_users.return_value = []
        mock_lms_client.get_enterprise_catalogs.return_value = [DEFAULT_CATALOG_RECORD]

        mock_license_client = mock_license_manager_client.return_value
        mock_license_client.get_customer_agreement.return_value = None
        mock_license_client.create_customer_agreement.return_value = {
            **DEFAULT_AGREEMENT_RECORD, "subscriptions": []
        }
        mock_license_client.create_subscription_plan.return_value = DEFAULT_SUBSCRIPTION_PLAN_RECORD

        # Make provisioning request
        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=self._get_base_request_payload())
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Verify checkout intent was not modified (stayed in original state)
        checkout_intent.refresh_from_db()
        self.assertEqual(checkout_intent.state, original_state)
        self.assertEqual(checkout_intent.workflow, original_workflow)
        self.assertIsNone(checkout_intent.last_provisioning_error)

        # Verify workflow was still created successfully
        workflow = ProvisionNewCustomerWorkflow.objects.first()
        self.assertIsNotNone(workflow)

    @mock.patch('enterprise_access.apps.provisioning.api.LicenseManagerApiClient')
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_checkout_intent_different_slug_ignored(self, mock_lms_api_client, mock_license_manager_client):
        """
        Test that checkout intents with different enterprise slug are ignored.
        """
        # Create a checkout intent with different enterprise slug
        different_slug = 'different-enterprise-slug'
        checkout_intent = self._create_checkout_intent(
            state=CheckoutIntentState.PAID,
            enterprise_slug=different_slug
        )

        # Setup mocks for successful provisioning
        mock_lms_client = mock_lms_api_client.return_value
        mock_lms_client.get_enterprise_customer_data.return_value = {
            **DEFAULT_CUSTOMER_RECORD,
            'slug': self.enterprise_slug,  # Different from checkout intent slug
        }
        mock_lms_client.get_enterprise_admin_users.return_value = []
        mock_lms_client.get_enterprise_pending_admin_users.return_value = []
        mock_lms_client.get_enterprise_catalogs.return_value = [DEFAULT_CATALOG_RECORD]

        mock_license_client = mock_license_manager_client.return_value
        mock_license_client.get_customer_agreement.return_value = None
        mock_license_client.create_customer_agreement.return_value = {
            **DEFAULT_AGREEMENT_RECORD, "subscriptions": []
        }
        mock_license_client.create_subscription_plan.return_value = DEFAULT_SUBSCRIPTION_PLAN_RECORD

        # Make provisioning request
        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=self._get_base_request_payload())
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Verify checkout intent was not modified (different slug)
        checkout_intent.refresh_from_db()
        self.assertEqual(checkout_intent.state, CheckoutIntentState.PAID)
        self.assertIsNone(checkout_intent.workflow)
        self.assertIsNone(checkout_intent.last_provisioning_error)

        # Verify workflow was still created successfully
        workflow = ProvisionNewCustomerWorkflow.objects.first()
        self.assertIsNotNone(workflow)
