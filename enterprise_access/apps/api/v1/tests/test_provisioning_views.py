"""
Tests for the provisioning views.
"""
import uuid
from unittest import mock

import ddt
from edx_rbac.constants import ALL_ACCESS_CONTEXT
from rest_framework import status
from rest_framework.reverse import reverse

from enterprise_access.apps.core.constants import (
    SYSTEM_ENTERPRISE_ADMIN_ROLE,
    SYSTEM_ENTERPRISE_LEARNER_ROLE,
    SYSTEM_ENTERPRISE_OPERATOR_ROLE,
    SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE
)
from enterprise_access.apps.provisioning.models import (
    GetCreateCustomerStep,
    GetCreateEnterpriseAdminUsersStep,
    ProvisionNewCustomerWorkflow
)
from test_utils import APITest

PROVISIONING_CREATE_ENDPOINT = reverse('api:v1:provisioning-create')

TEST_ENTERPRISE_UUID = uuid.uuid4()


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
    @mock.patch('enterprise_access.apps.provisioning.models.get_or_create_enterprise_admin_users')
    @mock.patch('enterprise_access.apps.provisioning.models.get_or_create_enterprise_customer')
    def test_provisioning_create_allowed_for_provisioning_admins(
            self, role_context_dict, expected_response_code, mock_create_customer, mock_create_admins,
    ):
        """
        Tests that we get expected 200 response for the provisioning create view when
        the requesting user has the correct system role and provides a valid request payload.
        """
        self.set_jwt_cookie([role_context_dict])

        mock_create_customer.return_value = {
            "uuid": str(uuid.uuid4()),
            "name": "Test customer",
            "country": "US",
            "slug": "test-customer",
        }
        mock_create_admins.return_value = {
            "created_admins": [{
                "user_email": "test-admin@example.com",
            }],
            "existing_admins": [],
        }

        request_payload = {
            "enterprise_customer": {
                "name": "Test customer",
                "country": "US",
                "slug": "test-customer",
            },
            "pending_admins": [
                {
                    "user_email": "test-admin@example.com",
                },
            ],
        }
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
            'created_customer_data': {
                'name': 'Test Customer',
                'slug': 'test-customer',
                'country': 'US',
                'uuid': str(TEST_ENTERPRISE_UUID),
            },
            'expected_get_customer_kwargs': {
                'enterprise_customer_slug': 'test-customer',
            },
            'create_customer_called': True,
            'expected_create_customer_kwargs': {
                'name': 'Test Customer',
                'slug': 'test-customer',
                'country': 'US',
            },
        },
        # Data representing the state where a customer with the given slug exists.
        {
            'existing_customer_data': {
                'name': 'Test Customer',
                'slug': 'test-customer',
                'country': 'US',
                'uuid': str(TEST_ENTERPRISE_UUID),
            },
            'created_customer_data': None,
            'expected_get_customer_kwargs': {
                'enterprise_customer_slug': 'test-customer',
            },
            'create_customer_called': False,
            'expected_create_customer_kwargs': None
        },
    )
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_get_or_create_customer_and_admins_created(self, test_data, mock_lms_api_client):
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

        request_payload = {
            "enterprise_customer": {
                'name': 'Test Customer',
                'slug': 'test-customer',
                'country': 'US',
            },
            'pending_admins': [
                {'user_email': 'alice@foo.com'},
                {'user_email': 'bob@foo.com'},
            ],
        }
        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=request_payload)
        assert response.status_code == status.HTTP_201_CREATED

        expected_response_payload = {
            'enterprise_customer': {
                'name': 'Test Customer',
                'slug': 'test-customer',
                'country': 'US',
                'uuid': str(TEST_ENTERPRISE_UUID),
            },
            'customer_admins': {
                'created_admins': [
                    {'user_email': 'alice@foo.com'},
                    {'user_email': 'bob@foo.com'},
                ],
                'existing_admins': [],
            },
        }
        actual_response_payload = response.json()
        self.assertEqual(actual_response_payload, expected_response_payload)

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
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_customer_fetched_admins_fetched_or_created(self, test_data, mock_lms_api_client):
        """
        Tests cases where [pending]admins are fetched or created, but the customer
        already exists
        """
        mock_client = mock_lms_api_client.return_value
        mock_client.get_enterprise_customer_data.return_value = {
            'name': 'Test Customer',
            'slug': 'test-customer',
            'country': 'US',
            'uuid': str(TEST_ENTERPRISE_UUID),
        }
        mock_client.get_enterprise_admin_users.return_value = test_data['existing_admin_users']
        mock_client.get_enterprise_pending_admin_users.return_value = test_data['existing_pending_admin_users']
        mock_client.create_enterprise_admin_user.side_effect = test_data['create_admin_user_side_effect']

        request_payload = {
            'enterprise_customer': {
                'name': 'Test Customer',
                'slug': 'test-customer',
                'country': 'US',
            },
            'pending_admins': [
                {'user_email': 'alice@foo.com'},
                {'user_email': 'bob@foo.com'},
            ],
        }
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
        expected_response_payload = {
            'enterprise_customer': {
                'name': 'Test Customer',
                'slug': 'test-customer',
                'country': 'US',
                'uuid': str(TEST_ENTERPRISE_UUID),
            },
            'customer_admins': {
                'created_admins': expected_created_admins,
                'existing_admins': expected_existing_admins,
            },
        }
        actual_response_payload = response.json()
        self.assertEqual(actual_response_payload, expected_response_payload)

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
