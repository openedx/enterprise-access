"""
Tests for Enterprise Access Subsidy Access Policy app API v1 views.
"""
from datetime import datetime, timedelta
from operator import itemgetter
from unittest import mock
from unittest.mock import call, patch
from uuid import UUID, uuid4

import ddt
from django.conf import settings
from requests.exceptions import HTTPError
from rest_framework import status
from rest_framework.reverse import reverse

from enterprise_access.apps.content_assignments.constants import LearnerContentAssignmentStateChoices
from enterprise_access.apps.content_assignments.tests.factories import (
    AssignmentConfigurationFactory,
    LearnerContentAssignmentFactory
)
from enterprise_access.apps.core.constants import (
    SYSTEM_ENTERPRISE_ADMIN_ROLE,
    SYSTEM_ENTERPRISE_LEARNER_ROLE,
    SYSTEM_ENTERPRISE_OPERATOR_ROLE
)
from enterprise_access.apps.subsidy_access_policy.constants import (
    REASON_LEARNER_ASSIGNMENT_CANCELLED,
    REASON_LEARNER_ASSIGNMENT_FAILED,
    REASON_LEARNER_NOT_ASSIGNED_CONTENT,
    REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY,
    AccessMethods,
    MissingSubsidyAccessReasonUserMessages,
    PolicyTypes,
    TransactionStateChoices
)
from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    AssignedLearnerCreditAccessPolicyFactory,
    PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory,
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory
)
from enterprise_access.apps.subsidy_access_policy.utils import create_idempotency_key_for_transaction
from test_utils import APITestWithMocks

SUBSIDY_ACCESS_POLICY_LIST_ENDPOINT = reverse('api:v1:subsidy-access-policies-list')

TEST_ENTERPRISE_UUID = uuid4()


# pylint: disable=missing-function-docstring
class CRUDViewTestMixin:
    """
    Mixin to set some basic state for test classes that cover the
    subsidy access policy CRUD views.
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.enterprise_uuid = TEST_ENTERPRISE_UUID

        cls.redeemable_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            display_name='A redeemable policy',
            enterprise_customer_uuid=cls.enterprise_uuid,
            spend_limit=3,
            active=True,
        )
        cls.non_redeemable_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            display_name='A non-redeemable policy',
            enterprise_customer_uuid=cls.enterprise_uuid,
            spend_limit=0,
            active=True,
        )

    def setUp(self):
        super().setUp()
        # Start in an unauthenticated state.
        self.client.logout()

    def setup_subsidy_mocks(self):
        """
        Setup mocks for subsidy.
        """
        self.yesterday = datetime.utcnow() - timedelta(days=1)
        self.tomorrow = datetime.utcnow() + timedelta(days=1)
        mock_subsidy = {
            'id': 123455,
            'active_datetime': self.yesterday,
            'expiration_datetime': self.tomorrow,
            'current_balance': 4,
            'is_active': True,
        }
        subsidy_client_patcher = patch.object(
            SubsidyAccessPolicy, 'subsidy_client'
        )
        self.mock_subsidy_client = subsidy_client_patcher.start()
        self.mock_subsidy_client.retrieve_subsidy.return_value = mock_subsidy

        self.addCleanup(subsidy_client_patcher.stop)


@ddt.ddt
class TestPolicyCRUDAuthNAndPermissionChecks(CRUDViewTestMixin, APITestWithMocks):
    """
    Tests Authentication and Permission checking for Subsidy Access Policy CRUD views.
    """
    @ddt.data(
        # A role that's not mapped to any feature perms will get you a 403.
        (
            {'system_wide_role': 'some-other-role', 'context': str(TEST_ENTERPRISE_UUID)},
            status.HTTP_403_FORBIDDEN,
        ),
        # A good admin role, but in a context/customer we're not aware of, gets you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(uuid4())},
            status.HTTP_403_FORBIDDEN,
        ),
        # A good learner role, but in a context/customer we're not aware of, gets you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(uuid4())},
            status.HTTP_403_FORBIDDEN,
        ),
        # An operator role, but in a context/customer we're not aware of, gets you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(uuid4())},
            status.HTTP_403_FORBIDDEN,
        ),
        # No JWT based auth, no soup for you.
        (
            None,
            status.HTTP_401_UNAUTHORIZED,
        ),
    )
    @ddt.unpack
    def test_policy_crud_views_unauthorized_forbidden(self, role_context_dict, expected_response_code):
        """
        Tests that we get expected 40x responses for all of the policy readonly views.
        """
        # Set the JWT-based auth that we'll use for every request
        if role_context_dict:
            self.set_jwt_cookie([role_context_dict])

        request_kwargs = {'uuid': str(self.redeemable_policy.uuid)}

        detail_url = reverse('api:v1:subsidy-access-policies-detail', kwargs=request_kwargs)
        list_url = reverse('api:v1:subsidy-access-policies-list')

        # Test the retrieve endpoint
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, expected_response_code)

        # Test the list endpoint
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, expected_response_code)

        # Test the create action
        response = self.client.post(list_url, data={'any': 'payload'})
        self.assertEqual(response.status_code, expected_response_code)

        # Test the patch action
        response = self.client.patch(detail_url, data={'any': 'other payload'})
        self.assertEqual(response.status_code, expected_response_code)

        # Test the destroy action
        response = self.client.delete(detail_url)
        self.assertEqual(response.status_code, expected_response_code)

    @ddt.data(
        # A role that's not mapped to any feature perms will get you a 403.
        (
            {'system_wide_role': 'some-other-role', 'context': str(TEST_ENTERPRISE_UUID)},
            status.HTTP_403_FORBIDDEN,
        ),
        # A good admin role, but in a context/customer we're not aware of, gets you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(uuid4())},
            status.HTTP_403_FORBIDDEN,
        ),
        # A good admin role, even with the correct context/customer, gets you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
            status.HTTP_403_FORBIDDEN,
        ),
        # A good learner role, but in a context/customer we're not aware of, gets you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(uuid4())},
            status.HTTP_403_FORBIDDEN,
        ),
        # A good learner role, even with the correct context/customer, gets you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
            status.HTTP_403_FORBIDDEN,
        ),
        # An operator role, but in a context/customer we're not aware of, gets you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(uuid4())},
            status.HTTP_403_FORBIDDEN,
        ),
        # No JWT based auth, no soup for you.
        (
            None,
            status.HTTP_401_UNAUTHORIZED,
        ),
    )
    @ddt.unpack
    def test_policy_crud_write_views_unauthorized_forbidden(self, role_context_dict, expected_response_code):
        """
        Tests that we get expected 40x responses for all of the policy write views.
        """
        # Set the JWT-based auth that we'll use for every request
        if role_context_dict:
            self.set_jwt_cookie([role_context_dict])

        request_kwargs = {'uuid': str(self.redeemable_policy.uuid)}

        # Test the create endpoint.
        response = self.client.post(
            SUBSIDY_ACCESS_POLICY_LIST_ENDPOINT,
            data={'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)},
        )
        self.assertEqual(response.status_code, expected_response_code)

        # Test the delete endpoint.
        response = self.client.delete(reverse('api:v1:subsidy-access-policies-detail', kwargs=request_kwargs))
        self.assertEqual(response.status_code, expected_response_code)

        # Test the update and partial_update views.
        response = self.client.put(reverse('api:v1:subsidy-access-policies-detail', kwargs=request_kwargs))
        self.assertEqual(response.status_code, expected_response_code)

        response = self.client.patch(reverse('api:v1:subsidy-access-policies-detail', kwargs=request_kwargs))
        self.assertEqual(response.status_code, expected_response_code)


@ddt.ddt
class TestAuthenticatedPolicyCRUDViews(CRUDViewTestMixin, APITestWithMocks):
    """
    Test the list and detail views for subsidy access policy records.
    """

    def setUp(self):
        self.maxDiff = None
        super().setUp()
        super().setup_subsidy_mocks()
        self.mock_subsidy_client.list_subsidy_transactions.return_value = {
            "results": [{"quantity": -1}],
            "aggregates": {"total_quantity": -1},
        }

    @ddt.data(
        # A good admin role, but for a context/customer that doesn't match anything we're aware of, gets you a 403.
        {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good learner role, but for a context/customer that doesn't match anything we're aware of, gets you a 403.
        {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good operator role, but for a context/customer that doesn't match anything we're aware of, gets you a 403.
        {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
    )
    def test_detail_view(self, role_context_dict):
        """
        Test that the detail view returns a 200 response code and the expected results of serialization.
        """
        # Set the JWT-based auth that we'll use for every request
        self.set_jwt_cookie([role_context_dict])

        request_kwargs = {'uuid': str(self.redeemable_policy.uuid)}

        # Test the retrieve endpoint
        response = self.client.get(reverse('api:v1:subsidy-access-policies-detail', kwargs=request_kwargs))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual({
            'access_method': 'direct',
            'active': True,
            'retired': False,
            'catalog_uuid': str(self.redeemable_policy.catalog_uuid),
            'display_name': self.redeemable_policy.display_name,
            'description': 'A generic description',
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'per_learner_enrollment_limit': self.redeemable_policy.per_learner_enrollment_limit,
            'per_learner_spend_limit': self.redeemable_policy.per_learner_spend_limit,
            'policy_type': 'PerLearnerEnrollmentCreditAccessPolicy',
            'spend_limit': 3,
            'subsidy_uuid': str(self.redeemable_policy.subsidy_uuid),
            'uuid': str(self.redeemable_policy.uuid),
            'subsidy_active_datetime': self.yesterday.isoformat(),
            'subsidy_expiration_datetime': self.tomorrow.isoformat(),
            'is_subsidy_active': True,
            'aggregates': {
                'amount_redeemed_usd_cents': 1,
                'amount_redeemed_usd': 0.01,
                'amount_allocated_usd_cents': 0,
                'amount_allocated_usd': 0.00,
                'spend_available_usd_cents': 2,
                'spend_available_usd': 0.02,
            },
            'assignment_configuration': None,
        }, response.json())

    @ddt.data(
        # A good admin role, but for a context/customer that doesn't match anything we're aware of, gets you a 403.
        {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good learner role, but for a context/customer that doesn't match anything we're aware of, gets you a 403.
        {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good operator role, but for a context/customer that doesn't match anything we're aware of, gets you a 403.
        {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
    )
    def test_assignment_policy_detail_view(self, role_context_dict):
        """
        Test that assignment-based policies serialize their related assignment configuration record.
        """
        self.set_jwt_cookie([role_context_dict])

        # Create a pair of AssignmentConfiguration + SubsidyAccessPolicy for the main test customer.
        assignment_configuration = AssignmentConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
        )
        assigned_learner_credit_policy = AssignedLearnerCreditAccessPolicyFactory(
            display_name='An assigned learner credit policy, for the test customer.',
            enterprise_customer_uuid=self.enterprise_uuid,
            active=True,
            assignment_configuration=assignment_configuration,
            spend_limit=1000000,
        )

        policy_kwargs = {'uuid': str(assigned_learner_credit_policy.uuid)}
        policy_detail_url = reverse('api:v1:subsidy-access-policies-detail', kwargs=policy_kwargs)

        policy_response = self.client.get(policy_detail_url)
        expected_config_response = {
            'uuid': str(assignment_configuration.uuid),
            'active': True,
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'subsidy_access_policy': str(assigned_learner_credit_policy.uuid),
        }
        assert policy_response.json()['assignment_configuration'] == expected_config_response

    @ddt.data(
        # A good admin role, but for a context/customer that doesn't match anything we're aware of, gets you a 403.
        {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good learner role, but for a context/customer that doesn't match anything we're aware of, gets you a 403.
        {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good operator role, but for a context/customer that doesn't match anything we're aware of, gets you a 403.
        {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
    )
    def test_list_view(self, role_context_dict):
        """
        Test that the list view returns a 200 response code and the expected (list) results of serialization.
        """
        # Set the JWT-based auth that we'll use for every request
        self.set_jwt_cookie([role_context_dict])

        # Test the retrieve endpoint
        response = self.client.get(
            reverse('api:v1:subsidy-access-policies-list'),
            {'enterprise_customer_uuid': str(self.enterprise_uuid),
             'active': True},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_json = response.json()
        self.assertEqual(response_json['count'], 2)

        expected_results = [
            {
                'access_method': 'direct',
                'active': True,
                'retired': False,
                'catalog_uuid': str(self.non_redeemable_policy.catalog_uuid),
                'display_name': self.non_redeemable_policy.display_name,
                'description': 'A generic description',
                'enterprise_customer_uuid': str(self.enterprise_uuid),
                'per_learner_enrollment_limit': self.non_redeemable_policy.per_learner_enrollment_limit,
                'per_learner_spend_limit': self.non_redeemable_policy.per_learner_spend_limit,
                'policy_type': 'PerLearnerEnrollmentCreditAccessPolicy',
                'spend_limit': 0,
                'subsidy_uuid': str(self.non_redeemable_policy.subsidy_uuid),
                'uuid': str(self.non_redeemable_policy.uuid),
                'subsidy_active_datetime': self.yesterday.isoformat(),
                'subsidy_expiration_datetime': self.tomorrow.isoformat(),
                'is_subsidy_active': True,
                'aggregates': {
                    'amount_redeemed_usd_cents': 1,
                    'amount_redeemed_usd': 0.01,
                    'amount_allocated_usd_cents': 0,
                    'amount_allocated_usd': 0.00,
                    'spend_available_usd_cents': 0,
                    'spend_available_usd': 0.00,
                },
                'assignment_configuration': None,
            },
            {
                'access_method': 'direct',
                'active': True,
                'retired': False,
                'catalog_uuid': str(self.redeemable_policy.catalog_uuid),
                'display_name': self.redeemable_policy.display_name,
                'description': 'A generic description',
                'enterprise_customer_uuid': str(self.enterprise_uuid),
                'per_learner_enrollment_limit': self.redeemable_policy.per_learner_enrollment_limit,
                'per_learner_spend_limit': self.redeemable_policy.per_learner_spend_limit,
                'policy_type': 'PerLearnerEnrollmentCreditAccessPolicy',
                'spend_limit': 3,
                'subsidy_uuid': str(self.redeemable_policy.subsidy_uuid),
                'uuid': str(self.redeemable_policy.uuid),
                'subsidy_active_datetime': self.yesterday.isoformat(),
                'subsidy_expiration_datetime': self.tomorrow.isoformat(),
                'is_subsidy_active': True,
                'aggregates': {
                    'amount_redeemed_usd_cents': 1,
                    'amount_redeemed_usd': 0.01,
                    'amount_allocated_usd_cents': 0,
                    'amount_allocated_usd': 0.00,
                    'spend_available_usd_cents': 2,
                    'spend_available_usd': 0.02,
                },
                'assignment_configuration': None,
            },
        ]

        sort_key = itemgetter('spend_limit')
        self.assertEqual(
            sorted(expected_results, key=sort_key),
            sorted(response_json['results'], key=sort_key),
        )

        # Test the retrieve endpoint for inactive policies
        response = self.client.get(
            reverse('api:v1:subsidy-access-policies-list'),
            {'enterprise_customer_uuid': str(self.enterprise_uuid),
             'active': False},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_json = response.json()
        self.assertEqual(response_json['count'], 0)
        self.assertEqual(response_json['results'], [])

        # Assert that we only call the subsidy service to list transaction
        # aggregates once per policy
        self.mock_subsidy_client.list_subsidy_transactions.assert_has_calls([
            call(
                subsidy_uuid=self.redeemable_policy.subsidy_uuid,
                subsidy_access_policy_uuid=self.redeemable_policy.uuid
            ),
            call(
                subsidy_uuid=self.non_redeemable_policy.subsidy_uuid,
                subsidy_access_policy_uuid=self.non_redeemable_policy.uuid,
            )
        ])

    @ddt.data(
        {
            'request_payload': {'reason': 'Peer Pressure.'},
            'expected_change_reason': 'Peer Pressure.',
        },
        {
            'request_payload': {'reason': ''},
            'expected_change_reason': None,
        },
        {
            'request_payload': {'reason': None},
            'expected_change_reason': None,
        },
        {
            'request_payload': {},
            'expected_change_reason': None,
        },
    )
    @ddt.unpack
    def test_destroy_view(self, request_payload, expected_change_reason):
        """
        Test that the destroy view performs a soft-delete and returns an appropriate response with 200 status code and
        the expected results of serialization.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        # Test the destroy endpoint
        response = self.client.delete(
            reverse('api:v1:subsidy-access-policies-detail', kwargs={'uuid': str(self.redeemable_policy.uuid)}),
            request_payload,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected_response = {
            'access_method': 'direct',
            'active': False,
            'retired': False,
            'catalog_uuid': str(self.redeemable_policy.catalog_uuid),
            'display_name': self.redeemable_policy.display_name,
            'description': 'A generic description',
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'per_learner_enrollment_limit': self.redeemable_policy.per_learner_enrollment_limit,
            'per_learner_spend_limit': self.redeemable_policy.per_learner_spend_limit,
            'policy_type': 'PerLearnerEnrollmentCreditAccessPolicy',
            'spend_limit': 3,
            'subsidy_uuid': str(self.redeemable_policy.subsidy_uuid),
            'uuid': str(self.redeemable_policy.uuid),
            'subsidy_active_datetime': self.yesterday.isoformat(),
            'subsidy_expiration_datetime': self.tomorrow.isoformat(),
            'is_subsidy_active': True,
            'aggregates': {
                'amount_redeemed_usd_cents': 1,
                'amount_redeemed_usd': 0.01,
                'amount_allocated_usd_cents': 0,
                'amount_allocated_usd': 0.00,
                'spend_available_usd_cents': 2,
                'spend_available_usd': 0.02,
            },
            'assignment_configuration': None,
        }
        self.assertEqual(expected_response, response.json())

        # Check that the latest history record for this policy contains the change reason provided via the API.
        self.redeemable_policy.refresh_from_db()
        assert self.redeemable_policy.history.order_by('-history_date').first().history_change_reason \
            == expected_change_reason

        # Test idempotency of the destroy endpoint.
        response = self.client.delete(
            reverse('api:v1:subsidy-access-policies-detail', kwargs={'uuid': str(self.redeemable_policy.uuid)}),
            request_payload,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(expected_response, response.json())

    @ddt.data(
        # Test sending a bunch of updates as a PATCH.
        {
            'is_patch': True,
            'request_payload': {
                'description': 'the new description',
                'display_name': 'new display_name',
                'active': True,
                'retired': True,
                'catalog_uuid': str(uuid4()),
                'subsidy_uuid': str(uuid4()),
                'access_method': AccessMethods.ASSIGNED,
                'spend_limit': None,
                'per_learner_spend_limit': 10000,
            },
        },
        # Test sending a bunch of updates as a PUT.
        {
            'is_patch': False,
            'request_payload': {
                'description': 'the new description',
                'display_name': 'new display_name',
                'active': True,
                'retired': True,
                'catalog_uuid': str(uuid4()),
                'subsidy_uuid': str(uuid4()),
                'access_method': AccessMethods.ASSIGNED,
                'spend_limit': None,
                'per_learner_spend_limit': 10000,
            },
        },
        # Test sending an empty string for `description`.
        {
            'is_patch': True,
            'request_payload': {
                'description': '',
            },
        }
    )
    @ddt.unpack
    def test_update_views(self, is_patch, request_payload):
        """
        Test that the update and partial_update views can modify certain
        fields of a policy record.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        policy_for_edit = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            display_name='old display_name',
            spend_limit=5,
            active=False,
        )

        action = self.client.patch if is_patch else self.client.put
        url = reverse(
            'api:v1:subsidy-access-policies-detail',
            kwargs={'uuid': str(policy_for_edit.uuid)}
        )
        response = action(url, data=request_payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_response = {
            # Fields that we officially support PATCHing.
            'access_method': policy_for_edit.access_method,
            'active': policy_for_edit.active,
            'retired': policy_for_edit.retired,
            'catalog_uuid': str(policy_for_edit.catalog_uuid),
            'display_name': policy_for_edit.display_name,
            'description': policy_for_edit.description,
            'per_learner_spend_limit': policy_for_edit.per_learner_spend_limit,
            'per_learner_enrollment_limit': policy_for_edit.per_learner_enrollment_limit,
            'spend_limit': policy_for_edit.spend_limit,
            'subsidy_uuid': str(policy_for_edit.subsidy_uuid),

            # All the rest of the fields that we do not support PATCHing.
            'uuid': str(policy_for_edit.uuid),
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'policy_type': 'PerLearnerSpendCreditAccessPolicy',
            'subsidy_active_datetime': self.yesterday.isoformat(),
            'subsidy_expiration_datetime': self.tomorrow.isoformat(),
            'is_subsidy_active': True,
            'aggregates': {
                'amount_redeemed_usd_cents': 1,
                'amount_redeemed_usd': 0.01,
                'amount_allocated_usd_cents': 0,
                'amount_allocated_usd': 0.00,
                'spend_available_usd_cents': 4,
                'spend_available_usd': 0.04,
            },
            'assignment_configuration': None,
        }
        expected_response.update(request_payload)
        self.assertEqual(expected_response, response.json())

    @ddt.data(
        {
            'enterprise_customer_uuid': str(uuid4()),
            'uuid': str(uuid4()),
            'policy_type': 'PerLearnerEnrollmentCapCreditAccessPolicy',
            'created': '1970-01-01 12:00:00Z',
            'modified': '1970-01-01 12:00:00Z',
            'nonsense_key': 'ship arriving too late to save a drowning witch',
        },
    )
    def test_update_views_fields_disallowed_for_update(self, request_payload):
        """
        Test that the update and partial_update views can NOT modify fields
        of a policy record that are not included in the update request serializer fields defintion.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        policy_for_edit = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            spend_limit=5,
            active=False,
        )
        url = reverse(
            'api:v1:subsidy-access-policies-detail',
            kwargs={'uuid': str(policy_for_edit.uuid)}
        )

        expected_unknown_keys = ", ".join(sorted(request_payload.keys()))

        # Test the PUT view
        response = self.client.put(url, data=request_payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.json(),
            {'non_field_errors': [f'Field(s) are not updatable: {expected_unknown_keys}']},
        )

        # Test the PATCH view
        response = self.client.patch(url, data=request_payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.assertEqual(
            response.json(),
            {'non_field_errors': [f'Field(s) are not updatable: {expected_unknown_keys}']},
        )

    @ddt.data(
        {
            'policy_class': PerLearnerSpendCapLearnerCreditAccessPolicyFactory,
            'request_payload': {
                'per_learner_enrollment_limit': 10,
            },
            'expected_error_message': 'must not define a per-learner enrollment limit',
        },
        {
            'policy_class': PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory,
            'request_payload': {
                'per_learner_spend_limit': 1000,
            },
            'expected_error_message': 'must not define a per-learner spend limit',
        },
    )
    @ddt.unpack
    def test_update_view_validates_fields_vs_policy_type(self, policy_class, request_payload, expected_error_message):
        """
        Test that the update view can NOT modify fields
        of a policy record that are relevant only to a different
        type of policy.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        self.maxDiff = None
        policy_for_edit = policy_class(
            enterprise_customer_uuid=self.enterprise_uuid,
            spend_limit=5,
            active=False,
        )
        url = reverse(
            'api:v1:subsidy-access-policies-detail',
            kwargs={'uuid': str(policy_for_edit.uuid)}
        )

        response = self.client.put(url, data=request_payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(expected_error_message, response.json()[0])


@ddt.ddt
class TestAdminPolicyCreateView(CRUDViewTestMixin, APITestWithMocks):
    """
    Test the create view for subsidy access policy records.
    This tests both the deprecated viewset and the preferred
    ``SubsidyAccessPolicyViewSet`` implementation.
    """

    def setUp(self):
        super().setUp()
        super().setup_subsidy_mocks()

    @ddt.data(
        {
            'policy_type': PolicyTypes.PER_LEARNER_ENROLLMENT_CREDIT,
            'extra_fields': {
                'per_learner_enrollment_limit': None,
            },
            'expected_response_code': status.HTTP_201_CREATED,
            'expected_error_keywords': [],
        },
        {
            'policy_type': PolicyTypes.PER_LEARNER_ENROLLMENT_CREDIT,
            'extra_fields': {
                'per_learner_enrollment_limit': 10,
            },
            'expected_response_code': status.HTTP_201_CREATED,
            'expected_error_keywords': [],
        },
        {
            'policy_type': PolicyTypes.PER_LEARNER_SPEND_CREDIT,
            'extra_fields': {
                'per_learner_spend_limit': None,
            },
            'expected_response_code': status.HTTP_201_CREATED,
            'expected_error_keywords': [],
        },
        {
            'policy_type': PolicyTypes.PER_LEARNER_SPEND_CREDIT,
            'extra_fields': {
                'per_learner_spend_limit': 30000,
            },
            'expected_response_code': status.HTTP_201_CREATED,
            'expected_error_keywords': [],
        },
        {
            'policy_type': PolicyTypes.PER_LEARNER_ENROLLMENT_CREDIT,
            'extra_fields': {
                'per_learner_spend_limit': 30000,
            },
            'expected_response_code': status.HTTP_400_BAD_REQUEST,
            'expected_error_keywords': ['must not define a per-learner spend limit'],
        },
        {
            'policy_type': PolicyTypes.PER_LEARNER_ENROLLMENT_CREDIT,
            'extra_fields': {
                'per_learner_spend_limit': 30000,
                'per_learner_enrollment_limit': 10,
            },
            'expected_response_code': status.HTTP_400_BAD_REQUEST,
            'expected_error_keywords': ['must not define a per-learner spend limit'],
        },
        {
            'policy_type': PolicyTypes.PER_LEARNER_SPEND_CREDIT,
            'extra_fields': {
                'per_learner_enrollment_limit': 10,
            },
            'expected_response_code': status.HTTP_400_BAD_REQUEST,
            'expected_error_keywords': ['must not define a per-learner enrollment limit'],
        },
        {
            'policy_type': PolicyTypes.PER_LEARNER_SPEND_CREDIT,
            'extra_fields': {
                'per_learner_enrollment_limit': 10,
                'per_learner_spend_limit': 30000,
            },
            'expected_response_code': status.HTTP_400_BAD_REQUEST,
            'expected_error_keywords': ['must not define a per-learner enrollment limit'],
        },
    )
    @ddt.unpack
    def test_create_view(self, policy_type, extra_fields, expected_response_code, expected_error_keywords):
        """
        Test the (deprecated) policy create view.  make sure "extra" fields which pertain to the specific policy type
        are correctly validated for existence/non-existence.
        """
        # Set the JWT-based auth that we'll use for every request
        self.set_jwt_cookie([
            {
                'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE,
                'context': str(TEST_ENTERPRISE_UUID),
            },
        ])

        # Test the create endpoint
        payload = {
            'policy_type': policy_type,
            'display_name': 'created policy',
            'description': 'test description',
            'active': True,
            'retired': False,
            'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID),
            'catalog_uuid': str(uuid4()),
            'subsidy_uuid': str(uuid4()),
            'access_method': AccessMethods.DIRECT,
            'spend_limit': None,
            'subsidy_active_datetime': self.yesterday.isoformat(),
            'subsidy_expiration_datetime': self.tomorrow.isoformat(),
            'is_subsidy_active': True,
        }
        payload.update(extra_fields)
        response = self.client.post(SUBSIDY_ACCESS_POLICY_LIST_ENDPOINT, payload)
        assert response.status_code == expected_response_code

        if expected_response_code == status.HTTP_201_CREATED:
            response_json = response.json()
            del response_json['uuid']
            expected_response = payload.copy()
            expected_response.setdefault("per_learner_enrollment_limit")
            expected_response.setdefault("per_learner_spend_limit")
            assert response_json == expected_response
        elif expected_response_code == status.HTTP_400_BAD_REQUEST:
            for expected_error_keyword in expected_error_keywords:
                assert expected_error_keyword in response.content.decode("utf-8")

    @ddt.data(
        {
            'policy_type': PolicyTypes.PER_LEARNER_SPEND_CREDIT,
            'extra_fields': {
                'per_learner_spend_limit': 30000,
            },
            'expected_response_code': status.HTTP_201_CREATED,
        }
    )
    @ddt.unpack
    def test_idempotent_create_view(self, policy_type, extra_fields, expected_response_code):
        """
        Test the (deprecated) policy create view's idempotency.
        """
        # Set the JWT-based auth that we'll use for every request
        self.set_jwt_cookie([
            {
                'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE,
                'context': str(TEST_ENTERPRISE_UUID),
            },
        ])

        # Test the retrieve endpoint
        enterprise_customer_uuid = str(TEST_ENTERPRISE_UUID)
        catalog_uuid = str(uuid4())
        subsidy_uuid = str(uuid4())
        payload = {
            'policy_type': policy_type,
            'display_name': 'new policy',
            'description': 'test description',
            'active': True,
            'retired': False,
            'enterprise_customer_uuid': enterprise_customer_uuid,
            'catalog_uuid': catalog_uuid,
            'subsidy_uuid': subsidy_uuid,
            'access_method': AccessMethods.DIRECT,
            'spend_limit': None,
            'subsidy_active_datetime': self.yesterday.isoformat(),
            'subsidy_expiration_datetime': self.tomorrow.isoformat(),
            'is_subsidy_active': True,
        }
        payload.update(extra_fields)
        response = self.client.post(SUBSIDY_ACCESS_POLICY_LIST_ENDPOINT, payload)
        assert response.status_code == expected_response_code

        if expected_response_code == status.HTTP_201_CREATED:
            response_json = response.json()
            del response_json['uuid']
            expected_response = payload.copy()
            expected_response.setdefault("per_learner_enrollment_limit")
            expected_response.setdefault("per_learner_spend_limit")
            assert response_json == expected_response

        # Test idempotency
        response = self.client.post(SUBSIDY_ACCESS_POLICY_LIST_ENDPOINT, payload)
        duplicate_status_code = status.HTTP_200_OK

        assert response.status_code == duplicate_status_code

        if response.status_code == status.HTTP_200_OK:
            response_json = response.json()
            del response_json['uuid']
            expected_response = payload.copy()
            expected_response.setdefault("per_learner_enrollment_limit")
            expected_response.setdefault("per_learner_spend_limit")
            assert response_json == expected_response


@ddt.ddt
class TestPolicyRedemptionAuthNAndPermissionChecks(APITestWithMocks):
    """
    Tests Authentication and Permission checking for Subsidy Access Policy views.
    Specifically, test all the non-happy-path conditions.
    """
    def setUp(self):
        super().setUp()
        self.enterprise_uuid = TEST_ENTERPRISE_UUID
        self.redeemable_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            spend_limit=3,
        )
        self.non_redeemable_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory()

    @ddt.data(
        # A role that's not mapped to any feature perms will get you a 403.
        (
            {'system_wide_role': 'some-other-role', 'context': str(TEST_ENTERPRISE_UUID)},
            status.HTTP_403_FORBIDDEN,
        ),
        # The right role, but in a context/customer we don't have, get's you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(uuid4())},
            status.HTTP_403_FORBIDDEN,
        ),
        # A learner role is also fine, but in a context/customer we don't have, get's you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(uuid4())},
            status.HTTP_403_FORBIDDEN,
        ),
        # An operator role is fine, too, but in a context/customer we don't have, get's you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(uuid4())},
            status.HTTP_403_FORBIDDEN,
        ),
        # No JWT based auth, no soup for you.
        (
            None,
            status.HTTP_401_UNAUTHORIZED,
        ),
    )
    @ddt.unpack
    def test_policy_redemption_forbidden_requests(self, role_context_dict, expected_response_code):
        """
        Tests that we get expected 403s for all of the policy redemption endpoints.
        """
        # Set the JWT-based auth that we'll use for every request
        if role_context_dict:
            self.set_jwt_cookie([role_context_dict])

        # The redeem endpoint
        url = reverse('api:v1:policy-redemption-redeem', kwargs={'policy_uuid': self.redeemable_policy.uuid})
        payload = {
            'lms_user_id': 1234,
            'content_key': 'course-v1:edX+edXPrivacy101+3T2020',
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, expected_response_code)

        # The credits_available endpoint
        query_params = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'lms_user_id': 1234,
        }
        response = self.client.get(reverse('api:v1:policy-redemption-credits-available'), query_params)
        self.assertEqual(response.status_code, expected_response_code)

        # The can_redeem endpoint
        url = reverse(
            "api:v1:policy-redemption-can-redeem",
            kwargs={"enterprise_customer_uuid": self.enterprise_uuid},
        )
        query_params = {
            'content_key': ['course-v1:edX+edXPrivacy101+3T2020', 'course-v1:edX+edXPrivacy101+3T2020_2'],
        }
        response = self.client.get(url, query_params)
        self.assertEqual(response.status_code, expected_response_code)


@ddt.ddt
class TestSubsidyAccessPolicyRedeemViewset(APITestWithMocks):
    """
    Tests for SubsidyAccessPolicyRedeemViewset.
    """

    def setUp(self):
        super().setUp()

        self.enterprise_uuid = '12aacfee-8ffa-4cb3-bed1-059565a57f06'

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': self.enterprise_uuid,
        }])

        self.redeemable_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            spend_limit=500000,
        )
        self.non_redeemable_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory()

        self.subsidy_access_policy_redeem_endpoint = reverse(
            'api:v1:policy-redemption-redeem',
            kwargs={'policy_uuid': self.redeemable_policy.uuid}
        )
        self.subsidy_access_policy_credits_available_endpoint = reverse('api:v1:policy-redemption-credits-available')
        self.subsidy_access_policy_can_redeem_endpoint = reverse(
            "api:v1:policy-redemption-can-redeem",
            kwargs={"enterprise_customer_uuid": self.enterprise_uuid},
        )
        self.setup_mocks()

    def setup_mocks(self):
        """
        Setup mocks for different api clients.
        """
        subsidy_client_path = (
            'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client'
        )
        subsidy_client_patcher = mock.patch(subsidy_client_path)
        self.subsidy_client = subsidy_client_patcher.start()
        self.subsidy_client.can_redeem.return_value = {
            'can_redeem': True,
            'active': True,
            'content_price': 0,
            'unit': 'usd_cents',
            'all_transactions': [],
        }
        self.subsidy_client.list_subsidy_transactions.return_value = {"results": [], "aggregates": {}}
        self.subsidy_client.create_subsidy_transaction.side_effect = (
            NotImplementedError("unit test must override create_subsidy_transaction to use.")
        )

        path_prefix = 'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.'

        contains_key_patcher = mock.patch(path_prefix + 'catalog_contains_content_key')
        self.mock_contains_key = contains_key_patcher.start()
        self.mock_contains_key.return_value = True

        get_content_metadata_patcher = mock.patch(path_prefix + 'get_content_metadata')
        self.mock_get_content_metadata = get_content_metadata_patcher.start()
        self.mock_get_content_metadata.return_value = {}

        lms_client_patcher = mock.patch('enterprise_access.apps.subsidy_access_policy.models.LmsApiClient')
        lms_client = lms_client_patcher.start()
        self.lms_client_instance = lms_client.return_value
        self.lms_client_instance.enterprise_contains_learner.return_value = True

        self.addCleanup(lms_client_patcher.stop)
        self.addCleanup(subsidy_client_patcher.stop)
        self.addCleanup(contains_key_patcher.stop)
        self.addCleanup(get_content_metadata_patcher.stop)

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.get_and_cache_transactions_for_learner')
    def test_redeem_policy(self, mock_transactions_cache_for_learner):  # pylint: disable=unused-argument
        """
        Verify that SubsidyAccessPolicyRedeemViewset redeem endpoint works as expected
        """
        self.mock_get_content_metadata.return_value = {'content_price': 123}
        mock_transaction_record = {
            'uuid': str(uuid4()),
            'state': TransactionStateChoices.COMMITTED,
            'other': True,
        }
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.side_effect = None
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.return_value = mock_transaction_record
        payload = {
            'lms_user_id': 1234,
            'content_key': 'course-v1:edX+edXPrivacy101+3T2020',
        }

        response = self.client.post(self.subsidy_access_policy_redeem_endpoint, payload)

        response_json = self.load_json(response.content)
        assert response_json == mock_transaction_record
        self.mock_get_content_metadata.assert_called_once_with(payload['content_key'])
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.assert_called_once_with(
            subsidy_uuid=str(self.redeemable_policy.subsidy_uuid),
            lms_user_id=payload['lms_user_id'],
            content_key=payload['content_key'],
            subsidy_access_policy_uuid=str(self.redeemable_policy.uuid),
            metadata=None,
            idempotency_key=create_idempotency_key_for_transaction(
                subsidy_uuid=str(self.redeemable_policy.subsidy_uuid),
                lms_user_id=payload['lms_user_id'],
                content_key=payload['content_key'],
                subsidy_access_policy_uuid=str(self.redeemable_policy.uuid),
                historical_redemptions_uuids=[],
            ),
        )

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.get_and_cache_transactions_for_learner')
    def test_redeem_policy_with_metadata(self, mock_transactions_cache_for_learner):  # pylint: disable=unused-argument
        """
        Verify that SubsidyAccessPolicyRedeemViewset redeem endpoint works as expected
        """
        self.mock_get_content_metadata.return_value = {'content_price': 123}
        mock_transaction_record = {
            'uuid': str(uuid4()),
            'status': 'committed',
            'other': True,
        }
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.side_effect = None
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.return_value = mock_transaction_record
        payload = {
            'lms_user_id': 1234,
            'content_key': 'course-v1:edX+edXPrivacy101+3T2020',
            'metadata': {
                'geag_first_name': 'John'
            }
        }

        response = self.client.post(self.subsidy_access_policy_redeem_endpoint, payload)

        response_json = self.load_json(response.content)
        assert response_json == mock_transaction_record
        self.mock_get_content_metadata.assert_called_once_with(payload['content_key'])
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.assert_called_once_with(
            subsidy_uuid=str(self.redeemable_policy.subsidy_uuid),
            lms_user_id=payload['lms_user_id'],
            content_key=payload['content_key'],
            subsidy_access_policy_uuid=str(self.redeemable_policy.uuid),
            metadata=payload['metadata'],
            idempotency_key=create_idempotency_key_for_transaction(
                subsidy_uuid=str(self.redeemable_policy.subsidy_uuid),
                lms_user_id=payload['lms_user_id'],
                content_key=payload['content_key'],
                subsidy_access_policy_uuid=str(self.redeemable_policy.uuid),
                historical_redemptions_uuids=[],
            ),
        )

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.get_and_cache_transactions_for_learner')
    @ddt.data(
        {
            "existing_transaction_state": None,
            "existing_transaction_reversed": None,
            "idempotency_key_versioned": False,
        },
        {
            "existing_transaction_state": TransactionStateChoices.CREATED,
            "existing_transaction_reversed": False,
            "idempotency_key_versioned": False,
        },
        {
            "existing_transaction_state": TransactionStateChoices.PENDING,
            "existing_transaction_reversed": False,
            "idempotency_key_versioned": False,
        },
        {
            "existing_transaction_state": TransactionStateChoices.COMMITTED,
            "existing_transaction_reversed": False,
            "idempotency_key_versioned": False,
        },
        {
            "existing_transaction_state": TransactionStateChoices.COMMITTED,
            "existing_transaction_reversed": True,
            "idempotency_key_versioned": True,
        },
        {
            "existing_transaction_state": TransactionStateChoices.FAILED,
            "existing_transaction_reversed": False,
            "idempotency_key_versioned": True,
        },
    )
    @ddt.unpack
    def test_redeem_policy_redemption_idempotency_key_versions(
        self,
        mock_transactions_cache_for_learner,
        existing_transaction_state,
        existing_transaction_reversed,
        idempotency_key_versioned,
    ):  # pylint: disable=unused-argument
        """
        Verify that SubsidyAccessPolicyRedeemViewset redeem endpoint sends either a baseline or a versioned idempotency
        key, depending on any existing transactions.
        """
        self.mock_get_content_metadata.return_value = {'content_price': 5000}

        lms_user_id = 1234
        content_key = 'course-v1:edX+edXPrivacy101+3T2020'
        historical_redemption_uuid = str(uuid4())
        baseline_idempotency_key = create_idempotency_key_for_transaction(
            subsidy_uuid=str(self.redeemable_policy.subsidy_uuid),
            lms_user_id=lms_user_id,
            content_key=content_key,
            subsidy_access_policy_uuid=str(self.redeemable_policy.uuid),
            historical_redemptions_uuids=[],
        )
        existing_transactions = []
        if existing_transaction_state:
            existing_transaction = {
                'uuid': historical_redemption_uuid,
                'state': existing_transaction_state,
                'idempotency_key': baseline_idempotency_key,
                'reversal': None,
            }
            if existing_transaction_reversed:
                existing_transaction['reversal'] = {'state': TransactionStateChoices.COMMITTED}
            existing_transactions.append(existing_transaction)
        self.redeemable_policy.subsidy_client.can_redeem.return_value = {
            'can_redeem': True,
            'active': True,
            'content_price': 5000,
            'unit': 'usd_cents',
            'all_transactions': existing_transactions,
        }
        mock_transaction_record = {
            'uuid': str(uuid4()),
            'state': TransactionStateChoices.COMMITTED,
            'other': True,
        }
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.side_effect = None
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.return_value = mock_transaction_record

        payload = {
            'lms_user_id': lms_user_id,
            'content_key': content_key,
        }
        response = self.client.post(self.subsidy_access_policy_redeem_endpoint, payload)

        assert response.status_code == status.HTTP_200_OK

        new_idempotency_key_sent = \
            self.redeemable_policy.subsidy_client.create_subsidy_transaction.call_args.kwargs['idempotency_key']
        if idempotency_key_versioned:
            assert new_idempotency_key_sent != baseline_idempotency_key
        else:
            assert new_idempotency_key_sent == baseline_idempotency_key

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.get_and_cache_transactions_for_learner')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_record')
    @ddt.data(
        {
            'is_subsidy_active': True,
            'has_subsidy_balance_remaining': True,
            'is_learned_linked': True,
            'has_learner_exceed_spend_cap': False,
        },
        {
            'is_subsidy_active': True,
            'has_subsidy_balance_remaining': True,
            'is_learned_linked': False,
            'has_learner_exceed_spend_cap': False,
        },
        {
            'is_subsidy_active': True,
            'has_subsidy_balance_remaining': True,
            'is_learned_linked': True,
            'has_learner_exceed_spend_cap': True,
        },
        {
            'is_subsidy_active': False,
            'has_subsidy_balance_remaining': True,
            'is_learned_linked': True,
            'has_learner_exceed_spend_cap': False,
        },
        {
            'is_subsidy_active': True,
            'has_subsidy_balance_remaining': False,
            'is_learned_linked': True,
            'has_learner_exceed_spend_cap': False,
        },
        {
            'is_subsidy_active': False,
            'has_subsidy_balance_remaining': False,
            'is_learned_linked': True,
            'has_learner_exceed_spend_cap': False,
        },
    )
    @ddt.unpack
    def test_credits_available_endpoint(
        self,
        mock_subsidy_record,
        mock_transactions_cache_for_learner,
        is_subsidy_active,
        has_subsidy_balance_remaining,
        is_learned_linked,
        has_learner_exceed_spend_cap,
    ):
        """
        Verify that SubsidyAccessPolicyViewset credits_available returns credit based policies with redeemable credit.
        """
        # The following policy should never be returned as it's inactive.
        PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            active=False,
        )
        # The following policy should never be returned as it has redeemability disabled.
        PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            retired=True,
        )
        # The following policy should never be returned as it's had more spend than the `spend_limit`.
        PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            per_learner_spend_limit=5,
            spend_limit=100,
        )

        # Create redeemable policies
        enroll_cap_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            per_learner_enrollment_limit=5,
        )
        spend_cap_policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            per_learner_spend_limit=(5 if has_learner_exceed_spend_cap else 1000),
        )

        mock_transaction_record = {
            'uuid': str(uuid4()),
            'state': TransactionStateChoices.COMMITTED,
            'content_key': 'something',
            'subsidy_access_policy_uuid': str(self.redeemable_policy.uuid),
            'quantity': -200,
            'other': True,
        }
        mock_transaction_record_second_policy = {
            'uuid': str(uuid4()),
            'state': TransactionStateChoices.COMMITTED,
            'content_key': 'something',
            'subsidy_access_policy_uuid': str(spend_cap_policy.uuid),
            'quantity': -200,
            'other': True,
        }
        mock_total_quantity_transactions = mock_transaction_record['quantity'] + \
            mock_transaction_record_second_policy['quantity']

        mock_transactions_cache_for_learner.return_value = {
            'transactions': [
                mock_transaction_record,
                mock_transaction_record_second_policy,
            ],
            'aggregates': {
                'total_quantity': mock_total_quantity_transactions,
            },
        }
        self.subsidy_client.list_subsidy_transactions.return_value = {
            'results': [
                mock_transaction_record,
                mock_transaction_record_second_policy,
            ],
            'aggregates': {
                'total_quantity': mock_total_quantity_transactions,
            }
        }
        mock_subsidy_record.return_value = {
            'uuid': str(uuid4()),
            'title': 'Test Subsidy',
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'expiration_datetime': '2030-01-01 12:00:00Z',
            'active_datetime': '2020-01-01 12:00:00Z',
            'current_balance': '5000' if has_subsidy_balance_remaining else '0',
            'is_active': is_subsidy_active,
        }
        self.lms_client_instance.enterprise_contains_learner.return_value = is_learned_linked

        query_params = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'lms_user_id': 1234,
        }
        response = self.client.get(self.subsidy_access_policy_credits_available_endpoint, query_params)

        response_json = response.json()

        if is_subsidy_active and has_subsidy_balance_remaining and is_learned_linked:
            # the above generic checks passed, now verify the specific policy-type specific checks.
            if has_learner_exceed_spend_cap:
                # The spend cap policy should not be returned as the learner has exceeded the spend cap.
                assert len(response_json) == 2
                redeemable_policy_uuids = {self.redeemable_policy.uuid, enroll_cap_policy.uuid}
                actual_uuids = {UUID(policy['uuid']) for policy in response_json}
                self.assertEqual(redeemable_policy_uuids, actual_uuids)
            else:
                # All policy-specific checks are complete/passing, assert that all 3 expected
                # policies are returned. self.redeemable_policy, along with the 2 instances created
                # from factories above, should give us a total of 3 policy records with credits
                # available. The inactive policy created above should not be returned. The policy with
                # a spend limit that's been exceeded should not be returned.
                assert len(response_json) == 3
                redeemable_policy_uuids = {self.redeemable_policy.uuid, enroll_cap_policy.uuid, spend_cap_policy.uuid}
                actual_uuids = {UUID(policy['uuid']) for policy in response_json}
                self.assertEqual(redeemable_policy_uuids, actual_uuids)
        else:
            # with an inactive (i.e., expired, not yet started) subsidy, we should get no records back.
            assert len(response_json) == 0

    @mock.patch('enterprise_access.apps.api.serializers.subsidy_access_policy.get_content_metadata_for_assignments')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.get_and_cache_transactions_for_learner')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_record')
    def test_credits_available_endpoint_with_content_assignments(
        self,
        mock_subsidy_record,
        mock_transactions_cache_for_learner,  # pylint: disable=unused-argument
        mock_get_metadata,
    ):
        """
        Verify that SubsidyAccessPolicyViewset credits_available returns learner content assignments for assigned
        learner credit access policies.
        """
        content_key = 'demoX'
        content_title = 'edx: Demo 101'
        content_price_cents = 100
        # Create a pair of AssignmentConfiguration + SubsidyAccessPolicy for the main test customer.
        assignment_configuration = AssignmentConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
        )
        AssignedLearnerCreditAccessPolicyFactory(
            display_name='An assigned learner credit policy, for the test customer.',
            enterprise_customer_uuid=self.enterprise_uuid,
            active=True,
            assignment_configuration=assignment_configuration,
            spend_limit=1000000,
        )
        assignment1 = LearnerContentAssignmentFactory.create(
            assignment_configuration=assignment_configuration,
            learner_email='alice@foo.com',
            lms_user_id=1234,
            content_key=content_key,
            content_title=content_title,
            content_quantity=-content_price_cents,
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
        )
        action = assignment1.add_successful_linked_action()

        # Implicitly tests that this response only includes allocated assignments
        LearnerContentAssignmentFactory.create(
            assignment_configuration=assignment_configuration,
            learner_email='bob@foo.com',
            lms_user_id=12345,
            content_key=content_key,
            content_title=content_title,
            content_quantity=-content_price_cents,
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
        )
        mock_subsidy_record.return_value = {
            'uuid': str(uuid4()),
            'title': 'Test Subsidy',
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'expiration_datetime': '2030-01-01 12:00:00Z',
            'active_datetime': '2020-01-01 12:00:00Z',
            'current_balance': '5000',
            'is_active': True,
        }
        self.lms_client_instance.enterprise_contains_learner.return_value = True
        query_params = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'lms_user_id': 1234,
        }
        # See LearnerContentAssignmentWithContentMetadataResponseSerializer
        # for what we expect to be in the response payload w.r.t. content metadata.
        mock_get_metadata.return_value = {
            content_key: {
                'key': content_key,
                'normalized_metadata': {
                    'start_date': '2020-01-01 12:00:00Z',
                    'end_date': '2022-01-01 12:00:00Z',
                    'enroll_by_date': '2021-01-01 12:00:00Z',
                    'content_price': 123,
                },
                'course_type': 'verified-audit',
                'owners': [
                    {'name': 'Smart Folks', 'logo_image_url': 'http://pictures.yes'},
                ],
            },
        }

        response = self.client.get(self.subsidy_access_policy_credits_available_endpoint, query_params)

        response_json = response.json()
        self.assertEqual(len(response_json[0]['learner_content_assignments']), 1)
        expected_learner_content_assignment = {
            'uuid': str(assignment1.uuid),
            'assignment_configuration': str(assignment_configuration.uuid),
            'learner_email': 'alice@foo.com',
            'lms_user_id': 1234,
            'content_key': content_key,
            'content_title': content_title,
            'content_quantity': -content_price_cents,
            'state': LearnerContentAssignmentStateChoices.ALLOCATED,
            'transaction_uuid': None,
            'last_notification_at': None,
            'actions': [
                {
                    'created': action.created.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    'modified': action.modified.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    'uuid': str(action.uuid),
                    'action_type': 'learner_linked',
                    'completed_at': action.completed_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    'error_reason': None
                }
            ],
            'content_metadata': {
                'start_date': '2020-01-01 12:00:00Z',
                'end_date': '2022-01-01 12:00:00Z',
                'enroll_by_date': '2021-01-01 12:00:00Z',
                'content_price': 123,
                'course_type': 'verified-audit',
                'partners': [
                    {'name': 'Smart Folks', 'logo_image_url': 'http://pictures.yes'},
                ],
            },
        }
        self.assertEqual(response_json[0]['learner_content_assignments'][0], expected_learner_content_assignment)


class BaseCanRedeemTestMixin:
    """
    Mixin to help with customer data, JWT cookies, and mock setup
    for testing can-redeem view.
    """
    def setUp(self):
        super().setUp()

        self.enterprise_uuid = '12aacfee-8ffa-4cb3-bed1-059565a57f06'

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': self.enterprise_uuid,
        }])
        self.subsidy_access_policy_can_redeem_endpoint = reverse(
            "api:v1:policy-redemption-can-redeem",
            kwargs={"enterprise_customer_uuid": self.enterprise_uuid},
        )
        self.setup_mocks()

    def setup_mocks(self):
        """
        Setup mocks for different api clients.
        """
        subsidy_client_path = (
            'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client'
        )
        subsidy_client_patcher = mock.patch(subsidy_client_path)
        subsidy_client = subsidy_client_patcher.start()
        subsidy_client.can_redeem.return_value = {
            'can_redeem': True,
            'active': True,
            'content_price': 5000,
            'unit': 'usd_cents',
            'all_transactions': [],
        }
        subsidy_client.list_subsidy_transactions.return_value = {"results": [], "aggregates": {}}
        subsidy_client.create_subsidy_transaction.side_effect = (
            NotImplementedError("unit test must override create_subsidy_transaction to use.")
        )

        path_prefix = 'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.'

        contains_key_patcher = mock.patch(path_prefix + 'catalog_contains_content_key')
        self.mock_contains_key = contains_key_patcher.start()
        self.mock_contains_key.return_value = True

        get_content_metadata_patcher = mock.patch(path_prefix + 'get_content_metadata')
        self.mock_get_content_metadata = get_content_metadata_patcher.start()
        self.mock_get_content_metadata.return_value = {}

        transactions_for_learner_patcher = mock.patch(path_prefix + 'transactions_for_learner')
        self.mock_policy_transactions_for_learner = transactions_for_learner_patcher.start()
        self.mock_policy_transactions_for_learner.return_value = {
            'transactions': [],
            'aggregates': {'total_quantity': 0},
        }

        lms_client_patcher = mock.patch('enterprise_access.apps.subsidy_access_policy.models.LmsApiClient')
        lms_client = lms_client_patcher.start()
        lms_client_instance = lms_client.return_value
        lms_client_instance.enterprise_contains_learner.return_value = True

        self.addCleanup(lms_client_patcher.stop)
        self.addCleanup(subsidy_client_patcher.stop)
        self.addCleanup(contains_key_patcher.stop)
        self.addCleanup(get_content_metadata_patcher.stop)
        self.addCleanup(transactions_for_learner_patcher.stop)


@ddt.ddt
class TestSubsidyAccessPolicyCanRedeemView(BaseCanRedeemTestMixin, APITestWithMocks):
    """
    Tests for the can-redeem view
    """

    def setUp(self):
        super().setUp()

        self.redeemable_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            spend_limit=500000,
        )
        self.non_redeemable_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory()

    def test_can_redeem_policy_missing_params(self):
        """
        Test that the can_redeem endpoint returns an access policy when one is redeemable.
        """
        self.redeemable_policy.subsidy_client.list_subsidy_transactions.return_value = {
            'results': [],
            'aggregates': {
                'total_quantity': 0,
            },
        }
        query_params = {}  # Test what happens when we fail to supply a list of content_keys.
        response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"content_key": ["This field is required."]}

    @mock.patch('enterprise_access.apps.subsidy_access_policy.subsidy_api.get_and_cache_transactions_for_learner')
    def test_can_redeem_policy(self, mock_transactions_cache_for_learner):
        """
        Test that the can_redeem endpoint returns an access policy when one is redeemable.
        """
        mock_transactions_cache_for_learner.return_value = {
            'transactions': [],
            'aggregates': {
                'total_quantity': 0,
            },
        }
        test_content_key_1 = "course-v1:edX+edXPrivacy101+3T2020"
        test_content_key_2 = "course-v1:edX+edXPrivacy101+3T2020_2"
        test_content_key_1_metadata_price = 29900
        test_content_key_2_metadata_price = 81900
        test_content_key_1_usd_price = 299
        test_content_key_2_usd_price = 819
        test_content_key_1_cents_price = 29900
        test_content_key_2_cents_price = 81900

        def mock_get_subsidy_content_data(*args):
            if test_content_key_1 in args:
                return {
                    "content_uuid": str(uuid4()),
                    "content_key": test_content_key_1,
                    "source": "edX",
                    "content_price": test_content_key_1_metadata_price,
                }
            elif test_content_key_2 in args:
                return {
                    "content_uuid": str(uuid4()),
                    "content_key": test_content_key_2,
                    "source": "edX",
                    "content_price": test_content_key_2_metadata_price,
                }
            else:
                return {}

        self.mock_get_content_metadata.side_effect = mock_get_subsidy_content_data

        with mock.patch(
            'enterprise_access.apps.subsidy_access_policy.content_metadata_api.get_and_cache_content_metadata',
            side_effect=mock_get_subsidy_content_data,
        ):
            query_params = {'content_key': [test_content_key_1, test_content_key_2]}
            response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)

        assert response.status_code == status.HTTP_200_OK
        response_list = response.json()

        # Make sure we got responses for all two content_keys requested.
        assert len(response_list) == 2

        # Check the response for the first content_key given.
        assert response_list[0]["content_key"] == test_content_key_1
        assert response_list[0]["list_price"] == {
            "usd": test_content_key_1_usd_price,
            "usd_cents": test_content_key_1_cents_price,
        }
        assert len(response_list[0]["redemptions"]) == 0
        assert response_list[0]["has_successful_redemption"] is False
        assert response_list[0]["redeemable_subsidy_access_policy"]["uuid"] == str(self.redeemable_policy.uuid)
        assert response_list[0]["can_redeem"] is True
        assert len(response_list[0]["reasons"]) == 0

        # Check the response for the second content_key given.
        assert response_list[1]["content_key"] == test_content_key_2
        assert response_list[1]["list_price"] == {
            "usd": test_content_key_2_usd_price,
            "usd_cents": test_content_key_2_cents_price,
        }
        assert len(response_list[1]["redemptions"]) == 0
        assert response_list[1]["has_successful_redemption"] is False
        assert response_list[1]["redeemable_subsidy_access_policy"]["uuid"] == str(self.redeemable_policy.uuid)
        assert response_list[1]["can_redeem"] is True
        assert len(response_list[1]["reasons"]) == 0

    @mock.patch('enterprise_access.apps.subsidy_access_policy.subsidy_api.get_and_cache_transactions_for_learner')
    @mock.patch('enterprise_access.apps.api.v1.views.subsidy_access_policy.LmsApiClient', return_value=mock.MagicMock())
    @ddt.data(
        {"has_admin_users": True},
        {"has_admin_users": False},
    )
    @ddt.unpack
    def test_can_redeem_policy_none_redeemable(
        self, mock_lms_client, mock_transactions_cache_for_learner, has_admin_users
    ):
        """
        Test that the can_redeem endpoint returns reasons for why each non-redeemable policy failed.
        """
        slug = 'sluggy'
        admin_email = 'edx@example.org'
        mock_lms_client().get_enterprise_customer_data.return_value = {
            'slug': slug,
            'admin_users': [{'email': admin_email}] if has_admin_users else [],
        }

        mock_transactions_cache_for_learner.return_value = {
            'transactions': [],
            'aggregates': {
                'total_quantity': 0,
            },
        }
        self.redeemable_policy.subsidy_client.can_redeem.return_value = {
            'can_redeem': False,
            'active': True,
            'content_price': 5000,  # value is ignored.
            'unit': 'usd_cents',
            'all_transactions': [],
        }
        test_content_key_1 = "course-v1:edX+edXPrivacy101+3T2020"
        test_content_key_2 = "course-v1:edX+edXPrivacy101+3T2020_2"
        test_content_key_1_metadata_price = 29900
        test_content_key_2_metadata_price = 81900

        def mock_get_subsidy_content_data(*args, **kwargs):
            if test_content_key_1 in args:
                return {
                    "content_uuid": str(uuid4()),
                    "content_key": test_content_key_1,
                    "source": "edX",
                    "content_price": test_content_key_1_metadata_price,
                }
            elif test_content_key_2 in args:
                return {
                    "content_uuid": str(uuid4()),
                    "content_key": test_content_key_2,
                    "source": "edX",
                    "content_price": test_content_key_2_metadata_price,
                }
            else:
                return {}

        self.mock_get_content_metadata.side_effect = mock_get_subsidy_content_data

        with mock.patch(
            'enterprise_access.apps.subsidy_access_policy.content_metadata_api.get_and_cache_content_metadata',
            side_effect=mock_get_subsidy_content_data,
        ):
            query_params = {'content_key': [test_content_key_1, test_content_key_2]}
            response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)

        assert response.status_code == status.HTTP_200_OK
        response_list = response.json()

        # Make sure we got responses for all two content_keys requested.
        assert len(response_list) == 2

        # Check the response for the first content_key given.
        assert response_list[0]["content_key"] == test_content_key_1
        # We should not assume that a list price is fetchable if the
        # content cant' be redeemed - the content may not be in any catalog for any policy.
        assert response_list[0]["list_price"] is None
        assert len(response_list[0]["redemptions"]) == 0
        assert response_list[0]["has_successful_redemption"] is False
        assert response_list[0]["redeemable_subsidy_access_policy"] is None
        assert response_list[0]["can_redeem"] is False

        expected_user_message = (
            MissingSubsidyAccessReasonUserMessages.ORGANIZATION_NO_FUNDS
            if has_admin_users
            else MissingSubsidyAccessReasonUserMessages.ORGANIZATION_NO_FUNDS_NO_ADMINS
        )
        expected_enterprise_admins = [{'email': admin_email}] if has_admin_users else []

        assert response_list[0]["reasons"] == [
            {
                "reason": REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY,
                "user_message": expected_user_message,
                "metadata": {
                    "enterprise_administrators": expected_enterprise_admins,
                },
                "policy_uuids": [str(self.redeemable_policy.uuid)],
            },
        ]

        # Check the response for the second content_key given.
        assert response_list[1]["content_key"] == test_content_key_2
        assert response_list[1]["list_price"] is None

        assert len(response_list[1]["redemptions"]) == 0
        assert response_list[1]["has_successful_redemption"] is False
        assert response_list[1]["redeemable_subsidy_access_policy"] is None
        assert response_list[1]["can_redeem"] is False
        assert response_list[1]["reasons"] == [
            {
                "reason": REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY,
                "user_message": expected_user_message,
                "metadata": {
                    "enterprise_administrators": expected_enterprise_admins,
                },
                "policy_uuids": [str(self.redeemable_policy.uuid)],
            },
        ]

    @mock.patch('enterprise_access.apps.subsidy_access_policy.subsidy_api.get_and_cache_transactions_for_learner')
    def test_can_redeem_policy_existing_redemptions(self, mock_transactions_cache_for_learner):
        """
        Test that the can_redeem endpoint shows existing redemptions too.
        """
        test_transaction_uuid = str(uuid4())
        mock_transactions_cache_for_learner.return_value = {
            "transactions": [{
                "uuid": test_transaction_uuid,
                "state": TransactionStateChoices.COMMITTED,
                "idempotency_key": "the-idempotency-key",
                "lms_user_id": self.user.lms_user_id,
                "content_key": "course-v1:demox+1234+2T2023",
                "quantity": -19900,
                "unit": "USD_CENTS",
                "enterprise_fulfillment_uuid": "6ff2c1c9-d5fc-48a8-81da-e6a675263f67",
                "subsidy_access_policy_uuid": str(self.redeemable_policy.uuid),
                "metadata": {},
                "reversal": None,
            }],
            "aggregates": {
                "total_quantity": -19900,
            },
        }

        self.redeemable_policy.subsidy_client.can_redeem.return_value = {
            'can_redeem': False,
            'active': True,
        }
        self.mock_get_content_metadata.return_value = {'content_price': 19900}

        mocked_content_data_from_view = {
            "content_uuid": str(uuid4()),
            "content_key": "course-v1:demox+1234+2T2023",
            "source": "edX",
            "content_price": 19900,
        }

        metadata_api_path = 'enterprise_access.apps.subsidy_access_policy.content_metadata_api'
        with mock.patch(
            f'{metadata_api_path}.get_and_cache_content_metadata',
            return_value=mocked_content_data_from_view,
        ):
            query_params = {'content_key': 'course-v1:demox+1234+2T2023'}
            response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)

        assert response.status_code == status.HTTP_200_OK
        response_list = response.json()

        # Make sure we got responses containing existing redemptions.
        assert len(response_list) == 1
        assert response_list[0]["content_key"] == query_params["content_key"]
        assert response_list[0]["list_price"] == {
            "usd": 199.00,
            "usd_cents": 19900,
        }
        assert len(response_list[0]["redemptions"]) == 1
        assert response_list[0]["redemptions"][0]["uuid"] == test_transaction_uuid
        assert response_list[0]["redemptions"][0]["policy_redemption_status_url"] == \
            f"{settings.ENTERPRISE_SUBSIDY_URL}/api/v1/transactions/{test_transaction_uuid}/"
        assert response_list[0]["redemptions"][0]["courseware_url"] == \
            f"{settings.LMS_URL}/courses/course-v1:demox+1234+2T2023/courseware/"
        self.assertTrue(response_list[0]["has_successful_redemption"])
        self.assertIsNone(response_list[0]["redeemable_subsidy_access_policy"])
        self.assertFalse(response_list[0]["can_redeem"])
        self.assertEqual(response_list[0]["reasons"], [])

        # We call this to fetch the list_price
        self.mock_get_content_metadata.assert_called_once_with("course-v1:demox+1234+2T2023")

    @mock.patch('enterprise_access.apps.subsidy_access_policy.subsidy_api.get_and_cache_transactions_for_learner')
    def test_can_redeem_policy_existing_reversed_redemptions(self, mock_transactions_cache_for_learner):
        """
        Test that the can_redeem endpoint returns can_redeem=True even with an existing reversed transaction.
        """
        test_transaction_uuid = str(uuid4())
        mock_transactions_cache_for_learner.return_value = {
            "transactions": [{
                "uuid": test_transaction_uuid,
                "state": TransactionStateChoices.COMMITTED,
                "idempotency_key": "the-idempotency-key",
                "lms_user_id": self.user.lms_user_id,
                "content_key": "course-v1:demox+1234+2T2023",
                "quantity": -19900,
                "unit": "USD_CENTS",
                "enterprise_fulfillment_uuid": "6ff2c1c9-d5fc-48a8-81da-e6a675263f67",
                "subsidy_access_policy_uuid": str(self.redeemable_policy.uuid),
                "metadata": {},
                "reversal": {
                    "uuid": str(uuid4()),
                    "state": TransactionStateChoices.COMMITTED,
                    "idempotency_key": f"admin-invoked-reverse-{test_transaction_uuid}",
                    "quantity": -19900,
                },
            }],
            "aggregates": {
                "total_quantity": 0,
            },
        }

        self.redeemable_policy.subsidy_client.can_redeem.return_value = {
            'can_redeem': True,
            'active': True,
        }
        self.mock_get_content_metadata.return_value = {'content_price': 19900}

        mocked_content_data_from_view = {
            "content_uuid": str(uuid4()),
            "content_key": "course-v1:demox+1234+2T2023",
            "source": "edX",
            "content_price": 19900,
        }

        with mock.patch(
            'enterprise_access.apps.subsidy_access_policy.content_metadata_api.get_and_cache_content_metadata',
            return_value=mocked_content_data_from_view,
        ):
            query_params = {'content_key': 'course-v1:demox+1234+2T2023'}
            response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)

        assert response.status_code == status.HTTP_200_OK
        response_list = response.json()

        # Make sure we got responses containing existing redemptions.
        assert len(response_list) == 1
        assert response_list[0]["content_key"] == query_params["content_key"]
        assert response_list[0]["list_price"] == {
            "usd": 199.00,
            "usd_cents": 19900,
        }
        assert len(response_list[0]["redemptions"]) == 1
        assert response_list[0]["redemptions"][0]["uuid"] == test_transaction_uuid
        assert response_list[0]["redemptions"][0]["policy_redemption_status_url"] == \
            f"{settings.ENTERPRISE_SUBSIDY_URL}/api/v1/transactions/{test_transaction_uuid}/"
        assert response_list[0]["redemptions"][0]["courseware_url"] == \
            f"{settings.LMS_URL}/courses/course-v1:demox+1234+2T2023/courseware/"
        assert response_list[0]["has_successful_redemption"] is False
        assert response_list[0]["redeemable_subsidy_access_policy"]["uuid"] == str(self.redeemable_policy.uuid)
        assert response_list[0]["can_redeem"] is True
        assert response_list[0]["reasons"] == []

    @mock.patch('enterprise_access.apps.subsidy_access_policy.subsidy_api.get_and_cache_transactions_for_learner')
    @mock.patch('enterprise_access.apps.api.v1.views.subsidy_access_policy.LmsApiClient')
    def test_can_redeem_policy_no_price(self, mock_lms_client, mock_transactions_cache_for_learner):
        """
        Test that the can_redeem endpoint successfully serializes a response for content that has no price.
        """
        test_content_key = "course-v1:demox+1234+2T2023"
        mock_lms_client.return_value.get_enterprise_customer_data.return_value = {
            'slug': 'sluggy',
            'admin_users': [{'email': 'edx@example.org'}],
        }

        self.mock_get_content_metadata.return_value = {
            'content_price': None,
        }

        mock_transactions_cache_for_learner.return_value = {
            'transactions': [],
            'aggregates': {
                'total_quantity': 0,
            },
        }

        mocked_content_data_from_view = {
            "content_uuid": str(uuid4()),
            "content_key": test_content_key,
            "source": "edX",
            "content_price": None,
        }

        with mock.patch(
            'enterprise_access.apps.subsidy_access_policy.content_metadata_api.get_and_cache_content_metadata',
            return_value=mocked_content_data_from_view,
        ):
            query_params = {'content_key': test_content_key}
            response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.json() == {
            'detail': f'Could not determine price for content_key: {test_content_key}',
        }

    @mock.patch('enterprise_access.apps.subsidy_access_policy.subsidy_api.get_versioned_subsidy_client')
    def test_can_redeem_subsidy_client_http_error(self, mock_get_client):
        """
        Test that the can_redeem endpoint raises
        an expected, specific exception when the subsidy REST API raises
        an HTTPError.
        """
        test_content_key = "course-v1:demox+1234+2T2023"
        query_params = {'content_key': test_content_key}

        mock_client = mock_get_client.return_value
        mock_client.list_subsidy_transactions.side_effect = HTTPError

        response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.json() == {
            'detail': 'Subsidy Transaction API error: HTTPError occurred in Subsidy API request.',
        }


@ddt.ddt
class TestAssignedSubsidyAccessPolicyCanRedeemView(BaseCanRedeemTestMixin, APITestWithMocks):
    """
    Tests for the can-redeem view for assignment-based policies.
    """

    def setUp(self):
        super().setUp()
        self.assignment_configuration = AssignmentConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
        )
        self.assigned_learner_credit_policy = AssignedLearnerCreditAccessPolicyFactory(
            display_name='An assigned learner credit policy, for the test customer.',
            enterprise_customer_uuid=self.enterprise_uuid,
            active=True,
            assignment_configuration=self.assignment_configuration,
            spend_limit=1000000,
        )
        self.content_key = 'edX+demoX'
        self.content_title = 'edx: Demo 101'
        self.assigned_price_cents = 25000
        self.assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            learner_email='alice@foo.com',
            lms_user_id=self.user.lms_user_id,
            content_key=self.content_key,
            content_title=self.content_title,
            content_quantity=-self.assigned_price_cents,
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
        )
        self.cancelled_content_key = 'edX+CancelledX'
        self.cancelled_assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            learner_email='alice@foo.com',
            lms_user_id=self.user.lms_user_id,
            content_key=self.cancelled_content_key,
            content_title='CANCELLED ASSIGNMENT',
            content_quantity=-self.assigned_price_cents,
            state=LearnerContentAssignmentStateChoices.CANCELLED,
        )
        self.failed_content_key = 'edX+FailedX'
        self.cancelled_assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            learner_email='alice@foo.com',
            lms_user_id=self.user.lms_user_id,
            content_key=self.failed_content_key,
            content_title='FAILED ASSIGNMENT',
            content_quantity=-self.assigned_price_cents,
            state=LearnerContentAssignmentStateChoices.ERRORED,
        )

    @mock.patch('enterprise_access.apps.content_assignments.api.get_and_cache_content_metadata')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.subsidy_api.get_and_cache_transactions_for_learner')
    def test_can_redeem_assigned_policy(
        self,
        mock_transactions_cache_for_learner,
        mock_content_get_and_cache_content_metadata
    ):
        """
        Test that the can_redeem endpoint returns an assigned access policy when one is redeemable.
        """
        mock_transactions_cache_for_learner.return_value = {
            'transactions': [],
            'aggregates': {
                'total_quantity': 0,
            },
        }
        test_content_key_1 = f"course-v1:{self.content_key}+3T2020"
        test_content_key_1_metadata_price = 29900

        mock_get_subsidy_content_data = {
            "content_uuid": str(uuid4()),
            "content_key": self.content_key,
            "source": "edX",
            "content_price": test_content_key_1_metadata_price,
        }
        mock_content_get_and_cache_content_metadata.return_value = mock_get_subsidy_content_data
        self.mock_get_content_metadata.return_value = mock_get_subsidy_content_data

        with mock.patch(
            'enterprise_access.apps.subsidy_access_policy.content_metadata_api.get_and_cache_content_metadata',
            side_effect=mock_get_subsidy_content_data,
        ):
            query_params = {'content_key': [test_content_key_1]}
            response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)

        assert response.status_code == status.HTTP_200_OK
        response_list = response.json()

        assert len(response_list) == 1

        # Check the response for the first content_key given.
        assert response_list[0]["content_key"] == test_content_key_1
        assert response_list[0]["list_price"] == {
            "usd": float(self.assigned_price_cents / 100),
            "usd_cents": self.assigned_price_cents,
        }
        assert len(response_list[0]["redemptions"]) == 0
        assert response_list[0]["has_successful_redemption"] is False
        assert response_list[0]["redeemable_subsidy_access_policy"]["uuid"] == \
            str(self.assigned_learner_credit_policy.uuid)
        assert response_list[0]["can_redeem"] is True
        assert len(response_list[0]["reasons"]) == 0

    @mock.patch('enterprise_access.apps.api.v1.views.subsidy_access_policy.LmsApiClient')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.subsidy_api.get_and_cache_transactions_for_learner')
    @ddt.data(
        # Only a cancelled assignment exists.
        {'has_cancelled_assignment': True, 'has_failed_assignment': False},
        # Only an errored assignment exists.
        {'has_cancelled_assignment': False, 'has_failed_assignment': True},
        # No assignment exists for the learner/content pair to check.
        {'has_cancelled_assignment': False, 'has_failed_assignment': True},
    )
    @ddt.unpack
    def test_can_redeem_no_assignment_for_content(
        self, mock_transactions_cache_for_learner, mock_lms_client,
        has_cancelled_assignment, has_failed_assignment,
    ):
        """
        Test that the can_redeem endpoint returns appropriate error reasons and user messages
        when checking re-deemability of unassigned/cancelled/failed assigned content.
        """
        mock_transactions_cache_for_learner.return_value = {
            'transactions': [],
            'aggregates': {
                'total_quantity': 0,
            },
        }
        course_content_key = "Unredeemable+Content"
        content_key_for_redemption = "course-v1:Unredeemable+Content+3T2020"
        if has_cancelled_assignment:
            course_content_key = self.cancelled_content_key
            content_key_for_redemption = f"course-v1:{self.cancelled_content_key}+1T2023"
        elif has_failed_assignment:
            course_content_key = self.failed_content_key
            content_key_for_redemption = f"course-v1:{self.failed_content_key}+1T2023"

        content_key_for_redemption_metadata_price = 29900
        mock_get_subsidy_content_data = {
            "content_uuid": str(uuid4()),
            "content_key": course_content_key,
            "source": "edX",
            "content_price": content_key_for_redemption_metadata_price,
        }
        self.mock_get_content_metadata.return_value = mock_get_subsidy_content_data

        # It's an unredeemable response, so mock out some admin users to return
        mock_lms_client.return_value.get_enterprise_customer_data.return_value = {
            'slug': 'sluggy',
            'admin_users': [{'email': 'edx@example.org'}],
        }

        # @mock.patch('enterprise_access.apps.content_assignments.api.get_and_cache_content_metadata')
        # enterprise_access.apps.subsidy_access_policy.content_metadata_api.get_and_cache_content_metadata
        with mock.patch(
            'enterprise_access.apps.content_assignments.api.get_and_cache_content_metadata',
            return_value=mock_get_subsidy_content_data,
        ):
            query_params = {'content_key': [content_key_for_redemption]}
            response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)

        assert response.status_code == status.HTTP_200_OK
        response_list = response.json()

        assert len(response_list) == 1

        # Check the response for the first content_key given.
        assert response_list[0]["content_key"] == content_key_for_redemption
        assert response_list[0]["list_price"] is None
        assert response_list[0]["redemptions"] == []
        assert response_list[0]["has_successful_redemption"] is False
        assert response_list[0]["redeemable_subsidy_access_policy"] is None
        assert response_list[0]["can_redeem"] is False

        expected_reason = REASON_LEARNER_NOT_ASSIGNED_CONTENT
        expected_message = MissingSubsidyAccessReasonUserMessages.LEARNER_NOT_ASSIGNED_CONTENT
        if has_cancelled_assignment:
            expected_reason = REASON_LEARNER_ASSIGNMENT_CANCELLED
            expected_message = MissingSubsidyAccessReasonUserMessages.LEARNER_ASSIGNMENT_CANCELED
        elif has_failed_assignment:
            expected_reason = REASON_LEARNER_ASSIGNMENT_FAILED
            expected_message = MissingSubsidyAccessReasonUserMessages.LEARNER_NOT_ASSIGNED_CONTENT

        expected_reasons = [
            {
                "reason": expected_reason,
                "user_message": expected_message,
                "metadata": {
                    "enterprise_administrators": [{'email': 'edx@example.org'}],
                },
                "policy_uuids": [str(self.assigned_learner_credit_policy.uuid)],
            },
        ]
        assert response_list[0]["reasons"] == expected_reasons
