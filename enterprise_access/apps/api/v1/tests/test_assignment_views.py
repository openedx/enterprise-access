"""
Tests for LearnerContentAssignment API views.
"""
from datetime import timedelta
from unittest import mock
from uuid import UUID, uuid4

import ddt
from django.utils import timezone
from rest_framework import status
from rest_framework.reverse import reverse

from enterprise_access.apps.content_assignments.constants import (
    NUM_DAYS_BEFORE_AUTO_EXPIRATION,
    AssignmentActionErrors,
    AssignmentActions,
    AssignmentAutomaticExpiredReason,
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
from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy
from enterprise_access.apps.subsidy_access_policy.tests.factories import AssignedLearnerCreditAccessPolicyFactory
from enterprise_access.utils import localized_utcnow
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
MOCK_CATALOG_RESULT = {
    'count': 2,
    'results': [
        {'key': 'course+A', 'data': 'things'}, {'key': 'course+B', 'data': 'stuff'},
    ],
}
MOCK_SUBSIDY_RECORD = {
    'uuid': str(uuid4()),
    'title': 'Test Subsidy',
    'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID),
    'expiration_datetime': '2030-01-01 12:00:00Z',
    'active_datetime': '2020-01-01 12:00:00Z',
    'current_balance': '5000',
    'is_active': True,
}


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

        self.now = localized_utcnow()

        self.content_metadata_one = {
            'content_key': 'course-v1:edX+Accessibility101+T2024a',
            'parent_content_key': 'edX+Accessibility101',
            'content_title': 'edx: Accessibility 101',
            'content_quantity': -123,
        }
        self.content_metadata_two = {
            'content_key': 'course-v1:edX+Privacy101+T2024a',
            'parent_content_key': 'edX+Privacy101',
            'content_title': 'edx: Privacy 101',
            'content_quantity': -321,
        }

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
            content_key=self.content_metadata_two['content_key'],
            parent_content_key=self.content_metadata_two['parent_content_key'],
            is_assigned_course_run=True,
            content_quantity=self.content_metadata_two['content_quantity'],
            content_title=self.content_metadata_two['content_title'],
        )
        self.assignment_allocated_post_link.add_successful_linked_action()
        self.assignment_allocated_post_link.add_successful_notified_action()

        # This assignment has been accepted by the learner (state=accepted), AND the assigned learner is the requester.
        self.requester_assignment_accepted = LearnerContentAssignmentFactory(
            created=self.now,
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
            content_key=self.content_metadata_one['content_key'],
            parent_content_key=self.content_metadata_one['parent_content_key'],
            is_assigned_course_run=True,
            content_quantity=self.content_metadata_one['content_quantity'],
            content_title=self.content_metadata_one['content_title'],
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
        self.requester_assignment_errored.actions.create(
            action_type=AssignmentActions.NOTIFIED,
            error_reason='Phony error reason.',
            traceback=None,
        )
        linked_action = self.assignment_cancelled.add_successful_linked_action()
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

        # Call the cancel endpoint.
        cancel_kwargs = {
            'assignment_configuration_uuid': str(TEST_ASSIGNMENT_CONFIG_UUID),
        }
        cancel_url = reverse('api:v1:admin-assignments-cancel', kwargs=cancel_kwargs)
        query_params = {
            'assignment_uuids': [str(self.assignment_allocated_post_link.uuid)],
        }
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
        response = self.client.post(cancel_url, query_params)
        assert response.status_code == expected_response_code

        # Call the nudge endpoint.
        nudge_kwargs = {
            'assignment_configuration_uuid': str(TEST_ASSIGNMENT_CONFIG_UUID),
        }
        nudge_url = reverse('api:v1:admin-assignments-nudge', kwargs=nudge_kwargs)
        query_params = {
            'assignment_uuids': [str(self.assignment_accepted.uuid)],
            'days_before_course_start_date': 3
        }

        # nudge endpoint
        response = self.client.post(nudge_url, query_params)
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

    def setUp(self):
        super().setUp()

        # Mock results from the catalog content metadata API endpoint.
        self.mock_catalog_result = MOCK_CATALOG_RESULT

        # Mock results from the subsidy record.
        self.mock_subsidy_record = MOCK_SUBSIDY_RECORD

    @ddt.data(
        # A good admin role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good operator role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
    )
    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient', autospec=True)
    @mock.patch.object(SubsidyAccessPolicy, 'subsidy_record', autospec=True)
    def test_retrieve(self, role_context_dict, mock_subsidy_record, mock_catalog_client):
        """
        Test that the retrieve view returns a 200 response code and the expected results of serialization.
        """
        # Set the JWT-based auth that we'll use for every request.
        self.set_jwt_cookie([role_context_dict])

        # Mock results from the catalog content metadata API endpoint.
        mock_catalog_client.return_value.catalog_content_metadata.return_value = self.mock_catalog_result

        # Mock results from the subsidy record.
        mock_subsidy_record.return_value = self.mock_subsidy_record

        # Add a successful reminder for our assignment record in the past,
        # so that we can later assert that the recent_action in the response payload
        # is the assignment's allocation time, not the (more distant) reminder time.
        self.assignment_allocated_pre_link.actions.create(
            action_type=AssignmentActions.REMINDED,
            completed_at=timezone.now() - timedelta(hours=3),
        )

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
            'parent_content_key': self.assignment_allocated_pre_link.parent_content_key,
            'is_assigned_course_run': self.assignment_allocated_pre_link.is_assigned_course_run,
            'content_title': self.assignment_allocated_pre_link.content_title,
            'content_quantity': self.assignment_allocated_pre_link.content_quantity,
            'learner_email': self.assignment_allocated_pre_link.learner_email,
            'lms_user_id': None,
            'state': LearnerContentAssignmentStateChoices.ALLOCATED,
            'transaction_uuid': self.assignment_allocated_pre_link.transaction_uuid,
            'actions': [
                {
                    'created': action.created.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    'modified': action.modified.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    'uuid': str(action.uuid),
                    'action_type': action.action_type,
                    'completed_at': action.completed_at.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    'error_reason': action.error_reason,
                }
                for action in self.assignment_allocated_pre_link.actions.order_by('created')
            ],
            'error_reason': None,
            'recent_action': {
                'action_type': AssignmentRecentActionTypes.ASSIGNED,
                'timestamp': self.assignment_allocated_pre_link.allocated_at.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            },
            'learner_state': AssignmentLearnerStates.NOTIFYING,
            'earliest_possible_expiration': {
                'date': (
                    self.assignment_allocated_pre_link.allocated_at + timedelta(days=NUM_DAYS_BEFORE_AUTO_EXPIRATION)
                ).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                'reason': AssignmentAutomaticExpiredReason.NINETY_DAYS_PASSED,
            },
        }

    @ddt.data(
        # A good admin role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good operator role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
    )
    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient', autospec=True)
    @mock.patch.object(SubsidyAccessPolicy, 'subsidy_record', autospec=True)
    def test_retrieve_allocated_with_notification_error(
        self, role_context_dict, mock_subsidy_record, mock_catalog_client
    ):
        assignment = LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
            lms_user_id=98123,
            transaction_uuid=None,
            assignment_configuration=self.assignment_configuration,
            content_key='edX+edXPrivacy101',
            content_quantity=-321,
            content_title='edx: Privacy 101'
        )
        assignment.add_errored_notified_action(Exception('foo'))

        # Set the JWT-based auth that we'll use for every request.
        self.set_jwt_cookie([role_context_dict])

        # Mock results from the catalog content metadata API endpoint.
        mock_catalog_client.return_value.catalog_content_metadata.return_value = self.mock_catalog_result

        # Mock results from the subsidy record.
        mock_subsidy_record.return_value = self.mock_subsidy_record

        # Setup and call the retrieve endpoint.
        detail_kwargs = {
            'assignment_configuration_uuid': str(TEST_ASSIGNMENT_CONFIG_UUID),
            'uuid': str(assignment.uuid),
        }
        detail_url = reverse('api:v1:admin-assignments-detail', kwargs=detail_kwargs)
        response = self.client.get(detail_url)

        assert response.status_code == status.HTTP_200_OK
        assert response.json().get('state') == LearnerContentAssignmentStateChoices.ALLOCATED
        assert response.json().get('learner_state') == AssignmentLearnerStates.FAILED
        assert response.json().get('error_reason') == {
            'action_type': AssignmentActions.NOTIFIED,
            'error_reason': AssignmentActionErrors.EMAIL_ERROR,
        }

    @ddt.data(
        # A good admin role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good operator role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
    )
    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient', autospec=True)
    @mock.patch.object(SubsidyAccessPolicy, 'subsidy_record', autospec=True)
    def test_retrieve_errored_state(self, role_context_dict, mock_subsidy_record, mock_catalog_client):
        """
        Test that the retrieve view returns a 200 response code and the expected results of serialization
        when there is a recent error.
        """
        # Set the JWT-based auth that we'll use for every request.
        self.set_jwt_cookie([role_context_dict])

        # Mock results from the catalog content metadata API endpoint.
        mock_catalog_client.return_value.catalog_content_metadata.return_value = self.mock_catalog_result

        # Mock results from the subsidy record.
        mock_subsidy_record.return_value = self.mock_subsidy_record

        # Setup and call the retrieve endpoint.
        detail_kwargs = {
            'assignment_configuration_uuid': str(TEST_ASSIGNMENT_CONFIG_UUID),
            'uuid': str(self.requester_assignment_errored.uuid),
        }
        detail_url = reverse('api:v1:admin-assignments-detail', kwargs=detail_kwargs)
        response = self.client.get(detail_url)

        assert response.status_code == status.HTTP_200_OK
        assert response.json().get('error_reason') == {
            'action_type': AssignmentActions.NOTIFIED,
            'error_reason': 'Phony error reason.',
        }

    @ddt.data(
        # A good admin role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good operator role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
    )
    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient', autospec=True)
    @mock.patch.object(SubsidyAccessPolicy, 'subsidy_record', autospec=True)
    def test_list(self, role_context_dict, mock_subsidy_record, mock_catalog_client):
        """
        Test that the list view returns a 200 response code and the expected (list) results of serialization, including
        the `learner_state_counts` overview metadata. It should also allow system-wide admins and operators.

        This also tests that only Assignment in the requested AssignmentConfiguration are returned.
        """
        # Set the JWT-based auth that we'll use for every request.
        self.set_jwt_cookie([role_context_dict])

        # Mock results from the catalog content metadata API endpoint.
        mock_catalog_client.return_value.catalog_content_metadata.return_value = self.mock_catalog_result

        # Mock results from the subsidy record.
        mock_subsidy_record.return_value = self.mock_subsidy_record

        # Send a list request for all Assignments for the main test customer.
        response = self.client.get(ADMIN_ASSIGNMENTS_LIST_ENDPOINT)
        response_json = response.json()

        # Only the Assignments for the main customer is returned, and not that of the other customer.
        expected_assignments_for_enterprise_customer = LearnerContentAssignment.objects.filter(
            assignment_configuration__enterprise_customer_uuid=TEST_ENTERPRISE_UUID
        )
        expected_assignment_uuids = {assignment.uuid for assignment in expected_assignments_for_enterprise_customer}
        actual_assignment_uuids = {UUID(assignment['uuid']) for assignment in response_json['results']}
        assert actual_assignment_uuids == expected_assignment_uuids

        expected_learner_state_counts = [
            {'count': 1, 'learner_state': 'failed'},
            {'count': 1, 'learner_state': 'waiting'},
            {'count': 1, 'learner_state': 'notifying'},
        ]
        assert response_json['learner_state_counts'] == expected_learner_state_counts

    @ddt.data(
        None,
        'recent_action_time',
        '-recent_action_time',
    )
    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient', autospec=True)
    @mock.patch.object(SubsidyAccessPolicy, 'subsidy_record', autospec=True)
    def test_list_ordering_recent_action_time(self, ordering_key, mock_subsidy_record, mock_catalog_client):
        """
        Test that the list view returns objects in the correct order when recent_action_time is the ordering key.  Also
        check that when no ordering parameter is supplied, the default ordering uses recent_action_time.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE,
            'context': str(TEST_ENTERPRISE_UUID),
        }])

        # Mock results from the catalog content metadata API endpoint.
        mock_catalog_client.return_value.catalog_content_metadata.return_value = self.mock_catalog_result

        # Mock results from the subsidy record.
        mock_subsidy_record.return_value = self.mock_subsidy_record

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
            # First 6 assignments ordered by their creation time.
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
    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient', autospec=True)
    @mock.patch.object(SubsidyAccessPolicy, 'subsidy_record', autospec=True)
    def test_list_ordering_learner_state_sort_order(self, ordering_key, mock_subsidy_record, mock_catalog_client):
        """
        Test that the list view returns objects in the correct order when learner_state_sort_order is the ordering key.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE,
            'context': str(TEST_ENTERPRISE_UUID),
        }])

        # Mock results from the catalog content metadata API endpoint.
        mock_catalog_client.return_value.catalog_content_metadata.return_value = self.mock_catalog_result

        # Mock results from the subsidy record.
        mock_subsidy_record.return_value = self.mock_subsidy_record

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

    def test_remind(self):
        """
        Test that the remind view reminds the learner of their assignment and returns
        an appropriate response with 200 status code.
        """
        # Set the JWT-based auth to an operator.

        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        # Call the remind endpoint.
        remind_kwargs = {
            'assignment_configuration_uuid': str(TEST_ASSIGNMENT_CONFIG_UUID),
        }
        remind_url = reverse('api:v1:admin-assignments-remind', kwargs=remind_kwargs)
        query_params = {
            'assignment_uuids': [str(self.assignment_allocated_post_link.uuid)],
        }

        with mock.patch(
            'enterprise_access.apps.content_assignments.api.send_reminder_email_for_pending_assignment'
        ) as mock_remind_task:
            response = self.client.post(remind_url, query_params)
            mock_remind_task.delay.assert_called_once_with(self.assignment_allocated_post_link.uuid)

        # Verify the API response.
        assert response.status_code == status.HTTP_200_OK

    def test_bulk_remind(self):
        """
        Test that the remind view reminds the learner of their assignment and returns an appropriate response
        with 422 status code if any of the uuids cannot be found.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        # Call the remind endpoint.
        remind_kwargs = {
            'assignment_configuration_uuid': str(TEST_ASSIGNMENT_CONFIG_UUID),
        }
        remind_url = reverse('api:v1:admin-assignments-remind', kwargs=remind_kwargs)
        random_uuid = str(uuid4())
        query_params = {
            'assignment_uuids': [str(self.assignment_allocated_post_link.uuid), random_uuid],
        }

        response = self.client.post(remind_url, query_params)
        # Verify the API response (one of the uuid's cannot be found)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @mock.patch('enterprise_access.apps.content_assignments.tasks.send_cancel_email_for_pending_assignment')
    def test_cancel(self, mock_send_cancel_email):
        """
        Test that the cancel view cancels the assignment and returns an appropriate response with 200 status code and
        the expected results of serialization.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        # Call the cancel endpoint.
        cancel_kwargs = {
            'assignment_configuration_uuid': str(TEST_ASSIGNMENT_CONFIG_UUID),
        }
        cancel_url = reverse('api:v1:admin-assignments-cancel', kwargs=cancel_kwargs)
        query_params = {
            'assignment_uuids': [str(self.assignment_allocated_post_link.uuid)],
        }

        response = self.client.post(cancel_url, query_params)

        # Verify the API response.
        assert response.status_code == status.HTTP_200_OK

        # Check that the assignments state were updated.
        self.assignment_allocated_post_link.refresh_from_db()
        assert self.assignment_allocated_post_link.state == LearnerContentAssignmentStateChoices.CANCELLED
        mock_send_cancel_email.delay.assert_called_once_with(self.assignment_allocated_post_link.uuid)

    @mock.patch('enterprise_access.apps.content_assignments.api.get_content_metadata_for_assignments')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.send_exec_ed_enrollment_warmer.delay')
    def test_nudge_happy_path(self, mock_send_nudge_email, mock_content_metadata_for_assignments):
        """
        Test that the nudge view nudges the assignment and returns an appropriate response with 200 status code and
        the expected results of serialization.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        start_date = timezone.now().replace(microsecond=0) + timezone.timedelta(days=14)
        end_date = timezone.now().replace(microsecond=0) + timezone.timedelta(days=180)
        enrollment_end = timezone.now().replace(microsecond=0) - timezone.timedelta(days=5)

        # Mock content metadata for assignment
        mock_content_metadata_for_assignments.return_value = {
            self.content_metadata_one['content_key']: {
                'key': self.content_metadata_one['parent_content_key'],
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': self.content_metadata_one['content_quantity'],
                },
                'course_type': 'executive-education-2u',
            },
        }
        # Call the nudge endpoint.
        nudge_kwargs = {
            'assignment_configuration_uuid': self.assignment_configuration.uuid,
        }
        nudge_url = reverse('api:v1:admin-assignments-nudge', kwargs=nudge_kwargs)
        query_params = {
            'assignment_uuids': [str(self.assignment_accepted.uuid)],
            'days_before_course_start_date': 14
        }

        expected_response = {
            "nudged_assignment_uuids": [str(self.assignment_accepted.uuid)],
            "unnudged_assignment_uuids": []
        }

        response = self.client.post(nudge_url, query_params)

        # Verify the API response.
        # assert response.status_code == status.HTTP_200_OK
        assert response.json() == expected_response

        mock_send_nudge_email.assert_called_once_with(self.assignment_accepted.uuid, 14)

    @mock.patch('enterprise_access.apps.content_assignments.tasks.send_exec_ed_enrollment_warmer.delay')
    def test_nudge_allocated_assignment(self, mock_send_nudge_email):
        """
        Test that the nudge view doesn't nudge the assignment and
        returns an appropriate response with 422 status code and
        the expected results of serialization.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        # Call the nudge endpoint.
        nudge_kwargs = {
            'assignment_configuration_uuid': self.assignment_configuration.uuid,
        }
        nudge_url = reverse('api:v1:admin-assignments-nudge', kwargs=nudge_kwargs)
        query_params = {
            'assignment_uuids': [str(self.assignment_allocated_post_link.uuid)],
            'days_before_course_start_date': 14
        }

        response = self.client.post(nudge_url, query_params)

        expected_response = {
            "error_message": "Could not process the nudge email(s) for assignment_configuration_uuid: {0}"
            .format(self.assignment_configuration.uuid),
        }

        # Verify the API response.
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.json() == expected_response

        mock_send_nudge_email.assert_not_called()

    @mock.patch('enterprise_access.apps.content_assignments.tasks.send_exec_ed_enrollment_warmer.delay')
    def test_nudge_no_assignments(self, mock_send_nudge_email):
        """
        Test that the nudge view doesn't nudge the assignment and
        returns an appropriate response with 422 status code and
        the expected results of serialization.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        # Call the nudge endpoint.
        nudge_kwargs = {
            'assignment_configuration_uuid': self.assignment_configuration.uuid,
        }
        nudge_url = reverse('api:v1:admin-assignments-nudge', kwargs=nudge_kwargs)

        query_params = {
            'assignment_uuids': [str(uuid4())],
            'days_before_course_start_date': 14
        }

        response = self.client.post(nudge_url, query_params)

        expected_response = {
            "error_message": "The list of assignments provided are not "
                             "associated to the assignment_configuration_uuid: {0}"
            .format(self.assignment_configuration.uuid)
        }

        # Verify the API response.
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.json() == expected_response

        mock_send_nudge_email.assert_not_called()

    def test_bulk_cancel(self):
        """
        Test that the cancel view cancels the assignment and returns an appropriate response
        with 422 status code if any of the uuids fail.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        # Call the cancel endpoint.
        detail_kwargs = {
            'assignment_configuration_uuid': str(TEST_ASSIGNMENT_CONFIG_UUID),
        }
        cancel_url = reverse('api:v1:admin-assignments-cancel', kwargs=detail_kwargs)
        query_params = {
            'assignment_uuids': [str(self.assignment_allocated_post_link.uuid), str(self.assignment_accepted.uuid)],
        }

        response = self.client.post(cancel_url, query_params)

        # The first one has a status of accepted (not cancelable), hence the 422
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @ddt.data(
        [AssignmentLearnerStates.WAITING, AssignmentLearnerStates.FAILED],
        [AssignmentLearnerStates.WAITING],
    )
    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient', autospec=True)
    @mock.patch.object(SubsidyAccessPolicy, 'subsidy_record', autospec=True)
    def test_learner_state_query_param_filter(self, learner_states_to_query, mock_subsidy_record, mock_catalog_client):
        """
        Test that the list view supports filtering on one or more ``learner_state`` values via a query parameter.
        """
        # Set the JWT-based auth that we'll use for every request.
        self.set_jwt_cookie([{'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}])

        # Mock results from the catalog content metadata API endpoint.
        mock_catalog_client.return_value.catalog_content_metadata.return_value = self.mock_catalog_result

        # Mock results from the subsidy record.
        mock_subsidy_record.return_value = self.mock_subsidy_record

        # Fetch our list of assignments associated with the test enterprise.
        assignments_for_enterprise_customer = LearnerContentAssignment.objects.filter(
            assignment_configuration__enterprise_customer_uuid=TEST_ENTERPRISE_UUID
        )
        # Double check we have stuff to work with
        assert assignments_for_enterprise_customer.count() > 1

        # Hit the view with a learner_state query param.
        learner_state_query_param_value = ",".join(learner_states_to_query)
        response = self.client.get(
            ADMIN_ASSIGNMENTS_LIST_ENDPOINT + f"?learner_state__in={learner_state_query_param_value}"
        )
        # Assert the results only contain the requested ``learner_state`` values.
        for assignment in response.json().get('results'):
            assert assignment.get('learner_state') in learner_states_to_query

    @ddt.data(
        [LearnerContentAssignmentStateChoices.ALLOCATED, LearnerContentAssignmentStateChoices.ERRORED],
        [LearnerContentAssignmentStateChoices.ALLOCATED],
    )
    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient', autospec=True)
    @mock.patch.object(SubsidyAccessPolicy, 'subsidy_record', autospec=True)
    def test_multi_state_query_param_filter(self, states_to_query, mock_subsidy_record, mock_catalog_client):
        """
        Test that the list view supports filtering on one or more ``state`` values via a query parameter.
        """
        # Set the JWT-based auth that we'll use for every request.
        self.set_jwt_cookie([{'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}])

        # Mock results from the catalog content metadata API endpoint.
        mock_catalog_client.return_value.catalog_content_metadata.return_value = self.mock_catalog_result

        # Mock results from the subsidy record.
        mock_subsidy_record.return_value = self.mock_subsidy_record

        # Fetch our list of assignments associated with the test enterprise.
        assignments_for_enterprise_customer = LearnerContentAssignment.objects.filter(
            assignment_configuration__enterprise_customer_uuid=TEST_ENTERPRISE_UUID
        )
        # Double check we have stuff to work with
        assert assignments_for_enterprise_customer.count() > 1

        # Hit the view with a state query param.
        state_query_param_value = ",".join(states_to_query)
        response = self.client.get(
            ADMIN_ASSIGNMENTS_LIST_ENDPOINT + f"?state__in={state_query_param_value}"
        )
        # Assert the results only contain the requested ``state`` values.
        for assignment in response.json().get('results'):
            assert assignment.get('state') in states_to_query

    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient', autospec=True)
    @mock.patch.object(SubsidyAccessPolicy, 'subsidy_record', autospec=True)
    def test_assignment_search_query_param(self, mock_subsidy_record, mock_catalog_client):
        """
        Test that the list view follows the default Django API filtering with the usage of the ``search`` query param.
        Currently the only two defined look up fields are ``content_title`` and ``learner_email``.
        """
        # Mock results from the catalog content metadata API endpoint.
        mock_catalog_client.return_value.catalog_content_metadata.return_value = self.mock_catalog_result

        # Mock results from the subsidy record.
        mock_subsidy_record.return_value = self.mock_subsidy_record

        # Set the JWT-based auth that we'll use for every request.
        self.set_jwt_cookie([{'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}])

        # Fetch our list of assignments associated with the test enterprise.
        assignments_for_enterprise_customer = LearnerContentAssignment.objects.filter(
            assignment_configuration__enterprise_customer_uuid=TEST_ENTERPRISE_UUID
        )
        # Double check we have stuff to work with
        assert assignments_for_enterprise_customer.count() > 1

        # Hit the view with a search query param for the content title of the first assignment.
        first_assignment = assignments_for_enterprise_customer.first()
        response = self.client.get(
            ADMIN_ASSIGNMENTS_LIST_ENDPOINT + f"?search={first_assignment.content_title}"
        )
        # Assert any of the results contain the content title matching the first assignment's.
        for assignment in response.json().get('results'):
            assert assignment.get('content_title') == first_assignment.content_title

        # Hit the view with a search query param for the learner email of another assignment.
        second_assignment = assignments_for_enterprise_customer.last()
        response = self.client.get(
            ADMIN_ASSIGNMENTS_LIST_ENDPOINT + f"?search={second_assignment.learner_email}"
        )
        # Assert any of the results contain the learner email matching the second assignment's.
        for assignment in response.json().get('results'):
            assert assignment.get('learner_email') == second_assignment.learner_email

        # Hit the view with a search query param for a random string that should not match any assignments.
        response = self.client.get(
            ADMIN_ASSIGNMENTS_LIST_ENDPOINT + "?search=random_garbage_random_garbage"
        )
        assert len(response.data.get('results')) == 0


@ddt.ddt
class TestAssignmentAuthorizedCRUD(CRUDViewTestMixin, APITest):
    """
    Test the Learner-facing Assignment API views while successfully authenticated/authorized.
    """

    def setUp(self):
        super().setUp()

        # Mock results from the catalog content metadata API endpoint.
        self.mock_catalog_result = MOCK_CATALOG_RESULT

        # Mock results from the subsidy record.
        self.mock_subsidy_record = MOCK_SUBSIDY_RECORD

    @ddt.data(
        # A good learner role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good admin role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good operator role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
    )
    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient', autospec=True)
    @mock.patch.object(SubsidyAccessPolicy, 'subsidy_record', autospec=True)
    def test_retrieve(self, role_context_dict, mock_subsidy_record, mock_catalog_client):
        """
        Test that the retrieve view returns a 200 response code and the expected results of serialization.
        """
        # Set the JWT-based auth that we'll use for every request.
        self.set_jwt_cookie([role_context_dict])

        # Mock results from the catalog content metadata API endpoint.
        mock_catalog_client.return_value.catalog_content_metadata.return_value = self.mock_catalog_result

        # Mock results from the subsidy record.
        mock_subsidy_record.return_value = self.mock_subsidy_record

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
            'parent_content_key': self.requester_assignment_accepted.parent_content_key,
            'is_assigned_course_run': self.requester_assignment_accepted.is_assigned_course_run,
            'content_title': self.requester_assignment_accepted.content_title,
            'content_quantity': self.requester_assignment_accepted.content_quantity,
            'learner_email': self.requester_assignment_accepted.learner_email,
            'lms_user_id': self.requester_assignment_accepted.lms_user_id,
            'state': LearnerContentAssignmentStateChoices.ACCEPTED,
            'transaction_uuid': str(self.requester_assignment_accepted.transaction_uuid),
            'actions': [
                {
                    'created': action.created.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    'modified': action.modified.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    'uuid': str(action.uuid),
                    'action_type': action.action_type,
                    'completed_at': action.completed_at.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    'error_reason': action.error_reason,
                }
                for action in self.requester_assignment_accepted.actions.order_by('created')
            ],
            'earliest_possible_expiration': {
                'date': (
                    self.requester_assignment_accepted.allocated_at + timedelta(days=NUM_DAYS_BEFORE_AUTO_EXPIRATION)
                ).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                'reason': AssignmentAutomaticExpiredReason.NINETY_DAYS_PASSED,
            }
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
    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient', autospec=True)
    @mock.patch.object(SubsidyAccessPolicy, 'subsidy_record', autospec=True)
    def test_list(self, role_context_dict, mock_subsidy_record, mock_catalog_client):
        """
        Test that the list view returns a 200 response code and the expected (list) results of serialization.

        This also tests that only Assignments for the requesting user are returned.
        """
        # Set the JWT-based auth that we'll use for every request.
        self.set_jwt_cookie([role_context_dict])

        # Mock results from the catalog content metadata API endpoint.
        mock_catalog_client.return_value.catalog_content_metadata.return_value = self.mock_catalog_result

        # Mock results from the subsidy record.
        mock_subsidy_record.return_value = self.mock_subsidy_record

        # Send a list request for all Assignments for the requesting user.
        response = self.client.get(ASSIGNMENTS_LIST_ENDPOINT)

        # Only Assignments that match the following qualifications are returned in paginated response:
        # 1. Assignment is for the requesting user.
        # 2. Assignment is in the requested AssignmentConfiguration.
        expected_assignments_for_requester = [
            self.requester_assignment_accepted,
            self.requester_assignment_cancelled,
            self.requester_assignment_errored,
        ]
        expected_assignment_uuids = {assignment.uuid for assignment in expected_assignments_for_requester}
        actual_assignment_uuids = {UUID(assignment['uuid']) for assignment in response.json()['results']}
        assert actual_assignment_uuids == expected_assignment_uuids


@ddt.ddt
class TestRemindAllCancelAll(CRUDViewTestMixin, APITest):
    """
    Tests for the remind-all and cancel-all actions.
    """
    def setUp(self):  # pylint: disable=super-method-not-called
        """
        We don't need all the extra records created in super().setUp()
        """
        self.client.logout()

    @ddt.data(
        # A good admin role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good operator role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
    )
    def test_remind_all(self, role_context_dict):
        """
        Tests the remind-all view.
        """
        self.set_jwt_cookie([role_context_dict])
        assignment_1 = LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
            lms_user_id=TEST_OTHER_LMS_USER_ID,
            transaction_uuid=None,
            assignment_configuration=self.assignment_configuration,
        )
        assignment_2 = LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
            learner_email=TEST_EMAIL,
            lms_user_id=TEST_USER_ID,
            transaction_uuid=None,
            assignment_configuration=self.assignment_configuration,
        )

        remind_kwargs = {
            'assignment_configuration_uuid': str(self.assignment_configuration.uuid),
        }
        remind_url = reverse('api:v1:admin-assignments-remind-all', kwargs=remind_kwargs)

        with mock.patch(
            'enterprise_access.apps.content_assignments.api.send_reminder_email_for_pending_assignment'
        ) as mock_remind_task:
            response = self.client.post(remind_url)
            mock_remind_task.delay.assert_has_calls(
                [mock.call(assignment_1.uuid), mock.call(assignment_2.uuid)],
                any_order=True,
            )

        # Verify the API response.
        assert response.status_code == status.HTTP_202_ACCEPTED

        for assignment in (assignment_1, assignment_2):
            assignment.refresh_from_db()
            self.assertEqual(assignment.state, LearnerContentAssignmentStateChoices.ALLOCATED)

    def test_learner_remind_all_403(self):
        """
        Learners can't perform the remind-all action.
        """
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        ])

        remind_kwargs = {
            'assignment_configuration_uuid': str(self.assignment_configuration.uuid),
        }
        remind_url = reverse('api:v1:admin-assignments-remind-all', kwargs=remind_kwargs)

        response = self.client.post(remind_url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_remind_all_no_remindable_assignments(self):
        """
        Tests the scenario where there are no assignments in a remindable state.
        """
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        ])

        LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
            lms_user_id=TEST_OTHER_LMS_USER_ID,
            transaction_uuid=None,
            assignment_configuration=self.assignment_configuration,
        )

        remind_kwargs = {
            'assignment_configuration_uuid': str(self.assignment_configuration.uuid),
        }
        remind_url = reverse('api:v1:admin-assignments-remind-all', kwargs=remind_kwargs)

        with mock.patch(
            'enterprise_access.apps.content_assignments.api.remind_assignments'
        ) as mock_remind_function:
            response = self.client.post(remind_url)
            self.assertFalse(mock_remind_function.called)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_remind_all_unlikely_thing_to_happen(self):
        """
        Tests the unlikely scenario where we attempt to remind assignments
        that are not in a remindable state, even after filtering at the view layer
        """
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        ])

        LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
            lms_user_id=TEST_OTHER_LMS_USER_ID,
            transaction_uuid=None,
            assignment_configuration=self.assignment_configuration,
        )

        remind_kwargs = {
            'assignment_configuration_uuid': str(self.assignment_configuration.uuid),
        }
        remind_url = reverse('api:v1:admin-assignments-remind-all', kwargs=remind_kwargs)

        with mock.patch(
            'enterprise_access.apps.content_assignments.api.remind_assignments'
        ) as mock_remind_function:
            mock_remind_function.return_value = {
                'non_remindable_assignments': mock.ANY,
            }
            response = self.client.post(remind_url)

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    @ddt.data(
        # A good admin role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good operator role, and with a context matching the main testing customer.
        {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
    )
    def test_cancel_all(self, role_context_dict):
        """
        Tests the cancel-all view.
        """
        self.set_jwt_cookie([role_context_dict])
        assignment_1 = LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
            lms_user_id=TEST_OTHER_LMS_USER_ID,
            transaction_uuid=None,
            assignment_configuration=self.assignment_configuration,
        )
        assignment_2 = LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
            learner_email=TEST_EMAIL,
            lms_user_id=TEST_USER_ID,
            transaction_uuid=None,
            assignment_configuration=self.assignment_configuration,
        )

        cancel_kwargs = {
            'assignment_configuration_uuid': str(self.assignment_configuration.uuid),
        }
        cancel_url = reverse('api:v1:admin-assignments-cancel-all', kwargs=cancel_kwargs)

        with mock.patch(
            'enterprise_access.apps.content_assignments.tasks.send_cancel_email_for_pending_assignment'
        ) as mock_cancel_task:
            response = self.client.post(cancel_url)
            mock_cancel_task.delay.assert_has_calls(
                [mock.call(assignment_1.uuid), mock.call(assignment_2.uuid)],
                any_order=True,
            )

        # Verify the API response.
        assert response.status_code == status.HTTP_202_ACCEPTED

        for assignment in (assignment_1, assignment_2):
            assignment.refresh_from_db()
            self.assertEqual(assignment.state, LearnerContentAssignmentStateChoices.CANCELLED)

    def test_learner_cancel_all_403(self):
        """
        Learners can't perform the cancel-all action.
        """
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        ])

        cancel_kwargs = {
            'assignment_configuration_uuid': str(self.assignment_configuration.uuid),
        }
        cancel_url = reverse('api:v1:admin-assignments-cancel-all', kwargs=cancel_kwargs)

        response = self.client.post(cancel_url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cancel_all_no_cancelable_assignments(self):
        """
        Tests the scenario where there are no assignments in a cancelable state.
        """
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        ])

        LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
            lms_user_id=TEST_OTHER_LMS_USER_ID,
            transaction_uuid=None,
            assignment_configuration=self.assignment_configuration,
        )

        cancel_kwargs = {
            'assignment_configuration_uuid': str(self.assignment_configuration.uuid),
        }
        cancel_url = reverse('api:v1:admin-assignments-cancel-all', kwargs=cancel_kwargs)

        with mock.patch(
            'enterprise_access.apps.content_assignments.api.cancel_assignments'
        ) as mock_cancel_function:
            response = self.client.post(cancel_url)
            self.assertFalse(mock_cancel_function.called)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cancel_all_unlikely_thing_to_happen(self):
        """
        Tests the unlikely scenario where we attempt to cancel assignments
        that are not in a cancelable state, even after filtering at the view layer
        """
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        ])

        LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
            lms_user_id=TEST_OTHER_LMS_USER_ID,
            transaction_uuid=None,
            assignment_configuration=self.assignment_configuration,
        )

        cancel_kwargs = {
            'assignment_configuration_uuid': str(self.assignment_configuration.uuid),
        }
        cancel_url = reverse('api:v1:admin-assignments-cancel-all', kwargs=cancel_kwargs)

        with mock.patch(
            'enterprise_access.apps.content_assignments.api.cancel_assignments'
        ) as mock_cancel_function:
            mock_cancel_function.return_value = {
                'non_cancelable': mock.ANY,
            }
            response = self.client.post(cancel_url)

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)


class TestFilteredRemindAllCancelAll(CRUDViewTestMixin, APITest):
    """
    Tests for the remind-all and cancel-all actions when filters are provided in the request query.
    """
    def test_cancel_all_filter_multiple_learner_states(self):
        """
        Tests the cancel-all view with a provided filter on multiple learner_states.
        """
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])
        learner_states_to_query = [
            AssignmentLearnerStates.WAITING,
            AssignmentLearnerStates.FAILED,
            AssignmentLearnerStates.NOTIFYING,
        ]
        cancel_kwargs = {
            'assignment_configuration_uuid': str(self.assignment_configuration.uuid),
        }
        cancel_url = reverse('api:v1:admin-assignments-cancel-all', kwargs=cancel_kwargs)
        learner_state_query_param_value = ",".join(learner_states_to_query)
        cancel_url += f'?learner_state__in={learner_state_query_param_value}'

        expected_cancelled_assignments = [
            self.assignment_allocated_pre_link,
            self.assignment_allocated_post_link,
            self.requester_assignment_errored,
        ]
        with mock.patch(
            'enterprise_access.apps.content_assignments.tasks.send_cancel_email_for_pending_assignment'
        ) as mock_cancel_task:
            response = self.client.post(cancel_url)

            assert response.status_code == status.HTTP_202_ACCEPTED
            mock_cancel_task.delay.assert_has_calls(
                [mock.call(assignment.uuid) for assignment in expected_cancelled_assignments],
                any_order=True,
            )
            assert mock_cancel_task.delay.call_count == len(expected_cancelled_assignments)
            for assignment in expected_cancelled_assignments:
                assignment.refresh_from_db()
                self.assertEqual(assignment.state, LearnerContentAssignmentStateChoices.CANCELLED)

    def test_cancel_all_filter_single_learner_state(self):
        """
        Tests the cancel-all view with a provided filter on a single learner_state.
        """
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])
        cancel_kwargs = {
            'assignment_configuration_uuid': str(self.assignment_configuration.uuid),
        }
        cancel_url = reverse('api:v1:admin-assignments-cancel-all', kwargs=cancel_kwargs)
        cancel_url += f'?learner_state={AssignmentLearnerStates.WAITING}'

        expected_cancelled_assignments = [
            self.assignment_allocated_post_link,
        ]
        with mock.patch(
            'enterprise_access.apps.content_assignments.tasks.send_cancel_email_for_pending_assignment'
        ) as mock_cancel_task:
            response = self.client.post(cancel_url)

            assert response.status_code == status.HTTP_202_ACCEPTED
            mock_cancel_task.delay.assert_has_calls(
                [mock.call(assignment.uuid) for assignment in expected_cancelled_assignments],
                any_order=True,
            )
            assert mock_cancel_task.delay.call_count == len(expected_cancelled_assignments)
            for assignment in expected_cancelled_assignments:
                assignment.refresh_from_db()
                self.assertEqual(assignment.state, LearnerContentAssignmentStateChoices.CANCELLED)

    def test_remind_all_filter_multiple_learner_states(self):
        """
        Tests the remind-all view with a provided filter on multiple learner_states.
        """
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])
        learner_states_to_query = [
            AssignmentLearnerStates.WAITING,
            AssignmentLearnerStates.FAILED,
            AssignmentLearnerStates.NOTIFYING,
        ]
        remind_kwargs = {
            'assignment_configuration_uuid': str(self.assignment_configuration.uuid),
        }
        remind_url = reverse('api:v1:admin-assignments-remind-all', kwargs=remind_kwargs)
        learner_state_query_param_value = ",".join(learner_states_to_query)
        remind_url += f'?learner_state__in={learner_state_query_param_value}'

        expected_reminded_assignments = [
            self.assignment_allocated_pre_link,
            self.assignment_allocated_post_link,
        ]
        with mock.patch(
            'enterprise_access.apps.content_assignments.api.send_reminder_email_for_pending_assignment'
        ) as mock_remind_task:
            response = self.client.post(remind_url)

            assert response.status_code == status.HTTP_202_ACCEPTED
            mock_remind_task.delay.assert_has_calls(
                [mock.call(assignment.uuid) for assignment in expected_reminded_assignments],
                any_order=True,
            )
            assert mock_remind_task.delay.call_count == len(expected_reminded_assignments)
            for assignment in expected_reminded_assignments:
                assignment.refresh_from_db()
                self.assertEqual(assignment.state, LearnerContentAssignmentStateChoices.ALLOCATED)

    def test_remind_all_filter_single_learner_state(self):
        """
        Tests the remind-all view with a provided filter on a single learner_state.
        """
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])
        remind_kwargs = {
            'assignment_configuration_uuid': str(self.assignment_configuration.uuid),
        }
        remind_url = reverse('api:v1:admin-assignments-remind-all', kwargs=remind_kwargs)
        remind_url += f'?learner_state={AssignmentLearnerStates.WAITING}'

        expected_reminded_assignments = [
            self.assignment_allocated_post_link,
        ]
        with mock.patch(
            'enterprise_access.apps.content_assignments.api.send_reminder_email_for_pending_assignment'
        ) as mock_remind_task:
            response = self.client.post(remind_url)

            assert response.status_code == status.HTTP_202_ACCEPTED
            mock_remind_task.delay.assert_has_calls(
                [mock.call(assignment.uuid) for assignment in expected_reminded_assignments],
                any_order=True,
            )
            assert mock_remind_task.delay.call_count == len(expected_reminded_assignments)
            for assignment in expected_reminded_assignments:
                assignment.refresh_from_db()
                self.assertEqual(assignment.state, LearnerContentAssignmentStateChoices.ALLOCATED)
