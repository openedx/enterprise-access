"""
Tests for Subsidy Access Policy Assignment Allocation view(s).
"""
from unittest import mock
from uuid import uuid4

import ddt
from django.core.cache import cache as django_cache
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
from enterprise_access.apps.subsidy_access_policy.models import AssignedLearnerCreditAccessPolicy
from enterprise_access.apps.subsidy_access_policy.tests.factories import AssignedLearnerCreditAccessPolicyFactory
from test_utils import APITest, APITestWithMocks

SUBSIDY_ACCESS_POLICY_LIST_ENDPOINT = reverse('api:v1:subsidy-access-policies-list')

TEST_ENTERPRISE_UUID = uuid4()


def _can_allocate_url(policy_uuid):
    return reverse(
        "api:v1:policy-allocation-allocate",
        kwargs={"policy_uuid": policy_uuid},
    )


@ddt.ddt
class TestSubsidyAccessPolicyAllocationView(APITestWithMocks):
    """
    Tests for the ``allocate`` view.
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.enterprise_uuid = TEST_ENTERPRISE_UUID
        cls.content_key = 'course-v1:edX+edXPrivacy101+3T2020'

        # Create a pair of AssignmentConfiguration + SubsidyAccessPolicy for the main test customer.
        cls.assignment_configuration = AssignmentConfigurationFactory(
            enterprise_customer_uuid=cls.enterprise_uuid,
        )
        cls.assigned_learner_credit_policy = AssignedLearnerCreditAccessPolicyFactory(
            display_name='An assigned learner credit policy, for the test customer.',
            enterprise_customer_uuid=cls.enterprise_uuid,
            active=True,
            assignment_configuration=cls.assignment_configuration,
            spend_limit=1000000,
        )

        cls.alice_assignment = LearnerContentAssignmentFactory(
            assignment_configuration=cls.assignment_configuration,
            learner_email='alice@foo.com',
            lms_user_id=None,
            content_key=cls.content_key,
            content_quantity=-123,
            state=LearnerContentAssignmentStateChoices.ERRORED,
        )
        cls.bob_assignment = LearnerContentAssignmentFactory(
            assignment_configuration=cls.assignment_configuration,
            learner_email='bob@foo.com',
            lms_user_id=None,
            content_key=cls.content_key,
            content_quantity=-456,
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
        )
        cls.carol_assignment = LearnerContentAssignmentFactory(
            assignment_configuration=cls.assignment_configuration,
            learner_email='carol@foo.com',
            lms_user_id=None,
            content_key=cls.content_key,
            content_quantity=-789,
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
        )

    def setUp(self):
        super().setUp()

        self.enterprise_uuid = TEST_ENTERPRISE_UUID

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_uuid),
        }])
        self.addCleanup(django_cache.clear)  # clear any leftover allocation locks

    @mock.patch.object(AssignedLearnerCreditAccessPolicy, 'can_allocate', autospec=True)
    @mock.patch(
        'enterprise_access.apps.subsidy_access_policy.models.assignments_api.allocate_assignments',
        autospec=True,
    )
    def test_allocate_happy_path(self, mock_allocate, mock_can_allocate):
        """
        Tests that we can successfully call the allocate view
        and that policy-level allocation occurs.
        """
        mock_can_allocate.return_value = (True, None)
        mock_allocate.return_value = {
            'updated': [self.alice_assignment],
            'created': [self.bob_assignment],
            'no_change': [self.carol_assignment],
        }

        allocate_url = _can_allocate_url(self.assigned_learner_credit_policy.uuid)
        allocate_payload = {
            'learner_emails': ['alice@foo.com', 'bob@foo.com', 'carol@foo.com'],
            'content_key': self.content_key,
            'content_price_cents': -12345,
        }

        response = self.client.post(allocate_url, data=allocate_payload)

        self.assertEqual(status.HTTP_202_ACCEPTED, response.status_code)
        expected_response_payload = {
            'updated': [
                {
                    'assignment_configuration': str(self.assignment_configuration.uuid),
                    'learner_email': 'alice@foo.com',
                    'lms_user_id': None,
                    'content_key': self.content_key,
                    'content_quantity': -123,
                    'last_notification_at': None,
                    'state': LearnerContentAssignmentStateChoices.ERRORED,
                    'transaction_uuid': None,
                    'uuid': str(self.alice_assignment.uuid),
                },
            ],
            'created': [
                {
                    'assignment_configuration': str(self.assignment_configuration.uuid),
                    'learner_email': 'bob@foo.com',
                    'lms_user_id': None,
                    'content_key': self.content_key,
                    'content_quantity': -456,
                    'last_notification_at': None,
                    'state': LearnerContentAssignmentStateChoices.ALLOCATED,
                    'transaction_uuid': None,
                    'uuid': str(self.bob_assignment.uuid),
                },
            ],
            'no_change': [
                {
                    'assignment_configuration': str(self.assignment_configuration.uuid),
                    'learner_email': 'carol@foo.com',
                    'lms_user_id': None,
                    'content_key': self.content_key,
                    'content_quantity': -789,
                    'last_notification_at': None,
                    'state': LearnerContentAssignmentStateChoices.ALLOCATED,
                    'transaction_uuid': None,
                    'uuid': str(self.carol_assignment.uuid),
                },
            ],
        }
        self.assertEqual(expected_response_payload, response.json())
        mock_can_allocate.assert_called_once_with(
            self.assigned_learner_credit_policy,
            len(allocate_payload['learner_emails']),
            allocate_payload['content_key'],
            allocate_payload['content_price_cents'],
        )
        mock_allocate.assert_called_once_with(
            self.assignment_configuration,
            allocate_payload['learner_emails'],
            allocate_payload['content_key'],
            allocate_payload['content_price_cents'],
        )

    @mock.patch.object(AssignedLearnerCreditAccessPolicy, 'can_allocate', autospec=True)
    @mock.patch(
        'enterprise_access.apps.subsidy_access_policy.models.assignments_api.allocate_assignments',
        autospec=True,
    )
    def test_cannot_allocate(self, mock_allocate, mock_can_allocate):
        """
        When the policy is un-allocatable, a request to allocate results in a
        422 response and no allocation takes place.
        """
        mock_can_allocate.return_value = (False, 'some-reason')

        allocate_url = _can_allocate_url(self.assigned_learner_credit_policy.uuid)
        allocate_payload = {
            'learner_emails': ['alice@foo.com', 'bob@foo.com', 'carol@foo.com'],
            'content_key': self.content_key,
            'content_price_cents': -12345,
        }

        response = self.client.post(allocate_url, data=allocate_payload)

        self.assertEqual(status.HTTP_422_UNPROCESSABLE_ENTITY, response.status_code)
        self.assertEqual(
            {'detail': 'some-reason'},
            response.json(),
        )
        mock_can_allocate.assert_called_once_with(
            self.assigned_learner_credit_policy,
            len(allocate_payload['learner_emails']),
            allocate_payload['content_key'],
            allocate_payload['content_price_cents'],
        )
        self.assertFalse(mock_allocate.called)

    @mock.patch.object(AssignedLearnerCreditAccessPolicy, 'can_allocate', autospec=True)
    @mock.patch(
        'enterprise_access.apps.subsidy_access_policy.models.assignments_api.allocate_assignments',
        autospec=True,
    )
    def test_cannot_allocate_positive_quantities(self, mock_allocate, mock_can_allocate):
        """
        Validate that you cannot request a positive amount of cents to allocate
        for a content key.
        """
        allocate_url = _can_allocate_url(self.assigned_learner_credit_policy.uuid)
        allocate_payload = {
            'learner_emails': ['alice@foo.com', 'bob@foo.com', 'carol@foo.com'],
            'content_key': self.content_key,
            'content_price_cents': 1,
        }

        response = self.client.post(allocate_url, data=allocate_payload)

        self.assertEqual(status.HTTP_400_BAD_REQUEST, response.status_code)
        self.assertEqual(
            {'content_price_cents': ['Ensure this value is less than or equal to 0.']},
            response.json(),
        )
        self.assertFalse(mock_allocate.called)
        self.assertFalse(mock_can_allocate.called)

    @mock.patch.object(AssignedLearnerCreditAccessPolicy, 'can_allocate', autospec=True)
    @mock.patch(
        'enterprise_access.apps.subsidy_access_policy.models.assignments_api.allocate_assignments',
        autospec=True,
    )
    def test_cannot_allocate_locked(self, mock_allocate, mock_can_allocate):
        """
        When the policy is currently locked, a request to allocate should
        result in a 429 response and no allocation takes place.
        """
        mock_can_allocate.return_value = (True, None)

        allocate_url = _can_allocate_url(self.assigned_learner_credit_policy.uuid)
        allocate_payload = {
            'learner_emails': ['alice@foo.com', 'bob@foo.com', 'carol@foo.com'],
            'content_key': self.content_key,
            'content_price_cents': -12345,
        }

        # manually acquire a lock on our policy before the request is made
        self.assigned_learner_credit_policy.acquire_lock()

        response = self.client.post(allocate_url, data=allocate_payload)

        self.assigned_learner_credit_policy.release_lock()

        self.assertEqual(status.HTTP_429_TOO_MANY_REQUESTS, response.status_code)
        self.assertEqual(
            {'detail': 'Enrollment currently locked for this subsidy access policy.'},
            response.json(),
        )
        self.assertFalse(mock_can_allocate.called)
        self.assertFalse(mock_allocate.called)


@ddt.ddt
class TestAssignmentConfigurationUnauthorizedCRUD(APITest):
    """
    Tests Authentication and Permission checking for AssignmentConfiguration CRUD views.
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.enterprise_uuid = TEST_ENTERPRISE_UUID

        # Create a pair of AssignmentConfiguration + SubsidyAccessPolicy for the main test customer.
        cls.assignment_configuration = AssignmentConfigurationFactory(
            enterprise_customer_uuid=cls.enterprise_uuid,
        )
        cls.assigned_learner_credit_policy = AssignedLearnerCreditAccessPolicyFactory(
            display_name='An assigned learner credit policy, for the test customer.',
            enterprise_customer_uuid=cls.enterprise_uuid,
            active=True,
            assignment_configuration=cls.assignment_configuration,
            spend_limit=1000000,
        )

    @ddt.data(
        # A role that's not mapped to any feature perms will get you a 403.
        (
            {'system_wide_role': 'some-other-role', 'context': str(TEST_ENTERPRISE_UUID)},
            status.HTTP_403_FORBIDDEN,
        ),
        # A good learner role, AND in the correct context/customer STILL gets you a 403.
        # Allocation APIs are inaccessible to all learners.
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
    def test_allocation_view_unauthorized_forbidden(self, role_context_dict, expected_response_code):
        """
        Tests that we get expected 40x responses for the allocation view
        """
        # Set the JWT-based auth that we'll use for every request
        if role_context_dict:
            self.set_jwt_cookie([role_context_dict])

        allocate_url = _can_allocate_url(self.assigned_learner_credit_policy.uuid)
        allocate_payload = {
            'learner_emails': ['alice@foo.com', 'bob@foo.com'],
            'content_key': 'the-content-key',
            'content_price_cents': -12345,
        }

        response = self.client.post(allocate_url, data=allocate_payload)

        assert response.status_code == expected_response_code
