"""
Tests for AssignmentConfiguration API views.
"""
from uuid import uuid4

import ddt
from rest_framework import status
from rest_framework.reverse import reverse

from enterprise_access.apps.content_assignments.models import AssignmentConfiguration
from enterprise_access.apps.content_assignments.tests.factories import AssignmentConfigurationFactory
from enterprise_access.apps.core.constants import (
    SYSTEM_ENTERPRISE_ADMIN_ROLE,
    SYSTEM_ENTERPRISE_LEARNER_ROLE,
    SYSTEM_ENTERPRISE_OPERATOR_ROLE
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import AssignedLearnerCreditAccessPolicyFactory
from test_utils import APITest

ASSIGNMENT_CONFIGURATION_LIST_ENDPOINT = reverse('api:v1:assignment-configurations-list')

TEST_ENTERPRISE_UUID = uuid4()


# pylint: disable=missing-function-docstring
class CRUDViewTestMixin:
    """
    Mixin to set some basic state for test classes that cover the AssignmentConfiguration CRUD views.
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.enterprise_uuid = TEST_ENTERPRISE_UUID
        cls.other_enterprise_uuid = uuid4()

        # Create a pair of AssignmentConfiguration + SubsidyAccessPolicy for the main test customer.
        cls.assignment_configuration_existing = AssignmentConfigurationFactory(
            enterprise_customer_uuid=cls.enterprise_uuid,
        )
        cls.assigned_learner_credit_policy = AssignedLearnerCreditAccessPolicyFactory(
            display_name='An assigned learner credit policy, for the test customer.',
            enterprise_customer_uuid=cls.enterprise_uuid,
            active=True,
            assignment_configuration=cls.assignment_configuration_existing,
            spend_limit=1000000,
        )

        # Create a pair of AssignmentConfiguration + SubsidyAccessPolicy for the "other" customer.
        # This is useful for testing that enterprise admins cannot read each other's models.
        cls.assignment_configuration_other_customer = AssignmentConfigurationFactory(
            enterprise_customer_uuid=cls.other_enterprise_uuid,
        )
        cls.assigned_learner_credit_policy_other_customer = AssignedLearnerCreditAccessPolicyFactory(
            display_name='An assigned learner credit policy, for a different customer.',
            enterprise_customer_uuid=cls.other_enterprise_uuid,
            active=True,
            assignment_configuration=cls.assignment_configuration_other_customer,
            spend_limit=1000000,
        )

    def setUp(self):
        super().setUp()
        # Start in an unauthenticated state.
        self.client.logout()


@ddt.ddt
class TestAssignmentConfigurationUnauthorizedCRUD(CRUDViewTestMixin, APITest):
    """
    Tests Authentication and Permission checking for AssignmentConfiguration CRUD views.
    """
    @ddt.data(
        # A role that's not mapped to any feature perms will get you a 403.
        (
            {'system_wide_role': 'some-other-role', 'context': str(TEST_ENTERPRISE_UUID)},
            status.HTTP_403_FORBIDDEN,
        ),
        # A good learner role, AND in the correct context/customer STILL gets you a 403.
        # AssignmentConfiguration APIs are inaccessible to all learners.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
            status.HTTP_403_FORBIDDEN,
        ),
        # A good admin role, but in a context/customer we're not aware of, gets you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(uuid4())},
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
    def test_assignment_config_readwrite_views_unauthorized_forbidden(self, role_context_dict, expected_response_code):
        """
        Tests that we get expected 40x responses for all of the read OR write views.
        """
        # Set the JWT-based auth that we'll use for every request
        if role_context_dict:
            self.set_jwt_cookie([role_context_dict])

        detail_kwargs = {'uuid': str(self.assignment_configuration_existing.uuid)}
        detail_url = reverse('api:v1:assignment-configurations-detail', kwargs=detail_kwargs)
        list_url = reverse('api:v1:assignment-configurations-list')

        # Test views that need CONTENT_ASSIGNMENT_CONFIGURATION_READ_PERMISSION:

        # GET/retrieve endpoint:
        response = self.client.get(detail_url)
        assert response.status_code == expected_response_code

        # GET/list endpoint:
        request_params = {'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)}
        response = self.client.get(list_url, request_params)
        assert response.status_code == expected_response_code

        # Test views that need CONTENT_ASSIGNMENT_CONFIGURATION_WRITE_PERMISSION:

        # POST/create endpoint:
        create_payload = {'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)}
        response = self.client.post(list_url, data=create_payload)
        assert response.status_code == expected_response_code

        # PUT/update endpoint:
        response = self.client.put(detail_url, data={'active': True})
        assert response.status_code == expected_response_code

        # PATCH/partial_update endpoint:
        response = self.client.patch(detail_url, data={'active': True})
        assert response.status_code == expected_response_code

        # DELETE/destroy endpoint:
        response = self.client.delete(detail_url)
        assert response.status_code == expected_response_code

    @ddt.data(
        # A good admin role, AND in the correct context/customer STILL gets you a 403.
        # AssignmentConfiguration write APIs are inaccessible to all enterprise admins.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
            status.HTTP_403_FORBIDDEN,
        ),
    )
    @ddt.unpack
    def test_assignment_config_write_views_unauthorized_forbidden(self, role_context_dict, expected_response_code):
        """
        Tests that we get expected 40x responses for only the write views.
        """
        # Set the JWT-based auth that we'll use for every request
        if role_context_dict:
            self.set_jwt_cookie([role_context_dict])

        detail_kwargs = {'uuid': str(self.assignment_configuration_existing.uuid)}
        detail_url = reverse('api:v1:assignment-configurations-detail', kwargs=detail_kwargs)
        list_url = reverse('api:v1:assignment-configurations-list')

        # Test views that need CONTENT_ASSIGNMENT_CONFIGURATION_WRITE_PERMISSION:

        # POST/create endpoint:
        create_payload = {'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)}
        response = self.client.post(list_url, data=create_payload)
        assert response.status_code == expected_response_code

        # PUT/update endpoint:
        response = self.client.put(detail_url, data={'active': True})
        assert response.status_code == expected_response_code

        # PATCH/partial_update endpoint:
        response = self.client.patch(detail_url, data={'active': True})
        assert response.status_code == expected_response_code

        # DELETE/destroy endpoint:
        response = self.client.delete(detail_url)
        assert response.status_code == expected_response_code


@ddt.ddt
class TestAssignmentConfigurationAuthorizedCRUD(CRUDViewTestMixin, APITest):
    """
    Test the AssignmentConfiguration API views while successfully authenticated/authorized.
    """
    @ddt.data(
        # A good admin role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good operator role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
    )
    def test_retrieve(self, role_context_dict):
        """
        Test that the retrieve view returns a 200 response code and the expected results of serialization.
        """
        # Set the JWT-based auth that we'll use for every request.
        self.set_jwt_cookie([role_context_dict])

        # Setup and call the retrieve endpoint.
        detail_kwargs = {'uuid': str(self.assignment_configuration_existing.uuid)}
        detail_url = reverse('api:v1:assignment-configurations-detail', kwargs=detail_kwargs)
        response = self.client.get(detail_url)

        assert response.status_code == status.HTTP_200_OK
        expected_config_response = {
            'uuid': str(self.assignment_configuration_existing.uuid),
            'active': True,
            'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID),
            'subsidy_access_policy': str(self.assigned_learner_credit_policy.uuid),
        }
        assert response.json() == expected_config_response

    @ddt.data(
        # A good admin role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good operator role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
    )
    def test_list(self, role_context_dict):
        """
        Test that the list view returns a 200 response code and the expected (list) results of serialization.  It should
        also allow system-wide admins and operators.

        This also tests that only AssignmentConfigs of the requested customer are returned.
        """
        # Set the JWT-based auth that we'll use for every request.
        self.set_jwt_cookie([role_context_dict])

        # Send a list request for all AssignmentConfigurations for the main test customer.
        list_url = reverse('api:v1:assignment-configurations-list')
        request_params = {'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)}
        response = self.client.get(list_url, request_params)

        # Only the AssignmentConfiguration for the main customer is returned, and not that of the other customer.
        assert response.json()['count'] == 1
        assert response.json()['results'][0] == {
            'uuid': str(self.assignment_configuration_existing.uuid),
            'active': True,
            'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID),
            'subsidy_access_policy': str(self.assigned_learner_credit_policy.uuid),
        }

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
    def test_destroy(self, request_payload, expected_change_reason):
        """
        Test that the destroy view performs a soft-delete and returns an appropriate response with 200 status code and
        the expected results of serialization.  Also test that the AssignmentConfiguration is unlinked from the
        associated policy.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        # Call the destroy endpoint.
        detail_kwargs = {'uuid': str(self.assignment_configuration_existing.uuid)}
        detail_url = reverse('api:v1:assignment-configurations-detail', kwargs=detail_kwargs)
        response = self.client.delete(detail_url, request_payload)

        assert response.status_code == status.HTTP_200_OK
        expected_response = {
            'uuid': str(self.assignment_configuration_existing.uuid),
            'active': False,
            'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID),
            'subsidy_access_policy': str(self.assigned_learner_credit_policy.uuid),
        }
        assert response.json() == expected_response

        # Check that the latest history record for this AssignmentConfiguration contains the change reason provided via
        # the API.
        self.assignment_configuration_existing.refresh_from_db()
        latest_history_entry = self.assignment_configuration_existing.history.order_by('-history_date').first()
        assert latest_history_entry.history_change_reason == expected_change_reason

        # Test idempotency of the destroy endpoint.
        response = self.client.delete(detail_url, request_payload)
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == expected_response

    @ddt.data(True, False)
    def test_update_views(self, is_patch):
        """
        Test that the update and partial_update views can modify certain fields of an AssignmentConfiguration record.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        detail_kwargs = {'uuid': str(self.assignment_configuration_existing.uuid)}
        detail_url = reverse('api:v1:assignment-configurations-detail', kwargs=detail_kwargs)

        action = self.client.patch if is_patch else self.client.put
        # Right now there's nothing really interesting on the model to update.
        request_payload = {
            'active': False,
        }
        response = action(detail_url, data=request_payload)

        assert response.status_code == status.HTTP_200_OK
        expected_response = {
            'uuid': str(self.assignment_configuration_existing.uuid),
            'active': False,
            'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID),
            'subsidy_access_policy': str(self.assigned_learner_credit_policy.uuid),
        }
        assert response.json() == expected_response

    def test_update_views_fields_disallowed_for_update(self):
        """
        Test that the update and partial_update views can NOT modify fields
        of a policy record that are not included in the update request serializer fields defintion.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        request_payload = {
            'uuid': str(uuid4()),
            'enterprise_customer_uuid': str(uuid4()),
            'subsidy_access_policy': str(uuid4()),
            'created': '1970-01-01 12:00:00Z',
            'modified': '1970-01-01 12:00:00Z',
            'nonsense_key': 'ship arriving too late to save a drowning witch',
        }

        detail_kwargs = {'uuid': str(self.assignment_configuration_existing.uuid)}
        detail_url = reverse('api:v1:assignment-configurations-detail', kwargs=detail_kwargs)

        expected_unknown_keys = ", ".join(sorted(request_payload.keys()))

        # Test the PUT view
        response = self.client.put(detail_url, data=request_payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {'non_field_errors': [f'Field(s) are not updatable: {expected_unknown_keys}']}

        # Test the PATCH view
        response = self.client.patch(detail_url, data=request_payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {'non_field_errors': [f'Field(s) are not updatable: {expected_unknown_keys}']}

    def test_create(self):
        """
        Test that create view happy path.  A net-new AsssignmentConfiguration should be created.
        """
        yet_another_enterprise_uuid = str(uuid4())
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': yet_another_enterprise_uuid}
        ])

        # Send a create request which should create a net-new AssignmentConfiguration.
        # It's possible to create these without linking directly to a policy record.
        list_url = reverse('api:v1:assignment-configurations-list')
        post_payload = {'enterprise_customer_uuid': yet_another_enterprise_uuid}

        response = self.client.post(list_url, post_payload)

        assert response.status_code == status.HTTP_201_CREATED

        response_payload = response.json()
        config_from_db = AssignmentConfiguration.objects.get(uuid=response_payload['uuid'])
        self.assertEqual(str(config_from_db.uuid), response_payload['uuid'])
        self.assertEqual(config_from_db.active, True)
        self.assertEqual(str(config_from_db.enterprise_customer_uuid), yet_another_enterprise_uuid)
        self.assertIsNone(config_from_db.policy)

    def test_create_unauthorized_other_customer(self):
        """
        Test that the create view fails when the requested policy belongs to a different customer.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        # Send a create request for a policy belonging to a different customer.  This should not be allowed!
        list_url = reverse('api:v1:assignment-configurations-list')
        post_payload = {'enterprise_customer_uuid': str(self.other_enterprise_uuid)}

        response = self.client.post(list_url, post_payload)

        assert response.status_code == status.HTTP_403_FORBIDDEN
