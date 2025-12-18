"""
Tests for the provisioning views.
"""
import random
import uuid
from datetime import timedelta
from unittest import mock

import ddt
from django.conf import settings
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
from enterprise_access.apps.customer_billing.models import (
    CheckoutIntent,
    SelfServiceSubscriptionRenewal,
    StripeEventSummary
)
from enterprise_access.apps.customer_billing.tests.factories import StripeEventDataFactory, StripeEventSummaryFactory
from enterprise_access.apps.provisioning.models import (
    GetCreateCustomerStep,
    GetCreateEnterpriseAdminUsersStep,
    GetCreateFirstPaidSubscriptionPlanStep,
    ProvisionNewCustomerWorkflow
)
from test_utils import APITest

User = get_user_model()

PROVISIONING_CREATE_ENDPOINT = reverse('api:v1:provisioning-create')

TEST_ENTERPRISE_UUID = uuid.uuid4()

TEST_CATALOG_UUID = uuid.uuid4()

TEST_AGREEMENT_UUID = uuid.uuid4()

TEST_TRIAL_SUBSCRIPTION_UUID = uuid.uuid4()
TEST_FIRST_PAID_SUBSCRIPTION_UUID = uuid.uuid4()

DEFAULT_CHECKOUT_INTENT_RECORD = {
    'enterprise_slug': 'test-customer',
    'enterprise_name': 'Test customer',
    'quantity': 5,
    'state': CheckoutIntentState.PAID,
    'expires_at': timezone.now() + timedelta(hours=1),
}

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

DEFAULT_TRIAL_SUBSCRIPTION_PLAN_RECORD = {
    "uuid": str(TEST_TRIAL_SUBSCRIPTION_UUID),
    "title": "provisioning test trial 1",
    "salesforce_opportunity_line_item": "00k000000000000123",
    "created": "2025-05-16T15:20:19.159640+00:00",
    "start_date": "2025-06-01T00:00:00+00:00",
    "expiration_date": "2026-03-31T00:00:00+00:00",
    "is_active": True,
    "is_current": False,
    "plan_type": "Standard Trial",
    "enterprise_catalog_uuid": str(TEST_CATALOG_UUID),
    "product": 1,
}

DEFAULT_FIRST_PAID_SUBSCRIPTION_PLAN_RECORD = {
    "uuid": str(TEST_FIRST_PAID_SUBSCRIPTION_UUID),
    "title": "provisioning test paid 1",
    "salesforce_opportunity_line_item": None,
    "created": "2025-05-16T15:20:19.159640+00:00",
    "start_date": "2026-03-31T00:00:00+00:00",
    "expiration_date": "2027-03-31T00:00:00+00:00",
    "is_active": True,
    "is_current": False,
    "plan_type": "Standard Paid",
    "enterprise_catalog_uuid": str(TEST_CATALOG_UUID),
    "product": 2,
}

DEFAULT_AGREEMENT_RECORD = {
    "uuid": str(TEST_AGREEMENT_UUID),
    "enterprise_customer_uuid": str(TEST_ENTERPRISE_UUID),
    "default_catalog_uuid": None,
    "subscriptions": [
        DEFAULT_TRIAL_SUBSCRIPTION_PLAN_RECORD,
        DEFAULT_FIRST_PAID_SUBSCRIPTION_PLAN_RECORD,
    ],
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
        # omit catalog_query_id to ensure the default is correct
    },
    'customer_agreement': {},
    'trial_subscription_plan': {
        'title': 'provisioning test trial 1',
        'salesforce_opportunity_line_item': '00k000000000000123',
        'start_date': '2025-06-01T00:00:00Z',
        'expiration_date': '2026-03-31T00:00:00Z',
        'product_id': 1,
        'desired_num_licenses': 5,
    },
    'first_paid_subscription_plan': {
        'title': 'provisioning test paid 1',
        'salesforce_opportunity_line_item': None,
        'start_date': '2026-03-31T00:00:00Z',
        'expiration_date': '2027-03-31T00:00:00Z',
        'product_id': 2,
        'desired_num_licenses': 5,
    },
}

EXPECTED_CATALOG_RESPONSE = {
    'uuid': str(TEST_CATALOG_UUID),
    'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID),
    'title': 'Test catalog',
    'catalog_query_id': 2,
}

EXPECTED_SUBSCRIPTION_PLAN_RENEWAL_RESPONSE = {
    'id': 1,
    'prior_subscription_plan': str(TEST_TRIAL_SUBSCRIPTION_UUID),
    'renewed_subscription_plan': str(TEST_FIRST_PAID_SUBSCRIPTION_UUID),
    'number_of_licenses': 5,
    'effective_date': '2026-03-31T00:00:00+00:00',
    'renewed_expiration_date': '2027-03-31T00:00:00+00:00',
    'salesforce_opportunity_id': None,
}


@ddt.ddt
class TestProvisioningAuth(APITest):
    """
    Tests Authentication and Permission checking for provisioning.
    """
    def setUp(self):
        super().setUp()
        self.checkout_intent = self._create_checkout_intent()

    def tearDown(self):
        super().tearDown()
        GetCreateCustomerStep.objects.all().delete()
        GetCreateEnterpriseAdminUsersStep.objects.all().delete()
        ProvisionNewCustomerWorkflow.objects.all().delete()
        StripeEventSummary.objects.all().delete()
        CheckoutIntent.objects.all().delete()

    def _create_checkout_intent(self):
        """Helper to create a checkout intent for testing."""
        return CheckoutIntent.objects.create(
            user=UserFactory(),
            **DEFAULT_CHECKOUT_INTENT_RECORD,
        )

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
    @mock.patch('enterprise_access.apps.provisioning.models.get_or_create_subscription_plan_renewal')
    def test_provisioning_create_allowed_for_provisioning_admins(
        self,
        role_context_dict,
        expected_response_code,
        mock_create_renewal,
        mock_create_customer,
        mock_create_admins,
        mock_create_catalog,
        mock_create_agreement,
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
        mock_create_renewal.return_value = EXPECTED_SUBSCRIPTION_PLAN_RENEWAL_RESPONSE

        event_data = StripeEventDataFactory.create(checkout_intent=self.checkout_intent)
        StripeEventSummaryFactory.create(stripe_event_data=event_data)

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
        self.checkout_intent = self._create_checkout_intent()

    def _create_checkout_intent(self):
        """Helper to create a checkout intent for testing."""
        return CheckoutIntent.objects.create(
            user=UserFactory(),
            **DEFAULT_CHECKOUT_INTENT_RECORD,
        )

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
    @mock.patch('enterprise_access.apps.provisioning.models.get_or_create_subscription_plan_renewal')
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_get_or_create_customer_and_admins_created(
        self,
        test_data,
        mock_lms_api_client,
        mock_create_renewal,
        mock_create_agreement,
    ):
        """
        Tests cases where admins don't exist and customer is fetched or created.
        """
        mock_client = mock_lms_api_client.return_value
        mock_client.get_enterprise_customer_data.return_value = test_data['existing_customer_data']
        mock_client.get_enterprise_admin_users.return_value = []

        if test_data['created_customer_data']:
            mock_client.create_enterprise_customer.return_value = test_data['created_customer_data']

        mock_client.create_enterprise_admin_user.side_effect = [
            {'user_email': 'alice@foo.com', 'enterprise_customer_uuid': TEST_ENTERPRISE_UUID},
            {'user_email': 'bob@foo.com', 'enterprise_customer_uuid': TEST_ENTERPRISE_UUID},
        ]
        mock_client.get_enterprise_catalogs.return_value = [DEFAULT_CATALOG_RECORD]
        mock_create_agreement.return_value = DEFAULT_AGREEMENT_RECORD
        mock_create_renewal.return_value = EXPECTED_SUBSCRIPTION_PLAN_RENEWAL_RESPONSE
        event_data = StripeEventDataFactory.create(checkout_intent=self.checkout_intent)
        StripeEventSummaryFactory.create(stripe_event_data=event_data)

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

        # Verify that a SelfServiceSubscriptionRenewal record was created during provisioning
        renewal_records = SelfServiceSubscriptionRenewal.objects.filter(
            checkout_intent=self.checkout_intent
        )
        self.assertEqual(renewal_records.count(), 1)

        renewal_record = renewal_records.first()
        self.assertEqual(
            renewal_record.subscription_plan_renewal_id,
            EXPECTED_SUBSCRIPTION_PLAN_RENEWAL_RESPONSE['id'],
        )
        self.assertEqual(
            str(renewal_record.prior_subscription_plan_uuid),
            EXPECTED_SUBSCRIPTION_PLAN_RENEWAL_RESPONSE['prior_subscription_plan'],
        )
        self.assertEqual(
            str(renewal_record.renewed_subscription_plan_uuid),
            EXPECTED_SUBSCRIPTION_PLAN_RENEWAL_RESPONSE['renewed_subscription_plan'],
        )
        self.assertIsNone(renewal_record.processed_at)

    @ddt.data(
        # No admin users exist, two admins created.
        {
            'existing_admin_users': [],
            'create_admins_called': True,
            'create_admin_user_side_effect': [
                {'user_email': 'alice@foo.com', 'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)},
                {'user_email': 'bob@foo.com', 'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)},
            ],
            'expected_create_admin_calls': [
                mock.call(str(TEST_ENTERPRISE_UUID), 'alice@foo.com'),
                mock.call(str(TEST_ENTERPRISE_UUID), 'bob@foo.com'),
            ],
        },
        # One admin exists, one new one created.
        {
            'existing_admin_users': [
                # Note the different in the 'email' key here
                {'email': 'alice@foo.com', 'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)},
            ],
            'create_admins_called': True,
            'create_admin_user_side_effect': [
                {'user_email': 'bob@foo.com', 'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)},
            ],
            'expected_create_admin_calls': [
                mock.call(str(TEST_ENTERPRISE_UUID), 'bob@foo.com'),
            ],
        },
        # Two admins exists, none created.
        {
            'existing_admin_users': [
                {'email': 'alice@foo.com', 'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)},
                {'email': 'bob@foo.com', 'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)},
            ],
            'created_admin_users': [],
            'create_admins_called': False,
            'create_admin_user_side_effect': [],
            'expected_create_admin_calls': [],
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
        mock_client.create_enterprise_admin_user.side_effect = test_data['create_admin_user_side_effect']
        mock_client.get_enterprise_catalogs.return_value = [DEFAULT_CATALOG_RECORD]
        mock_license_client = mock_license_manager_client.return_value
        mock_license_client.get_customer_agreement.return_value = DEFAULT_AGREEMENT_RECORD
        mock_license_client.create_subscription_plan_renewal.return_value = EXPECTED_SUBSCRIPTION_PLAN_RENEWAL_RESPONSE
        event_data = StripeEventDataFactory.create(checkout_intent=self.checkout_intent)
        StripeEventSummaryFactory.create(stripe_event_data=event_data)

        request_payload = {**DEFAULT_REQUEST_PAYLOAD}
        request_payload['pending_admins'] = [
            {'user_email': 'alice@foo.com'},
            {'user_email': 'bob@foo.com'},
        ]
        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=request_payload)
        assert response.status_code == status.HTTP_201_CREATED

        existing_emails = sorted(
            [record['email'] for record in test_data['existing_admin_users']]
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
        if test_data['create_admins_called']:
            mock_client.create_enterprise_admin_user.assert_has_calls(
                test_data['expected_create_admin_calls'],
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
    @mock.patch('enterprise_access.apps.provisioning.models.get_or_create_subscription_plan_renewal')
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_catalog_fetched_or_created(
        self,
        test_data,
        mock_lms_api_client,
        mock_create_renewal,
        mock_create_agreement,
    ):
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
        mock_create_renewal.return_value = EXPECTED_SUBSCRIPTION_PLAN_RENEWAL_RESPONSE

        event_data = StripeEventDataFactory.create(checkout_intent=self.checkout_intent)
        StripeEventSummaryFactory.create(stripe_event_data=event_data)

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
    @mock.patch('enterprise_access.apps.provisioning.models.get_or_create_subscription_plan_renewal')
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_catalog_created_with_generated_title_and_inferred_query_id(
        self,
        mock_lms_api_client,
        mock_create_renewal,
        mock_create_agreement,
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
        mock_create_renewal.return_value = EXPECTED_SUBSCRIPTION_PLAN_RENEWAL_RESPONSE

        event_data = StripeEventDataFactory.create(checkout_intent=self.checkout_intent)
        StripeEventSummaryFactory.create(stripe_event_data=event_data)

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
            mock_license_client.create_subscription_plan.side_effect = [
                DEFAULT_TRIAL_SUBSCRIPTION_PLAN_RECORD,
                DEFAULT_FIRST_PAID_SUBSCRIPTION_PLAN_RECORD,
            ]

        mock_license_client.create_subscription_plan_renewal.return_value = EXPECTED_SUBSCRIPTION_PLAN_RENEWAL_RESPONSE

        event_data = StripeEventDataFactory.create(checkout_intent=self.checkout_intent)
        StripeEventSummaryFactory.create(stripe_event_data=event_data)

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

    @ddt.data(True, False)
    @mock.patch('enterprise_access.apps.provisioning.api.LicenseManagerApiClient')
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_new_subscription_plan_created(
        self, response_has_product, mock_lms_api_client, mock_license_manager_client,
    ):
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
        trial_plan_record = dict(DEFAULT_TRIAL_SUBSCRIPTION_PLAN_RECORD)
        first_paid_plan_record = dict(DEFAULT_FIRST_PAID_SUBSCRIPTION_PLAN_RECORD)
        if not response_has_product:
            trial_plan_record.pop('product')
        mock_license_client.create_subscription_plan.side_effect = [trial_plan_record, first_paid_plan_record]
        mock_license_client.create_subscription_plan_renewal.return_value = EXPECTED_SUBSCRIPTION_PLAN_RENEWAL_RESPONSE

        event_data = StripeEventDataFactory.create(checkout_intent=self.checkout_intent)
        StripeEventSummaryFactory.create(stripe_event_data=event_data)

        # Make the provisioning request
        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=DEFAULT_REQUEST_PAYLOAD)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        actual_response = response.json()
        # The subscription_plan in the response should match what the license manager returned
        self.assertIn('trial_subscription_plan', actual_response)
        self.assertEqual(
            actual_response['trial_subscription_plan']['uuid'],
            trial_plan_record['uuid'],
        )
        self.assertEqual(
            actual_response['trial_subscription_plan']['title'],
            trial_plan_record['title'],
        )
        self.assertEqual(
            actual_response['trial_subscription_plan']['salesforce_opportunity_line_item'],
            trial_plan_record['salesforce_opportunity_line_item'],
        )
        self.assertTrue(actual_response['trial_subscription_plan']['is_active'])
        if response_has_product:
            self.assertEqual(
                actual_response['trial_subscription_plan']['product'],
                trial_plan_record['product'],
            )
        else:
            self.assertIsNone(actual_response['trial_subscription_plan']['product'])

        # Workflow record/step assertions
        workflow = ProvisionNewCustomerWorkflow.objects.all()[0]
        self.assertIsNotNone(workflow.get_create_trial_subscription_plan_step())
        self.assertIsNotNone(workflow.get_create_first_paid_subscription_plan_step())

        # LicenseManagerApiClient should be called to create agreement and subscription plan
        mock_license_client.get_customer_agreement.assert_called_once_with(
            str(TEST_ENTERPRISE_UUID)
        )
        mock_license_client.create_customer_agreement.assert_called_once_with(
            str(TEST_ENTERPRISE_UUID),
            'test-customer',
            default_catalog_uuid=None,
        )

        expected_create_subscription_plan_calls = [
            mock.call(
                customer_agreement_uuid=str(TEST_AGREEMENT_UUID),
                title='provisioning test trial 1',
                salesforce_opportunity_line_item='00k000000000000123',
                start_date='2025-06-01T00:00:00+00:00',
                expiration_date='2026-03-31T00:00:00+00:00',
                desired_num_licenses=5,
                enterprise_catalog_uuid=str(TEST_CATALOG_UUID),
                product_id=1,
            ),
            mock.call(
                customer_agreement_uuid=str(TEST_AGREEMENT_UUID),
                title='provisioning test paid 1',
                salesforce_opportunity_line_item=None,
                start_date='2026-03-31T00:00:00+00:00',
                expiration_date='2027-03-31T00:00:00+00:00',
                desired_num_licenses=5,
                enterprise_catalog_uuid=str(TEST_CATALOG_UUID),
                product_id=2,
            ),
        ]
        mock_license_client.create_subscription_plan.assert_has_calls(
            expected_create_subscription_plan_calls,
            any_order=False,
        )
        assert mock_license_client.create_subscription_plan.call_count == 2

    @mock.patch('enterprise_access.apps.provisioning.api.LicenseManagerApiClient')
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    @mock.patch('enterprise_access.apps.api.v1.views.provisioning.logger')
    def test_legacy_single_plan_request_transformation(
        self, mock_logger, mock_lms_api_client, mock_license_manager_client
    ):
        """
        Test that legacy requests with single 'subscription_plan' key are transformed
        to the new two-plan format and successfully provision resources.
        """
        # Setup mocks for successful provisioning.
        mock_lms_client = mock_lms_api_client.return_value
        mock_lms_client.get_enterprise_customer_data.return_value = None
        mock_lms_client.create_enterprise_customer.return_value = DEFAULT_CUSTOMER_RECORD
        mock_lms_client.get_enterprise_admin_users.return_value = []
        mock_lms_client.get_enterprise_pending_admin_users.return_value = []
        mock_lms_client.get_enterprise_catalogs.return_value = [DEFAULT_CATALOG_RECORD]

        mock_license_client = mock_license_manager_client.return_value
        mock_license_client.get_customer_agreement.return_value = None
        mock_license_client.create_customer_agreement.return_value = {
            **DEFAULT_AGREEMENT_RECORD, "subscriptions": []
        }
        mock_license_client.create_subscription_plan.side_effect = [
            DEFAULT_TRIAL_SUBSCRIPTION_PLAN_RECORD,
            DEFAULT_FIRST_PAID_SUBSCRIPTION_PLAN_RECORD,
        ]
        mock_license_client.create_subscription_plan_renewal.return_value = (
            EXPECTED_SUBSCRIPTION_PLAN_RENEWAL_RESPONSE
        )

        # Create a legacy request payload with 'subscription_plan' instead of the new format.
        legacy_request_payload = {**DEFAULT_REQUEST_PAYLOAD}
        legacy_request_payload.pop('first_paid_subscription_plan')
        legacy_request_payload['subscription_plan'] = legacy_request_payload.pop('trial_subscription_plan')

        event_data = StripeEventDataFactory.create(checkout_intent=self.checkout_intent)
        StripeEventSummaryFactory.create(stripe_event_data=event_data)

        # Make the provisioning request.
        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=legacy_request_payload)

        # Should succeed despite using legacy format.
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Verify warning was logged about deprecated format.
        mock_logger.warning.assert_called_once()
        warning_message = mock_logger.warning.call_args[0][0]
        self.assertIn('Deprecated request format detected', warning_message)
        self.assertIn('subscription_plan', warning_message)

        # Verify info log about transformation.
        mock_logger.info.assert_called()
        info_calls = [call[0][0] for call in mock_logger.info.call_args_list]
        self.assertTrue(
            any('Transformed legacy subscription_plan' in msg for msg in info_calls),
            "Expected transformation log message not found"
        )

        # Verify response has both trial and paid subscription plans.
        response_data = response.json()
        self.assertIn('trial_subscription_plan', response_data)
        self.assertIn('first_paid_subscription_plan', response_data)

        # Verify the workflow input_data contains correct trial and paid plan data
        workflow = ProvisionNewCustomerWorkflow.objects.first()
        trial_plan_input = workflow.input_data.get('create_trial_subscription_plan_input')
        first_paid_plan_input = workflow.input_data.get('create_first_paid_subscription_plan_input')
        assert trial_plan_input['title'] == legacy_request_payload['subscription_plan']['title']
        assert trial_plan_input['salesforce_opportunity_line_item'] == (
            legacy_request_payload['subscription_plan']['salesforce_opportunity_line_item']
        )
        assert trial_plan_input['product_id'] == legacy_request_payload['subscription_plan']['product_id']
        assert 'First Paid Plan' in first_paid_plan_input['title']
        assert first_paid_plan_input['product_id'] == settings.PROVISIONING_PAID_SUBSCRIPTION_PRODUCT_ID
        assert first_paid_plan_input['salesforce_opportunity_line_item'] is None


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
        GetCreateFirstPaidSubscriptionPlanStep.objects.all().delete()
        ProvisionNewCustomerWorkflow.objects.all().delete()
        User.objects.filter(email__endswith='@test-factory.com').delete()

    def _create_checkout_intent(
        self,
        state=CheckoutIntentState.PAID,
        user=None,
        enterprise_slug=None,
        enterprise_name=None,
    ):
        """Helper to create a checkout intent for testing."""
        return CheckoutIntent.objects.create(
            user=user or self.user,
            enterprise_slug=enterprise_slug or self.enterprise_slug,
            enterprise_name=enterprise_name or 'Test Enterprise',
            quantity=10,
            state=state,
            expires_at=timezone.now() + timedelta(hours=1),
        )

    def _get_base_request_payload(self):
        """Helper to get base request payload with test enterprise slug."""
        payload = {**DEFAULT_REQUEST_PAYLOAD}
        payload['enterprise_customer']['slug'] = self.enterprise_slug
        return payload

    @ddt.data(*CheckoutIntent.FULFILLABLE_STATES())
    @mock.patch('enterprise_access.apps.provisioning.api.LicenseManagerApiClient')
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_checkout_intent_synchronized_on_success(
        self, intent_state, mock_lms_api_client, mock_license_manager_client,
    ):
        """
        Test that a fulfillable checkout intent is linked to workflow and marked as FULFILLED on success.
        """
        checkout_intent = self._create_checkout_intent(state=intent_state)
        event_data = StripeEventDataFactory.create(checkout_intent=checkout_intent)
        StripeEventSummaryFactory.create(stripe_event_data=event_data)

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
        mock_license_client.create_subscription_plan.side_effect = [
            DEFAULT_TRIAL_SUBSCRIPTION_PLAN_RECORD,
            DEFAULT_FIRST_PAID_SUBSCRIPTION_PLAN_RECORD,
        ]
        mock_license_client.create_subscription_plan_renewal.return_value = EXPECTED_SUBSCRIPTION_PLAN_RENEWAL_RESPONSE

        # Make provisioning request
        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=self._get_base_request_payload())
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Refresh checkout intent and verify it was synchronized
        checkout_intent.refresh_from_db()
        self.assertEqual(checkout_intent.state, CheckoutIntentState.FULFILLED)
        self.assertIsNotNone(checkout_intent.workflow)
        self.assertIsNone(checkout_intent.last_provisioning_error)
        self.assertEqual(str(checkout_intent.enterprise_uuid), str(TEST_ENTERPRISE_UUID))

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
        mock_license_client.create_subscription_plan_renewal.return_value = EXPECTED_SUBSCRIPTION_PLAN_RENEWAL_RESPONSE

        # Make provisioning request (should fail at subscription plan step)
        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=self._get_base_request_payload())
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

        # Refresh checkout intent and verify it was synchronized with error
        checkout_intent.refresh_from_db()
        self.assertEqual(checkout_intent.state, CheckoutIntentState.ERRORED_PROVISIONING)
        self.assertIsNotNone(checkout_intent.workflow)
        self.assertEqual(checkout_intent.last_provisioning_error, error_message)
        self.assertEqual(str(checkout_intent.enterprise_uuid), str(TEST_ENTERPRISE_UUID))

    @ddt.data(*(set(CheckoutIntentState) - set(CheckoutIntent.FULFILLABLE_STATES())))
    @mock.patch('enterprise_access.apps.provisioning.api.LicenseManagerApiClient')
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_checkout_intent_wrong_state(
        self, intent_state, mock_lms_api_client, mock_license_manager_client
    ):
        """
        Test that non-fulfillable checkout intents CRASH the workflow before it provisionions any subscriptions.
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
        mock_license_client.create_subscription_plan.side_effect = [
            DEFAULT_TRIAL_SUBSCRIPTION_PLAN_RECORD,
            DEFAULT_FIRST_PAID_SUBSCRIPTION_PLAN_RECORD,
        ]
        mock_license_client.create_subscription_plan_renewal.return_value = EXPECTED_SUBSCRIPTION_PLAN_RENEWAL_RESPONSE

        # Make provisioning request
        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=self._get_base_request_payload())
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

        # Verify checkout intent was not modified (stayed in original state)
        checkout_intent.refresh_from_db()
        self.assertEqual(checkout_intent.state, original_state)
        self.assertEqual(checkout_intent.workflow, original_workflow)
        self.assertIsNone(checkout_intent.last_provisioning_error)
        self.assertIsNone(checkout_intent.enterprise_uuid)

        # Verify workflow was still created successfully
        workflow = ProvisionNewCustomerWorkflow.objects.first()
        assert workflow.exception_message == 'No fulfillable CheckoutIntent records for the given slug were found.'

    @mock.patch('enterprise_access.apps.provisioning.api.LicenseManagerApiClient')
    @mock.patch('enterprise_access.apps.provisioning.api.LmsApiClient')
    def test_checkout_intent_different_slug_ignored(self, mock_lms_api_client, mock_license_manager_client):
        """
        Test that checkout intents with different enterprise slug are ignored.
        """
        # The checkout intent we expect to be updated.
        main_checkout_intent = self._create_checkout_intent(state=CheckoutIntentState.PAID)
        event_data = StripeEventDataFactory.create(checkout_intent=main_checkout_intent)
        StripeEventSummaryFactory.create(stripe_event_data=event_data)

        # Create a checkout intent with different enterprise slug. Later, test that this is NOT modified.
        different_slug = 'different-enterprise-slug'
        different_checkout_intent = self._create_checkout_intent(
            user=UserFactory(),
            state=CheckoutIntentState.PAID,
            enterprise_slug=different_slug,
        )
        event_data = StripeEventDataFactory.create(checkout_intent=different_checkout_intent)
        StripeEventSummaryFactory.create(stripe_event_data=event_data)

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
        mock_license_client.create_subscription_plan.side_effect = [
            DEFAULT_TRIAL_SUBSCRIPTION_PLAN_RECORD,
            DEFAULT_FIRST_PAID_SUBSCRIPTION_PLAN_RECORD,
        ]
        mock_license_client.create_subscription_plan_renewal.return_value = EXPECTED_SUBSCRIPTION_PLAN_RENEWAL_RESPONSE

        # Make provisioning request
        response = self.client.post(PROVISIONING_CREATE_ENDPOINT, data=self._get_base_request_payload())
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Verify different_checkout_intent was NOT modified (different slug)
        different_checkout_intent.refresh_from_db()
        self.assertEqual(different_checkout_intent.state, CheckoutIntentState.PAID)
        self.assertIsNone(different_checkout_intent.workflow)
        self.assertIsNone(different_checkout_intent.last_provisioning_error)
        self.assertIsNone(different_checkout_intent.enterprise_uuid)

        # Verify the main checkout intent WAS modified.
        main_checkout_intent.refresh_from_db()
        self.assertEqual(main_checkout_intent.state, CheckoutIntentState.FULFILLED)
        self.assertIsNotNone(main_checkout_intent.workflow)
        self.assertIsNotNone(main_checkout_intent.enterprise_uuid)

        # Verify workflow was still created successfully
        workflow = ProvisionNewCustomerWorkflow.objects.first()
        self.assertIsNotNone(workflow)


@ddt.ddt
class TestSubscriptionPlanOLIUpdateView(APITest):
    """
    Tests for the SubscriptionPlan OLI update endpoint.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.checkout_intent = CheckoutIntent.objects.create(
            user=self.user,
            enterprise_slug='test-enterprise',
            enterprise_name='Test Enterprise',
            quantity=10,
            state=CheckoutIntentState.FULFILLED,
            expires_at=timezone.now() + timedelta(hours=1),
            enterprise_uuid=TEST_ENTERPRISE_UUID,
        )
        self.workflow = ProvisionNewCustomerWorkflow.objects.create(
            input_data={'test': 'data'}
        )
        self.checkout_intent.workflow = self.workflow
        self.checkout_intent.save()

        self.subscription_plan_uuid = uuid.uuid4()
        # Create a subscription plan step with complete output data
        GetCreateFirstPaidSubscriptionPlanStep.objects.create(
            workflow_record_uuid=self.workflow.uuid,
            input_data={
                'title': 'Paid Plan',
                'is_trial': False,
                'salesforce_opportunity_line_item': 'existing_oli_123',
                'start_date': '2025-01-15T00:00:00Z',
                'expiration_date': '2025-12-31T23:59:59Z',
                'desired_num_licenses': 10,
                'product_id': 1,
            },
            output_data={
                'uuid': str(self.subscription_plan_uuid),
                'title': 'Paid Plan',
                'salesforce_opportunity_line_item': 'existing_oli_123',
                'created': '2025-01-01T00:00:00Z',
                'start_date': '2025-01-15T00:00:00Z',
                'expiration_date': '2025-12-31T23:59:59Z',
                'is_active': True,
                'is_current': True,
                'plan_type': 'Standard',
                'enterprise_catalog_uuid': str(TEST_CATALOG_UUID),
                'product': 1,
            }
        )

        self.endpoint_url = reverse('api:v1:subscription-plan-oli-update')
        self.set_jwt_cookie([
            {
                'system_wide_role': SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE,
                'context': ALL_ACCESS_CONTEXT,
            },
        ])

    def tearDown(self):
        super().tearDown()
        CheckoutIntent.objects.all().delete()
        ProvisionNewCustomerWorkflow.objects.all().delete()
        GetCreateFirstPaidSubscriptionPlanStep.objects.all().delete()

    @mock.patch('enterprise_access.apps.api.v1.views.provisioning.LicenseManagerApiClient')
    def test_successful_oli_update(self, mock_license_manager_client):
        """Test successful update of SubscriptionPlan OLI."""
        mock_client = mock_license_manager_client.return_value
        mock_client.update_subscription_plan.return_value = {
            'uuid': str(self.subscription_plan_uuid),
            'salesforce_opportunity_line_item': 'new_oli_456',
        }

        request_data = {
            'checkout_intent_id': self.checkout_intent.id,
            'salesforce_opportunity_line_item': 'new_oli_456',
            'is_trial': False,
        }

        response = self.client.post(self.endpoint_url, data=request_data)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        response_data = response.json()
        self.assertTrue(response_data['success'])
        self.assertEqual(str(response_data['subscription_plan_uuid']), str(self.subscription_plan_uuid))
        self.assertEqual(response_data['salesforce_opportunity_line_item'], 'new_oli_456')
        self.assertEqual(str(response_data['checkout_intent_uuid']), str(self.checkout_intent.uuid))
        self.assertEqual(str(response_data['checkout_intent_id']), str(self.checkout_intent.id))

        mock_client.update_subscription_plan.assert_called_once_with(
            subscription_uuid=str(self.subscription_plan_uuid),
            salesforce_opportunity_line_item='new_oli_456'
        )

    def test_checkout_intent_not_found(self):
        """Test error when CheckoutIntent doesn't exist."""
        request_data = {
            'checkout_intent_uuid': str(uuid.uuid4()),  # Non-existent id
            'salesforce_opportunity_line_item': 'new_oli_456',
        }

        response = self.client.post(self.endpoint_url, data=request_data)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        response_data = response.json()
        if isinstance(response_data, dict):
            self.assertIn('not found', response_data.get('detail', '').lower())
        else:
            self.assertTrue(any('not found' in str(item).lower() for item in response_data))

    def test_no_workflow_associated(self):
        """Test error when CheckoutIntent has no workflow."""
        checkout_intent_no_workflow = CheckoutIntent.objects.create(
            user=UserFactory(),
            enterprise_slug='test-no-workflow',
            enterprise_name='Test No Workflow',
            quantity=5,
            state=CheckoutIntentState.PAID,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        request_data = {
            'checkout_intent_uuid': str(checkout_intent_no_workflow.uuid),
            'salesforce_opportunity_line_item': 'new_oli_456',
        }

        response = self.client.post(self.endpoint_url, data=request_data)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response_data = response.json()
        # Handle both dict and list response formats
        if isinstance(response_data, dict):
            self.assertIn('no associated workflow', response_data.get('detail', '').lower())
        else:
            # If it's a list (field errors), check the first item
            self.assertTrue(any('no associated workflow' in str(item).lower() for item in response_data))

    @mock.patch('enterprise_access.apps.api.v1.views.provisioning.LicenseManagerApiClient')
    def test_license_manager_api_error(self, mock_license_manager_client):
        """Test error handling when License Manager API fails."""
        mock_client = mock_license_manager_client.return_value
        mock_client.update_subscription_plan.side_effect = Exception('License Manager API Error')

        request_data = {
            'checkout_intent_uuid': str(self.checkout_intent.uuid),
            'salesforce_opportunity_line_item': 'new_oli_456',
            'is_trial': False,
        }

        response = self.client.post(self.endpoint_url, data=request_data)

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        response_data = response.json()
        if isinstance(response_data, dict):
            self.assertIn('failed to update subscription plan', response_data.get('detail', '').lower())
        else:
            self.assertTrue(any('failed to update subscription plan' in str(item).lower() for item in response_data))

    def test_unauthorized_access(self):
        """Test that unauthorized users cannot access the endpoint."""
        self.set_jwt_cookie([])  # Remove authorization

        request_data = {
            'checkout_intent_id': str(self.checkout_intent.id),
            'salesforce_opportunity_line_item': 'new_oli_456',
        }

        response = self.client.post(self.endpoint_url, data=request_data)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
