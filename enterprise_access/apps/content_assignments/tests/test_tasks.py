"""
Tests for Enterprise Access content_assignments tasks.
"""
from unittest import mock
from uuid import uuid4

import ddt
from celery import states as celery_states
from django.conf import settings
from requests.exceptions import HTTPError
from rest_framework import status

from enterprise_access.apps.api_client.tests.test_utils import MockResponse
from enterprise_access.apps.content_assignments.constants import (
    AssignmentActionErrors,
    AssignmentActions,
    LearnerContentAssignmentStateChoices
)
from enterprise_access.apps.content_assignments.tasks import (
    create_pending_enterprise_learner_for_assignment_task,
    send_assignment_automatically_expired_email,
    send_cancel_email_for_pending_assignment,
    send_email_for_new_assignment,
    send_reminder_email_for_pending_assignment
)
from enterprise_access.apps.content_assignments.tests.factories import (
    AssignmentConfigurationFactory,
    LearnerContentAssignmentFactory
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    AssignedLearnerCreditAccessPolicyFactory,
    PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory
)
from test_utils import APITestWithMocks

TEST_ENTERPRISE_UUID = uuid4()
TEST_EMAIL = 'foo@bar.com'
TEST_LMS_USER_ID = 2
TEST_ASSIGNMENT_UUID = uuid4()


@ddt.ddt
class TestCreatePendingEnterpriseLearnerForAssignmentTask(APITestWithMocks):
    """
    Test create_pending_enterprise_learner_for_assignment_task().
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        # Create a pair of AssignmentConfiguration + SubsidyAccessPolicy for the main test customer.
        cls.assignment_configuration = AssignmentConfigurationFactory(
            enterprise_customer_uuid=TEST_ENTERPRISE_UUID,
        )
        cls.assigned_learner_credit_policy = AssignedLearnerCreditAccessPolicyFactory(
            display_name='An assigned learner credit policy, for the test customer.',
            enterprise_customer_uuid=TEST_ENTERPRISE_UUID,
            active=True,
            assignment_configuration=cls.assignment_configuration,
            spend_limit=1000000,
        )

    def setUp(self):
        super().setUp()

        self.assignment = LearnerContentAssignmentFactory(
            learner_email=TEST_EMAIL,
            assignment_configuration=self.assignment_configuration,
        )

    @ddt.data(
        # The LMS API did not find an existing PendingEnterpriseLearner, so it created one.
        {
            'mock_lms_response_status': status.HTTP_201_CREATED,
            'mock_lms_response_body': {
                'enterprise_customer': str(TEST_ENTERPRISE_UUID),
                'user_email': TEST_EMAIL,
            },
        },
        # The LMS API found an existing PendingEnterpriseLearner.
        {
            'mock_lms_response_status': status.HTTP_204_NO_CONTENT,
            'mock_lms_response_body': None,
        },
    )
    @ddt.unpack
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_happy_path(self, mock_oauth_client, mock_lms_response_status, mock_lms_response_body):
        """
        2xx response form the LMS API should cause the task to run successfully.
        """
        mock_oauth_client.return_value.post.return_value = MockResponse(
            mock_lms_response_body,
            mock_lms_response_status,
        )

        task_result = create_pending_enterprise_learner_for_assignment_task.delay(self.assignment.uuid)

        # Celery thinks the task succeeded.
        assert task_result.state == celery_states.SUCCESS

        # The LMS/enterprise API was called once only, and with the correct request body.
        assert len(mock_oauth_client.return_value.post.call_args_list) == 1
        assert mock_oauth_client.return_value.post.call_args.kwargs['json'] == [{
            'enterprise_customer': str(self.assignment.assignment_configuration.enterprise_customer_uuid),
            'user_email': self.assignment.learner_email,
        }]

        # Make sure the assignment state doesn't change from allocated.
        self.assignment.refresh_from_db()
        assert self.assignment.state == LearnerContentAssignmentStateChoices.ALLOCATED

    @ddt.data(
        # 503 is a prototypical "please retry this endpoint" status.
        status.HTTP_503_SERVICE_UNAVAILABLE,
        # 400 should really not trigger retry, but it does.  We should improve LoggedTaskWithRetry to make it not retry!
        status.HTTP_400_BAD_REQUEST,
    )
    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_max_retries(self, response_status_that_triggers_retry, mock_oauth_client):
        """
        On repeated error responses from the LMS/enterprise API, the celery worker should retry the task until the
        maximum number of retries configured, then set the assignment state to ERRORED.
        """
        mock_oauth_client.return_value.post.return_value = MockResponse(
            {
                'enterprise_customer': str(TEST_ENTERPRISE_UUID),
                'user_email': TEST_EMAIL,
            },
            response_status_that_triggers_retry,
        )

        task_result = create_pending_enterprise_learner_for_assignment_task.delay(self.assignment.uuid)

        # Celery thinks the task failed.
        assert task_result.state == celery_states.FAILURE

        # The overall task result is just the HTTPError bubbled up from the API response.
        assert isinstance(task_result.result, HTTPError)
        assert task_result.result.response.status_code == response_status_that_triggers_retry

        # The LMS/enterprise API was called once plus the max number of retries, all with the correct request body.
        assert len(mock_oauth_client.return_value.post.call_args_list) == 1 + settings.TASK_MAX_RETRIES
        for call in mock_oauth_client.return_value.post.call_args_list:
            assert call.kwargs['json'] == [{
                'enterprise_customer': str(self.assignment.assignment_configuration.enterprise_customer_uuid),
                'user_email': self.assignment.learner_email,
            }]

        # Finally, make sure the on_failure() handler successfully updated assignment state to errored.
        self.assignment.refresh_from_db()
        assert self.assignment.state == LearnerContentAssignmentStateChoices.ERRORED
        action = self.assignment.actions.filter(action_type=AssignmentActions.LEARNER_LINKED).first()
        self.assertIn('HTTPError', action.traceback)
        self.assertEqual(action.error_reason, AssignmentActionErrors.INTERNAL_API_ERROR)

    @mock.patch('enterprise_access.apps.api_client.base_oauth.OAuthAPIClient')
    def test_last_retry_success(self, mock_oauth_client):
        """
        Test a scenario where the API response keeps triggering a retry until the last attempt, then finally responds
        successfully.
        """
        # Mock multiple consecutive responses, only the last of which was successful.
        retry_triggering_responses = [
            MockResponse(None, status.HTTP_503_SERVICE_UNAVAILABLE)
            for _ in range(settings.TASK_MAX_RETRIES)
        ]
        final_success_response = MockResponse(
            {
                'enterprise_customer': str(TEST_ENTERPRISE_UUID),
                'user_email': TEST_EMAIL,
            },
            status.HTTP_201_CREATED,
        )
        mock_oauth_client.return_value.post.side_effect = retry_triggering_responses + [final_success_response]

        task_result = create_pending_enterprise_learner_for_assignment_task.delay(self.assignment.uuid)

        # Celery thinks the task succeeded.
        assert task_result.state == celery_states.SUCCESS

        # The LMS/enterprise API was called once plus the max number of retries, all with the correct request body.
        assert len(mock_oauth_client.return_value.post.call_args_list) == 1 + settings.TASK_MAX_RETRIES
        for call in mock_oauth_client.return_value.post.call_args_list:
            assert call.kwargs['json'] == [{
                'enterprise_customer': str(self.assignment.assignment_configuration.enterprise_customer_uuid),
                'user_email': self.assignment.learner_email,
            }]

        # Make sure the assignment state does NOT change to errored.
        self.assignment.refresh_from_db()
        assert self.assignment.state == LearnerContentAssignmentStateChoices.ALLOCATED


@ddt.ddt
class TestBrazeEmailTasks(APITestWithMocks):
    """
    Verify cancel and remind emails hit braze client with expected args
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.assignment_configuration = AssignmentConfigurationFactory(
            enterprise_customer_uuid=TEST_ENTERPRISE_UUID,
            uuid=TEST_ASSIGNMENT_UUID,
        )

    def setUp(self):
        super().setUp()
        self.course_name = 'test-course-name'
        self.enterprise_customer_name = 'test-customer-name'
        self.assignment = LearnerContentAssignmentFactory(
            uuid=TEST_ASSIGNMENT_UUID,
            learner_email='TESTING THIS EMAIL',
            lms_user_id=TEST_LMS_USER_ID,
            assignment_configuration=self.assignment_configuration,
        )
        self.policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory()

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.objects')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.LmsApiClient')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.BrazeApiClient')
    def test_send_cancel_email_for_pending_assignment(
        self, mock_braze_client, mock_lms_client,
        mock_policy_model,  # pylint: disable=unused-argument
    ):
        """
        Verify send_cancel_email_for_pending_assignment hits braze client with expected args
        """
        admin_email = 'test@admin.com'
        mock_lms_client.return_value.get_enterprise_customer_data.return_value = {
            'uuid': TEST_ENTERPRISE_UUID,
            'slug': 'test-slug',
            'admin_users': [{
                'email': admin_email,
                'lms_user_id': 1
            }],
            'name': self.enterprise_customer_name,
        }
        mock_recipient = {
            'external_user_id': 1
        }

        mock_admin_mailto = f'mailto:{admin_email}'
        mock_braze_client.return_value.create_recipient.return_value = mock_recipient
        mock_braze_client.return_value.generate_mailto_link.return_value = mock_admin_mailto
        send_cancel_email_for_pending_assignment(self.assignment.uuid)

        # Make sure our LMS client got called correct times and with what we expected
        mock_lms_client.return_value.get_enterprise_customer_data.assert_called_with(
            self.assignment_configuration.enterprise_customer_uuid
        )

        mock_braze_client.return_value.send_campaign_message.assert_any_call(
            'test-assignment-cancelled-campaign',
            recipients=[mock_recipient],
            trigger_properties={
                'contact_admin_link': mock_admin_mailto,
                'organization': self.enterprise_customer_name,
                'course_title': self.assignment.content_title
            },
        )
        assert mock_braze_client.return_value.send_campaign_message.call_count == 1

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.objects')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.get_content_metadata_for_assignments')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.LmsApiClient')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.BrazeApiClient')
    @ddt.data(True, False)
    def test_send_reminder_email_for_pending_assignment(
        self, is_logistrated, mock_braze_client, mock_lms_client, mock_get_metadata,
        mock_policy_model,  # pylint: disable=unused-argument
    ):
        """
        Verify send_reminder_email_for_pending_assignment hits braze client with expected args
        """
        if not is_logistrated:
            self.assignment.lms_user_id = None
            self.assignment.save()

        content_key = 'demoX'
        admin_email = 'test@admin.com'
        mock_lms_client.return_value.get_enterprise_customer_data.return_value = {
            'uuid': TEST_ENTERPRISE_UUID,
            'slug': 'test-slug',
            'admin_users': [{
                'email': admin_email,
                'lms_user_id': 1
            }],
            'name': self.enterprise_customer_name,
        }
        mock_recipient = {
            'external_user_id': 1
        }
        mock_get_metadata.return_value = {
            'key': content_key,
            'normalized_metadata': {
                'start_date': '2020-01-01 12:00:00Z',
                'end_date': '2022-01-01 12:00:00Z',
                'enroll_by_date': '2021-01-01 12:00:00Z',
                'content_price': 123,
            },
            'owners': [
                {'name': 'Smart Folks', 'logo_image_url': 'http://pictures.yes'},
                {'name': 'Good People', 'logo_image_url': 'http://pictures.nice'},
                {'name': 'Fast Learners', 'logo_image_url': 'http://pictures.totally'},
            ],
            'card_image_url': 'https://itsanimage.com'
        }

        mock_admin_mailto = f'mailto:{admin_email}'
        mock_braze_client.return_value.create_recipient.return_value = mock_recipient
        mock_braze_client.return_value.generate_mailto_link.return_value = mock_admin_mailto
        send_reminder_email_for_pending_assignment(self.assignment.uuid)

        # Make sure our LMS client got called correct times and with what we expected
        mock_lms_client.return_value.get_enterprise_customer_data.assert_called_with(
            self.assignment_configuration.enterprise_customer_uuid
        )

        assert mock_braze_client.return_value.send_campaign_message.call_count == 1
        expected_campaign_identifier = 'test-assignment-remind-campaign'
        if is_logistrated:
            expected_campaign_identifier = 'test-assignment-remind-post-logistration-campaign'
        mock_braze_client.return_value.send_campaign_message.assert_called_once_with(
            expected_campaign_identifier,
            recipients=[mock_recipient],
            trigger_properties={
                'contact_admin_link': mock_admin_mailto,
                'organization': self.enterprise_customer_name,
                'course_title': self.assignment.content_title,
                'enrollment_deadline': 'Jan 01, 2021',
                'start_date': 'Jan 01, 2020',
                'course_partner': 'Smart Folks, Good People, and Fast Learners',
                'course_card_image': 'https://itsanimage.com',
                'learner_portal_link': 'http://enterprise-learner-portal.example.com/test-slug'
            },
        )

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.objects')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.get_content_metadata_for_assignments')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.LmsApiClient')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.BrazeApiClient')
    def test_send_email_for_new_assignment(
        self,
        mock_braze_client,
        mock_lms_client,
        mock_get_metadata,
        mock_policy_model   # pylint: disable=unused-argument
    ):
        """
        Verify send_email_for_new_assignment hits braze client with expected args
        """
        content_key = 'demoX'
        admin_email = 'test@admin.com'
        mock_lms_client.return_value.get_enterprise_customer_data.return_value = {
            'uuid': TEST_ENTERPRISE_UUID,
            'slug': 'test-slug',
            'admin_users': [{
                'email': admin_email,
                'lms_user_id': 1
            }],
            'name': self.enterprise_customer_name,
        }
        mock_recipient = {
            'external_user_id': 1
        }
        mock_get_metadata.return_value = {
            'key': content_key,
            'normalized_metadata': {
                'start_date': '2020-01-01T12:00:00Z',
                'end_date': '2022-01-01 12:00:00Z',
                'enroll_by_date': '2021-01-01T12:00:00Z',
                'content_price': 123,
            },
            'owners': [
                {'name': 'Smart Folks', 'logo_image_url': 'http://pictures.yes'},
                {'name': 'Good People', 'logo_image_url': 'http://pictures.nice'},
            ],
            'card_image_url': 'https://itsanimage.com',
        }

        mock_admin_mailto = f'mailto:{admin_email}'
        mock_braze_client.return_value.create_recipient.return_value = mock_recipient
        mock_braze_client.return_value.generate_mailto_link.return_value = mock_admin_mailto
        send_email_for_new_assignment(self.assignment.uuid)

        # Make sure our LMS client got called correct times and with what we expected
        mock_lms_client.return_value.get_enterprise_customer_data.assert_called_with(
            self.assignment_configuration.enterprise_customer_uuid
        )

        mock_braze_client.return_value.send_campaign_message.assert_any_call(
            'test-assignment-notification-campaign',
            recipients=[mock_recipient],
            trigger_properties={
                'contact_admin_link': mock_admin_mailto,
                'organization': self.enterprise_customer_name,
                'course_title': self.assignment.content_title,
                'enrollment_deadline': 'Jan 01, 2021',
                'start_date': 'Jan 01, 2020',
                'course_partner': 'Smart Folks and Good People',
                'course_card_image': 'https://itsanimage.com',
                'learner_portal_link': '{}/{}'.format(settings.ENTERPRISE_LEARNER_PORTAL_URL, 'test-slug'),
            },
        )
        assert mock_braze_client.return_value.send_campaign_message.call_count == 1

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.objects')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.LmsApiClient')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.BrazeApiClient')
    def test_send_assignment_automatically_expired_email(
        self, mock_braze_client, mock_lms_client,
        mock_subsidy_model,  # pylint: disable=unused-argument
    ):
        """
        Verify `send_assignment_automatically_expired_email` task work as expected
        """
        admin_email = 'test@admin.com'
        mock_lms_client.return_value.get_enterprise_customer_data.return_value = {
            'uuid': TEST_ENTERPRISE_UUID,
            'slug': 'test-slug',
            'admin_users': [{
                'email': admin_email,
                'lms_user_id': 1
            }],
            'name': self.enterprise_customer_name,
        }
        mock_recipient = {
            'external_user_id': 1
        }
        mock_braze_client.return_value.create_recipient.return_value = mock_recipient
        mock_admin_mailto = f'mailto:{admin_email}'
        mock_braze_client.return_value.create_recipient.return_value = mock_recipient
        mock_braze_client.return_value.generate_mailto_link.return_value = mock_admin_mailto

        send_assignment_automatically_expired_email(self.assignment.uuid)

        mock_braze_client.return_value.send_campaign_message.assert_any_call(
            'test-assignment-expired-campaign',
            recipients=[mock_recipient],
            trigger_properties={
                'contact_admin_link': mock_admin_mailto,
                'course_title': self.assignment.content_title,
                'organization': self.enterprise_customer_name,
            },
        )
        assert mock_braze_client.return_value.send_campaign_message.call_count == 1
