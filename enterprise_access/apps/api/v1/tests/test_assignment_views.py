"""
Tests for LearnerContentAssignment API views.
"""
from uuid import UUID, uuid4

import ddt
from rest_framework import status
from rest_framework.reverse import reverse

from enterprise_access.apps.content_assignments.constants import (
    AssignmentLearnerStates,
    AssignmentRecentActionTypes,
    LearnerContentAssignmentStateChoices
)
from enterprise_access.apps.content_assignments.models import LearnerContentAssignment
from enterprise_access.apps.content_assignments.tests.factories import (
    AssignmentConfigurationFactory,
    LearnerContentAssignmentFactory
)
from enterprise_access.apps.core.constants import (
    SYSTEM_ENTERPRISE_ADMIN_ROLE,
    SYSTEM_ENTERPRISE_LEARNER_ROLE,
    SYSTEM_ENTERPRISE_OPERATOR_ROLE
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import AssignedLearnerCreditAccessPolicyFactory
from test_utils import TEST_EMAIL, TEST_USER_ID, APITest

TEST_ENTERPRISE_UUID = uuid4()
TEST_OTHER_ENTERPRISE_UUID = uuid4()
TEST_ASSIGNMENT_CONFIG_UUID = uuid4()
TEST_OTHER_LMS_USER_ID = TEST_USER_ID + 1000

ADMIN_ASSIGNMENTS_LIST_ENDPOINT = reverse(
    'api:v1:admin-assignments-list',
    kwargs={'assignment_configuration_uuid': str(TEST_ASSIGNMENT_CONFIG_UUID)}
)
ASSIGNMENTS_LIST_ENDPOINT = reverse(
    'api:v1:assignments-list',
    kwargs={'assignment_configuration_uuid': str(TEST_ASSIGNMENT_CONFIG_UUID)}
)


# pylint: disable=missing-function-docstring
class CRUDViewTestMixin:
    """
    Mixin to set some basic state for test classes that cover the AssignmentConfiguration CRUD views.
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        # Create a pair of AssignmentConfiguration + SubsidyAccessPolicy for the main test customer.
        cls.assignment_configuration = AssignmentConfigurationFactory(
            uuid=TEST_ASSIGNMENT_CONFIG_UUID,
            enterprise_customer_uuid=TEST_ENTERPRISE_UUID,
        )
        cls.assigned_learner_credit_policy = AssignedLearnerCreditAccessPolicyFactory(
            display_name='An assigned learner credit policy, for the test customer.',
            enterprise_customer_uuid=TEST_ENTERPRISE_UUID,
            active=True,
            assignment_configuration=cls.assignment_configuration,
            spend_limit=1000000,
        )

        # Create a pair of AssignmentConfiguration + SubsidyAccessPolicy for the "other" customer.
        # This is useful for testing that enterprise admins cannot read each other's models.
        cls.assignment_configuration_other_customer = AssignmentConfigurationFactory(
            enterprise_customer_uuid=TEST_OTHER_ENTERPRISE_UUID,
        )
        cls.assigned_learner_credit_policy_other_customer = AssignedLearnerCreditAccessPolicyFactory(
            display_name='An assigned learner credit policy, for a different customer.',
            enterprise_customer_uuid=TEST_OTHER_ENTERPRISE_UUID,
            active=True,
            assignment_configuration=cls.assignment_configuration_other_customer,
            spend_limit=1000000,
        )

    def setUp(self):
        super().setUp()
        # Start in an unauthenticated state.
        self.client.logout()

        # This assignment has just been allocated, so its lms_user_id is null.
        self.assignment_allocated_pre_link = LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
            lms_user_id=None,
            transaction_uuid=None,
            assignment_configuration=self.assignment_configuration,
        )

        # The user for this assignment was found during enterprise linking, so its lms_user_id is non-null.
        self.assignment_allocated_post_link = LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
            lms_user_id=TEST_OTHER_LMS_USER_ID,
            transaction_uuid=None,
            assignment_configuration=self.assignment_configuration,
        )
        self.assignment_allocated_post_link.add_successful_linked_action()
        self.assignment_allocated_post_link.add_successful_notified_action()

        # This assignment has been accepted by the learner (state=accepted), AND the assigned learner is the requester.
        self.requester_assignment_accepted = LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
            learner_email=TEST_EMAIL,
            lms_user_id=TEST_USER_ID,
            transaction_uuid=uuid4(),
            assignment_configuration=self.assignment_configuration,
        )
        self.requester_assignment_accepted.add_successful_linked_action()
        self.requester_assignment_accepted.add_successful_notified_action()

        # This assignment has been accepted by the learner (state=accepted), AND the assigned learner is not the
        # requester.
        self.assignment_accepted = LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
            lms_user_id=TEST_OTHER_LMS_USER_ID,
            transaction_uuid=uuid4(),
            assignment_configuration=self.assignment_configuration,
        )
        self.assignment_accepted.add_successful_linked_action()
        self.assignment_accepted.add_successful_notified_action()

        # This assignment has been cancelled (state=cancelled), AND the assigned learner is the requester.
        self.requester_assignment_cancelled = LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.CANCELLED,
            learner_email=TEST_EMAIL,
            lms_user_id=TEST_USER_ID,
            transaction_uuid=uuid4(),
            assignment_configuration=self.assignment_configuration,
        )
        self.requester_assignment_cancelled.add_successful_linked_action()
        self.requester_assignment_cancelled.add_successful_notified_action()

        # This assignment has been cancelled (state=cancelled), AND the assigned learner is not the requester.
        self.assignment_cancelled = LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.CANCELLED,
            lms_user_id=TEST_OTHER_LMS_USER_ID,
            transaction_uuid=uuid4(),
            assignment_configuration=self.assignment_configuration,
        )
        self.assignment_cancelled.add_successful_linked_action()
        self.assignment_cancelled.add_successful_notified_action()

        # This assignment encountered a system error (state=errored), AND the assigned learner is the requester.
        self.requester_assignment_errored = LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.ERRORED,
            learner_email=TEST_EMAIL,
            lms_user_id=TEST_USER_ID,
            transaction_uuid=uuid4(),
            assignment_configuration=self.assignment_configuration,
        )
        linked_action, _ = self.assignment_cancelled.add_successful_linked_action()
        linked_action.error_reason = 'Phony error reason.'
        linked_action.save()

        ###
        # Below are additional assignments pertaining to a completely different customer than the main test customer.
        ###

        # This assignment has been accepted by the learner (state=accepted), AND the assigned learner is the requester.
        self.requester_assignment_accepted_other_customer = LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
            learner_email=TEST_EMAIL,
            lms_user_id=TEST_USER_ID,
            transaction_uuid=uuid4(),
            assignment_configuration=self.assignment_configuration_other_customer,
        )

        # This assignment has been accepted by the learner (state=accepted), AND the assigned learner is not the
        # requester.
        self.assignment_accepted_other_customer = LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
            lms_user_id=TEST_OTHER_LMS_USER_ID,
            transaction_uuid=uuid4(),
            assignment_configuration=self.assignment_configuration_other_customer,
        )


@ddt.ddt
class TestAdminAssignmentsUnauthorizedCRUD(CRUDViewTestMixin, APITest):
    """
    Tests Authentication and Permission checking for Admin-facing LearnerContentAssignment CRUD views.
    """
    @ddt.data(
        # A role that's not mapped to any feature perms will get you a 403.
        (
            {'system_wide_role': 'some-other-role', 'context': str(TEST_ENTERPRISE_UUID)},
            status.HTTP_403_FORBIDDEN,
        ),
        # A good learner role, AND in the correct context/customer STILL gets you a 403.
        # LearnerContentAssignment admin APIs are inaccessible to all learners.
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
        # A good admin role, AND a real context/customer but just the wrong one, gets you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_OTHER_ENTERPRISE_UUID)},
            status.HTTP_403_FORBIDDEN,
        ),
        # No JWT based auth, no soup for you.
        (
            None,
            status.HTTP_401_UNAUTHORIZED,
        ),
    )
    @ddt.unpack
    def test_admin_assignment_readwrite_views_unauthorized_forbidden(self, role_context_dict, expected_response_code):
        """
        Tests that we get expected 40x responses for all of the read OR write views.
        """
        # Set the JWT-based auth that we'll use for every request
        if role_context_dict:
            self.set_jwt_cookie([role_context_dict])

        detail_kwargs = {
            'assignment_configuration_uuid': str(TEST_ASSIGNMENT_CONFIG_UUID),
            'uuid': str(self.assignment_allocated_pre_link.uuid),
        }
        detail_url = reverse('api:v1:admin-assignments-detail', kwargs=detail_kwargs)
        cancel_url = detail_url + 'cancel/'

        # Test views that need CONTENT_ASSIGNMENT_ADMIN_READ_PERMISSION:

        # GET/retrieve endpoint:
        response = self.client.get(detail_url)
        assert response.status_code == expected_response_code

        # GET/list endpoint:
        request_params = {'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)}
        response = self.client.get(ADMIN_ASSIGNMENTS_LIST_ENDPOINT, request_params)
        assert response.status_code == expected_response_code

        # Test views that need CONTENT_ASSIGNMENT_ADMIN_WRITE_PERMISSION:

        # cancel endpoint:
        response = self.client.post(cancel_url)
        assert response.status_code == expected_response_code


@ddt.ddt
class TestAssignmentsUnauthorizedCRUD(CRUDViewTestMixin, APITest):
    """
    Tests Authentication and Permission checking for Learner-facing LearnerContentAssignment CRUD views.
    """
    @ddt.data(
        # A role that's not mapped to any feature perms will get you a 403.
        (
            {'system_wide_role': 'some-other-role', 'context': str(TEST_ENTERPRISE_UUID)},
            status.HTTP_403_FORBIDDEN,
        ),
        # A good learner role, but in a context/customer we're not aware of, gets you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(uuid4())},
            status.HTTP_403_FORBIDDEN,
        ),
        # A good learner role, AND a real context/customer but just the wrong one, gets you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(TEST_OTHER_ENTERPRISE_UUID)},
            status.HTTP_403_FORBIDDEN,
        ),
        # No JWT based auth, no soup for you.
        (
            None,
            status.HTTP_401_UNAUTHORIZED,
        ),
    )
    @ddt.unpack
    def test_assignment_views_unauthorized_forbidden(self, role_context_dict, expected_response_code):
        """
        Tests that we get expected 40x responses for all of the learner-facing views.
        """
        # Set the JWT-based auth that we'll use for every request
        if role_context_dict:
            self.set_jwt_cookie([role_context_dict])

        detail_kwargs = {
            'assignment_configuration_uuid': str(TEST_ASSIGNMENT_CONFIG_UUID),
            'uuid': str(self.requester_assignment_accepted.uuid),
        }
        detail_url = reverse('api:v1:assignments-detail', kwargs=detail_kwargs)

        # Test views that need CONTENT_ASSIGNMENT_READ_PERMISSION:

        # GET/retrieve endpoint:
        response = self.client.get(detail_url)
        assert response.status_code == expected_response_code

        # GET/list endpoint:
        request_params = {'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)}
        response = self.client.get(ADMIN_ASSIGNMENTS_LIST_ENDPOINT, request_params)
        assert response.status_code == expected_response_code


@ddt.ddt
class TestAdminAssignmentAuthorizedCRUD(CRUDViewTestMixin, APITest):
    """
    Test the Admin-facing Assignment API views while successfully authenticated/authorized.
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
        detail_kwargs = {
            'assignment_configuration_uuid': str(TEST_ASSIGNMENT_CONFIG_UUID),
            'uuid': str(self.assignment_allocated_pre_link.uuid),
        }
        detail_url = reverse('api:v1:admin-assignments-detail', kwargs=detail_kwargs)
        response = self.client.get(detail_url)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            'uuid': str(self.assignment_allocated_pre_link.uuid),
            'assignment_configuration': str(self.assignment_allocated_pre_link.assignment_configuration.uuid),
            'content_key': self.assignment_allocated_pre_link.content_key,
            'content_quantity': self.assignment_allocated_pre_link.content_quantity,
            'last_notification_at': None,
            'learner_email': self.assignment_allocated_pre_link.learner_email,
            'lms_user_id': None,
            'state': LearnerContentAssignmentStateChoices.ALLOCATED,
            'transaction_uuid': self.assignment_allocated_pre_link.transaction_uuid,
            'actions': [
                {
                    'uuid': str(action.uuid),
                    'action_type': action.action_type,
                    'completed_at': action.completed_at.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    'error_reason': None,
                }
                for action in self.assignment_allocated_pre_link.actions.order_by('completed_at')
            ],
            'recent_action': {
                'action_type': AssignmentRecentActionTypes.ASSIGNED,
                'timestamp': self.assignment_allocated_pre_link.created.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            },
            'learner_state': AssignmentLearnerStates.NOTIFYING,
        }

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

        This also tests that only Assignment in the requested AssignmentConfiguration are returned.
        """
        # Set the JWT-based auth that we'll use for every request.
        self.set_jwt_cookie([role_context_dict])

        # Send a list request for all Assignments for the main test customer.
        response = self.client.get(ADMIN_ASSIGNMENTS_LIST_ENDPOINT)

        # Only the Assignments for the main customer is returned, and not that of the other customer.
        expected_assignments_for_enterprise_customer = LearnerContentAssignment.objects.filter(
            assignment_configuration__enterprise_customer_uuid=TEST_ENTERPRISE_UUID
        )
        expected_assignment_uuids = {assignment.uuid for assignment in expected_assignments_for_enterprise_customer}
        actual_assignment_uuids = {UUID(assignment['uuid']) for assignment in response.json()['results']}
        assert actual_assignment_uuids == expected_assignment_uuids

    @ddt.data(
        None,
        'recent_action_time',
        '-recent_action_time',
    )
    def test_list_ordering_recent_action_time(self, ordering_key):
        """
        Test that the list view returns objects in the correct order when recent_action_time is the ordering key.  Also
        check that when no ordering parameter is supplied, the default ordering uses recent_action_time.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE,
            'context': str(TEST_ENTERPRISE_UUID),
        }])

        # Add reminder action to perturb the output ordering:
        self.assignment_allocated_post_link.add_successful_reminded_action()

        # Add non-reminder actions to another assignment to make sure it does NOT perturb the output ordering.
        self.assignment_allocated_pre_link.add_successful_linked_action()
        self.assignment_allocated_pre_link.add_successful_notified_action()

        query_params = None
        if ordering_key:
            query_params = {'ordering': ordering_key}

        # Send a list request for all Assignments for the main test customer, optionally with a specific ordering.
        response = self.client.get(ADMIN_ASSIGNMENTS_LIST_ENDPOINT, data=query_params)

        # Explicitly define the REVERSE order of assignments returned by the list view.  This should be the order if
        # ?ordering=+recent_action_time
        recent_action_time_ordering = [
            # First 6 assignments oredered by their creation time.
            self.assignment_allocated_pre_link,  # Still chronologically first, despite recent non-reminder actions.
            self.requester_assignment_accepted,
            self.assignment_accepted,
            self.requester_assignment_cancelled,
            self.assignment_cancelled,
            self.requester_assignment_errored,
            # This assignment was created first, but is knocked to the end of the list because we added a reminded
            # action most recently.
            self.assignment_allocated_post_link,
        ]
        expected_assignments_ordering = None
        if not ordering_key or ordering_key.startswith('-'):
            # The default ordering is reversed of chronological order.
            expected_assignments_ordering = reversed(recent_action_time_ordering)
        else:
            # Ordering is chronological IFF ?ordering=recent_action_time
            expected_assignments_ordering = recent_action_time_ordering
        expected_assignment_uuids = [assignment.uuid for assignment in expected_assignments_ordering]
        actual_assignment_uuids = [UUID(assignment['uuid']) for assignment in response.json()['results']]
        assert actual_assignment_uuids == expected_assignment_uuids

    @ddt.data(
        'learner_state_sort_order',
        '-learner_state_sort_order',
    )
    def test_list_ordering_learner_state_sort_order(self, ordering_key):
        """
        Test that the list view returns objects in the correct order when learner_state_sort_order is the ordering key.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE,
            'context': str(TEST_ENTERPRISE_UUID),
        }])

        query_params = None
        if ordering_key:
            query_params = {'ordering': ordering_key}

        # Send a list request for all Assignments for the main test customer, optionally with a specific ordering.
        response = self.client.get(ADMIN_ASSIGNMENTS_LIST_ENDPOINT, data=query_params)

        list_response_ordering = [
            # First, list allocated, non-notified assignments.
            self.assignment_allocated_pre_link,
            # Then, list allocated, notified assignments.
            self.assignment_allocated_post_link,
            # Then, list errored assignments.
            self.requester_assignment_errored,

            # No need to test sort order of accepted and cancelled assignments since they are not displayed.
            # self.assignment_accepted,
            # self.requester_assignment_accepted,
            # self.requester_assignment_cancelled,
            # self.assignment_cancelled,
        ]
        expected_assignments_ordering = list_response_ordering
        if ordering_key.startswith('-'):
            # The default ordering is reversed of chronological order.
            expected_assignments_ordering = reversed(list_response_ordering)
        expected_assignment_uuids = [assignment.uuid for assignment in expected_assignments_ordering]
        actual_assignment_uuids = [
            UUID(assignment['uuid'])
            for assignment in response.json()['results']
            # Only gather the assignments with the states under test from the response.
            if assignment['state'] in (
                LearnerContentAssignmentStateChoices.ALLOCATED,
                LearnerContentAssignmentStateChoices.ERRORED,
            )
        ]
        assert actual_assignment_uuids == expected_assignment_uuids

    def test_cancel(self):
        """
        Test that the cancel view cancels the assignment and returns an appropriate response with 200 status code and
        the expected results of serialization.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        # Call the cancel endpoint.
        detail_kwargs = {
            'assignment_configuration_uuid': str(TEST_ASSIGNMENT_CONFIG_UUID),
            'uuid': str(self.assignment_allocated_post_link.uuid),
        }
        detail_url = reverse('api:v1:admin-assignments-detail', kwargs=detail_kwargs)
        cancel_url = detail_url + 'cancel/'
        response = self.client.post(cancel_url)

        # Verify the API response.
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['state'] == LearnerContentAssignmentStateChoices.CANCELLED

        # Check that the assignment state was updated.
        self.assignment_allocated_post_link.refresh_from_db()
        assert self.assignment_allocated_post_link.state == LearnerContentAssignmentStateChoices.CANCELLED

        # Test idempotency of the cancel endpoint.  This time, the status is 204 because the assignment was already
        # cancelled.
        response = self.client.post(cancel_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['state'] == LearnerContentAssignmentStateChoices.CANCELLED

    def test_cancel_non_cancelable_returns_422(self):
        """
        Test that the cancel view fails with a 422 if the assignment is non-cancelable.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        # Call the cancel endpoint on an already accepted assignment.
        detail_kwargs = {
            'assignment_configuration_uuid': str(TEST_ASSIGNMENT_CONFIG_UUID),
            'uuid': str(self.assignment_accepted.uuid),
        }
        detail_url = reverse('api:v1:admin-assignments-detail', kwargs=detail_kwargs)
        cancel_url = detail_url + 'cancel/'
        response = self.client.post(cancel_url)

        # Verify the API response.
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        # Check that the assignment state was NOT updated, and the state is still accepted.
        self.assignment_allocated_post_link.refresh_from_db()
        assert self.assignment_accepted.state == LearnerContentAssignmentStateChoices.ACCEPTED


@ddt.ddt
class TestAssignmentAuthorizedCRUD(CRUDViewTestMixin, APITest):
    """
    Test the Learner-facing Assignment API views while successfully authenticated/authorized.
    """
    @ddt.data(
        # A good learner role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
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
        detail_kwargs = {
            'assignment_configuration_uuid': str(TEST_ASSIGNMENT_CONFIG_UUID),
            'uuid': str(self.requester_assignment_accepted.uuid),
        }
        detail_url = reverse('api:v1:assignments-detail', kwargs=detail_kwargs)
        response = self.client.get(detail_url)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            'uuid': str(self.requester_assignment_accepted.uuid),
            'assignment_configuration': str(self.requester_assignment_accepted.assignment_configuration.uuid),
            'content_key': self.requester_assignment_accepted.content_key,
            'content_quantity': self.requester_assignment_accepted.content_quantity,
            'last_notification_at': None,
            'learner_email': self.requester_assignment_accepted.learner_email,
            'lms_user_id': self.requester_assignment_accepted.lms_user_id,
            'state': LearnerContentAssignmentStateChoices.ACCEPTED,
            'transaction_uuid': str(self.requester_assignment_accepted.transaction_uuid),
            'actions': [
                {
                    'uuid': str(action.uuid),
                    'action_type': action.action_type,
                    'completed_at': str(action.completed_at.strftime('%Y-%m-%dT%H:%M:%S.%fZ')),
                    'error_reason': None,
                }
                for action in self.requester_assignment_accepted.actions.order_by('completed_at')
            ],
            'recent_action': {
                'action_type': AssignmentRecentActionTypes.ASSIGNED,
                'timestamp': self.requester_assignment_accepted.created.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            },
            'learner_state': None,
        }

    def test_retrieve_other_assignment_not_found(self):
        """
        Tests that we get expected 40x responses when learner A attempts to retrieve learner B's assignment.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(TEST_ENTERPRISE_UUID)
        }])

        detail_kwargs = {
            'assignment_configuration_uuid': str(TEST_ASSIGNMENT_CONFIG_UUID),
            'uuid': str(self.assignment_accepted.uuid),
        }
        detail_url = reverse('api:v1:assignments-detail', kwargs=detail_kwargs)

        # GET/retrieve endpoint:
        response = self.client.get(detail_url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @ddt.data(
        # A good learner role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good admin role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good operator role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
    )
    def test_list(self, role_context_dict):
        """
        Test that the list view returns a 200 response code and the expected (list) results of serialization.

        This also tests that only Assignments for the requesting user are returned.
        """
        # Set the JWT-based auth that we'll use for every request.
        self.set_jwt_cookie([role_context_dict])

        # Send a list request for all Assignments for the requesting user.
        response = self.client.get(ASSIGNMENTS_LIST_ENDPOINT)

        # Only Assignments that match the following qualifications are returned in paginated response:
        # 1. Assignment is for the requesting user.
        # 2. Assignment is in the requested AssignementConfiguration.
        expected_assignments_for_requester = [
            self.requester_assignment_accepted,
            self.requester_assignment_cancelled,
            self.requester_assignment_errored,
        ]
        expected_assignment_uuids = {assignment.uuid for assignment in expected_assignments_for_requester}
        actual_assignment_uuids = {UUID(assignment['uuid']) for assignment in response.json()['results']}
        assert actual_assignment_uuids == expected_assignment_uuids
