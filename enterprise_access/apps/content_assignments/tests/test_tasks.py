"""
Tests for Enterprise Access content_assignments tasks.
"""
import datetime
from unittest import mock
from uuid import uuid4

import ddt
import freezegun
from celery import states as celery_states
from django.conf import settings
from django.utils.timezone import now, timedelta
from edx_django_utils.cache import TieredCache
from requests.exceptions import HTTPError
from rest_framework import status

from enterprise_access.apps.api_client.braze_client import ENTERPRISE_BRAZE_ALIAS_LABEL
from enterprise_access.apps.api_client.tests.test_utils import MockResponse
from enterprise_access.apps.content_assignments.constants import (
    BRAZE_TIMESTAMP_FORMAT,
    AssignmentActionErrors,
    AssignmentActions,
    LearnerContentAssignmentStateChoices
)
from enterprise_access.apps.content_assignments.content_metadata_api import get_human_readable_date
from enterprise_access.apps.content_assignments.tasks import (
    BrazeCampaignSender,
    create_pending_enterprise_learner_for_assignment_task,
    send_assignment_automatically_expired_email,
    send_bnr_automatically_expired_email,
    send_cancel_email_for_pending_assignment,
    send_email_for_new_assignment,
    send_reminder_email_for_pending_assignment
)
from enterprise_access.apps.content_assignments.tests.factories import (
    AssignmentConfigurationFactory,
    LearnerContentAssignmentFactory
)
from enterprise_access.apps.subsidy_access_policy.models import REQUEST_CACHE_NAMESPACE
from enterprise_access.apps.subsidy_access_policy.tests.factories import AssignedLearnerCreditAccessPolicyFactory
from enterprise_access.apps.subsidy_request.tests.factories import LearnerCreditRequestFactory
from enterprise_access.cache_utils import request_cache
from enterprise_access.utils import format_datetime_obj, get_automatic_expiration_date_and_reason
from test_utils import APITestWithMocks

TEST_COURSE_KEY = 'edX+DemoX'
TEST_COURSE_RUN_KEY = 'course-v1:edX+DemoX+Demo_Course'
TEST_ENTERPRISE_CUSTOMER_NAME = 'test-customer-name'
TEST_ENTERPRISE_UUID = uuid4()
TEST_EMAIL = 'foo@bar.com'
TEST_LMS_USER_ID = 1
TEST_LMS_USER_ID_2 = 2
TEST_ASSIGNMENT_UUID = uuid4()
TEST_RUN_BASED_ASSIGNMENT_UUID = uuid4()


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
        cls.policy = AssignedLearnerCreditAccessPolicyFactory(
            assignment_configuration=cls.assignment_configuration,
            spend_limit=10000000,
        )
        cls.mock_enterprise_customer_data = {
            'uuid': TEST_ENTERPRISE_UUID,
            'slug': 'test-slug',
            'admin_users': [{
                'email': 'test@admin.com',
                'lms_user_id': 1
            }],
            'name': TEST_ENTERPRISE_CUSTOMER_NAME,
        }
        cls.mock_content_metadata = {
            'key': TEST_COURSE_KEY,
            'course_runs': [
                {
                    'key': TEST_COURSE_RUN_KEY,
                    'start': '2020-01-01T12:00:00Z',
                    'end': '2022-01-01 12:00:00Z',
                    'enrollment_start': None,
                    'enrollment_end': '2023-09-11T19:00:00Z',
                    'pacing_type': 'self_paced',
                    'weeks_to_complete': 8,
                }
            ],
            'normalized_metadata': {
                'start_date': '2020-01-01T12:00:00Z',
                'end_date': '2022-01-01 12:00:00Z',
                'enroll_by_date': '2021-01-01T12:00:00Z',
                'content_price': 123,
            },
            'normalized_metadata_by_run': {
                TEST_COURSE_RUN_KEY: {
                    'start_date': '2020-01-01T12:00:00Z',
                    'end_date': '2022-01-01 12:00:00Z',
                    'enroll_by_date': '2021-01-01T12:00:00Z',
                    'content_price': 123,
                },
            },
            'owners': [
                {'name': 'Smart Folks', 'logo_image_url': 'http://pictures.yes'},
                {'name': 'Good People', 'logo_image_url': 'http://pictures.nice'},
            ],
            'card_image_url': 'https://itsanimage.com',
        }
        cls.mock_formatted_todays_date = get_human_readable_date(datetime.datetime.now().strftime(
            BRAZE_TIMESTAMP_FORMAT
        ))

    def setUp(self):
        super().setUp()
        self.course_name = 'test-course-name'
        self.enterprise_customer_name = TEST_ENTERPRISE_CUSTOMER_NAME
        self.assignment_course = LearnerContentAssignmentFactory(
            uuid=TEST_ASSIGNMENT_UUID,
            content_key=TEST_COURSE_KEY,
            parent_content_key=None,
            is_assigned_course_run=False,
            learner_email='edx@example.com',
            lms_user_id=TEST_LMS_USER_ID,
            assignment_configuration=self.assignment_configuration,
        )
        self.assignment_course_run = LearnerContentAssignmentFactory(
            uuid=TEST_RUN_BASED_ASSIGNMENT_UUID,
            content_key=TEST_COURSE_RUN_KEY,
            parent_content_key=TEST_COURSE_KEY,
            is_assigned_course_run=True,
            learner_email='learner@example.com',
            preferred_course_run_key=TEST_COURSE_RUN_KEY,
            lms_user_id=TEST_LMS_USER_ID_2,
            assignment_configuration=self.assignment_configuration,
        )

    def tearDown(self):
        super().tearDown()
        # Clear the subsidy record from the subsidy_access_policy request cache
        # and the tiered/django-memcached cache.
        request_cache(namespace=REQUEST_CACHE_NAMESPACE).clear()
        TieredCache.dangerous_clear_all_tiers()

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.objects')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.LmsApiClient')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.BrazeApiClient')
    @ddt.data(
        {'is_assigned_course_run': False},
        {'is_assigned_course_run': True},
    )
    @ddt.unpack
    def test_send_cancel_email_for_pending_assignment(
        self,
        mock_braze_client,
        mock_lms_client,
        mock_policy_model,  # pylint: disable=unused-argument
        is_assigned_course_run,
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

        assignment = self.assignment_course_run if is_assigned_course_run else self.assignment_course

        mock_admin_mailto = f'mailto:{admin_email}'
        mock_braze_client.return_value.create_recipient.return_value = mock_recipient
        mock_braze_client.return_value.generate_mailto_link.return_value = mock_admin_mailto
        send_cancel_email_for_pending_assignment(assignment.uuid)

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
                'course_title': assignment.content_title
            },
        )
        assert mock_braze_client.return_value.send_campaign_message.call_count == 1

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.LmsApiClient')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.BrazeApiClient')
    @ddt.data(
        {'is_logistrated': True, 'is_assigned_course_run': False},
        {'is_logistrated': False, 'is_assigned_course_run': False},
        {'is_logistrated': True, 'is_assigned_course_run': True},
        {'is_logistrated': False, 'is_assigned_course_run': True},
    )
    @ddt.unpack
    def test_send_reminder_email_for_pending_assignment(
        self,
        mock_braze_client_class,
        mock_lms_client,
        mock_catalog_client,
        mock_subsidy_client,
        is_logistrated,
        is_assigned_course_run,
    ):
        """
        Verify send_reminder_email_for_pending_assignment hits braze client with expected args
        """
        mock_braze_client = mock_braze_client_class.return_value

        assignment = self.assignment_course_run if is_assigned_course_run else self.assignment_course

        if not is_logistrated:
            assignment.lms_user_id = None
            assignment.save()

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
        mock_metadata = {
            'key': assignment.content_key,
            'normalized_metadata': {
                'start_date': '2020-01-01 12:00:00Z',
                'end_date': '2022-01-01 12:00:00Z',
                'enroll_by_date': '2021-01-01 12:00:00Z',
                'content_price': 123,
            },
            'normalized_metadata_by_run': {
                TEST_COURSE_RUN_KEY: {
                    'start_date': '2020-01-01 12:00:00Z',
                    'end_date': '2022-01-01 12:00:00Z',
                    'enroll_by_date': '2021-01-01 12:00:00Z',
                    'content_price': 123,
                },
            },
            'owners': [
                {'name': 'Smart Folks', 'logo_image_url': 'http://pictures.yes'},
                {'name': 'Good People', 'logo_image_url': 'http://pictures.nice'},
                {'name': 'Fast Learners', 'logo_image_url': 'http://pictures.totally'},
            ],
            'card_image_url': 'https://itsanimage.com',
            'course_runs': [
                {
                    'key': TEST_COURSE_RUN_KEY,
                    'start': '2020-01-01 12:00:00Z',
                    'end': '2022-01-01 12:00:00Z',
                    'enrollment_start': None,
                    'enrollment_end': '2023-09-11T19:00:00Z',
                    'pacing_type': 'self_paced',
                    'weeks_to_complete': 8,
                }
            ],
        }
        mock_catalog_client.return_value.catalog_content_metadata.return_value = {
            'count': 1,
            'results': [mock_metadata]
        }

        # Set the subsidy expiration time to tomorrow
        mock_subsidy = {
            'uuid': self.policy.subsidy_uuid,
            'expiration_datetime': (now() + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%SZ'),
        }
        mock_subsidy_client.retrieve_subsidy.return_value = mock_subsidy
        mock_braze_client.generate_mailto_link.return_value = f'mailto:{admin_email}'

        current_time = now()
        with freezegun.freeze_time(current_time):
            send_reminder_email_for_pending_assignment(assignment.uuid)

        # Make sure our LMS client got called correct times and with what we expected
        mock_lms_client.return_value.get_enterprise_customer_data.assert_called_with(
            self.assignment_configuration.enterprise_customer_uuid
        )

        expected_campaign_identifier = 'test-assignment-remind-campaign'
        expected_recipient = mock_braze_client.create_recipient_no_external_id.return_value
        if is_logistrated:
            expected_campaign_identifier = 'test-assignment-remind-post-logistration-campaign'
            expected_recipient = mock_braze_client.create_recipient.return_value
            self.assertFalse(mock_braze_client.create_braze_alias.called)
        else:
            mock_braze_client.create_braze_alias.assert_called_once_with(
                [assignment.learner_email],
                ENTERPRISE_BRAZE_ALIAS_LABEL,
            )

        mock_braze_client.send_campaign_message.assert_called_once_with(
            expected_campaign_identifier,
            recipients=[expected_recipient],
            trigger_properties={
                'contact_admin_link': f'mailto:{admin_email}',
                'organization': self.enterprise_customer_name,
                'course_title': assignment.content_title,
                'enrollment_deadline': 'Jan 01, 2021',
                'start_date': current_time.strftime(BRAZE_TIMESTAMP_FORMAT),
                'course_partner': 'Smart Folks, Good People, and Fast Learners',
                'course_card_image': 'https://itsanimage.com',
                'learner_portal_link': 'http://enterprise-learner-portal.example.com/test-slug',
                'action_required_by_timestamp': '2021-01-01T12:00:00Z'
            },
        )

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.LmsApiClient')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.BrazeApiClient')
    @ddt.data(
        {'is_assigned_course_run': False},
        {'is_assigned_course_run': True},
    )
    @ddt.unpack
    def test_send_email_for_new_assignment(
        self,
        mock_braze_client,
        mock_lms_client,
        mock_catalog_client,
        mock_subsidy_client,
        is_assigned_course_run,
    ):
        """
        Verify send_email_for_new_assignment hits braze client with expected args
        """
        mock_lms_client.return_value.get_enterprise_customer_data.return_value = self.mock_enterprise_customer_data
        mock_recipient = {
            'external_user_id': 1
        }
        mock_catalog_client.return_value.catalog_content_metadata.return_value = {
            'count': 1,
            'results': [self.mock_content_metadata]
        }
        # Set the subsidy expiration time to tomorrow
        mock_subsidy = {
            'uuid': self.policy.subsidy_uuid,
            'expiration_datetime': (now() + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%SZ'),
        }
        mock_subsidy_client.retrieve_subsidy.return_value = mock_subsidy

        admin_email = self.mock_enterprise_customer_data['admin_users'][0]['email']
        mock_admin_mailto = f"mailto:{admin_email}"
        mock_braze_client.return_value.create_recipient.return_value = mock_recipient
        mock_braze_client.return_value.generate_mailto_link.return_value = mock_admin_mailto

        assignment = self.assignment_course_run if is_assigned_course_run else self.assignment_course

        send_email_for_new_assignment(assignment.uuid)

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
                'course_title': assignment.content_title,
                'enrollment_deadline': 'Jan 01, 2021',
                'start_date': datetime.datetime.now().strftime(BRAZE_TIMESTAMP_FORMAT),
                'course_partner': 'Smart Folks and Good People',
                'course_card_image': self.mock_content_metadata['card_image_url'],
                'learner_portal_link': '{}/{}'.format(
                    settings.ENTERPRISE_LEARNER_PORTAL_URL,
                    self.mock_enterprise_customer_data['slug']
                ),
                'action_required_by_timestamp': '2021-01-01T12:00:00Z'
            }
        )
        assert mock_braze_client.return_value.send_campaign_message.call_count == 1

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.LmsApiClient')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.BrazeApiClient')
    @ddt.data(
        {'is_assigned_course_run': False},
        {'is_assigned_course_run': True},
    )
    @ddt.unpack
    def test_send_email_for_new_assignment_failure(
        self,
        mock_braze_client,
        mock_lms_client,
        mock_catalog_client,
        mock_subsidy_client,
        is_assigned_course_run,
    ):
        """
        Verify send_email_for_new_assignment does not change the state of the
        assignment record on failure.
        """
        mock_lms_client.return_value.get_enterprise_customer_data.return_value = self.mock_enterprise_customer_data
        mock_recipient = {
            'external_user_id': 1
        }
        mock_catalog_client.return_value.catalog_content_metadata.return_value = {
            'count': 1,
            'results': [self.mock_content_metadata]
        }

        # Set the subsidy expiration time to tomorrow
        mock_subsidy = {
            'uuid': self.policy.subsidy_uuid,
            'expiration_datetime': (now() + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%SZ'),
        }
        mock_subsidy_client.retrieve_subsidy.return_value = mock_subsidy

        braze_client_instance = mock_braze_client.return_value
        braze_client_instance.send_campaign_message.side_effect = Exception('foo')

        admin_email = self.mock_enterprise_customer_data['admin_users'][0]['email']
        mock_admin_mailto = f"mailto:{admin_email}"
        braze_client_instance.create_recipient.return_value = mock_recipient
        braze_client_instance.generate_mailto_link.return_value = mock_admin_mailto

        assignment = self.assignment_course_run if is_assigned_course_run else self.assignment_course

        send_email_for_new_assignment.delay(assignment.uuid)
        self.assertEqual(assignment.state, LearnerContentAssignmentStateChoices.ALLOCATED)
        self.assertTrue(assignment.actions.filter(
            error_reason=AssignmentActionErrors.EMAIL_ERROR,
            action_type=AssignmentActions.NOTIFIED,
        ).exists())

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.objects')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.LmsApiClient')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.BrazeApiClient')
    @ddt.data(
        {'is_assigned_course_run': False},
        {'is_assigned_course_run': True},
    )
    @ddt.unpack
    def test_send_assignment_automatically_expired_email(
        self,
        mock_braze_client,
        mock_lms_client,
        mock_subsidy_model,  # pylint: disable=unused-argument
        is_assigned_course_run,
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

        assignment = self.assignment_course_run if is_assigned_course_run else self.assignment_course

        send_assignment_automatically_expired_email(assignment.uuid)

        mock_braze_client.return_value.send_campaign_message.assert_any_call(
            'test-assignment-expired-campaign',
            recipients=[mock_recipient],
            trigger_properties={
                'contact_admin_link': mock_admin_mailto,
                'course_title': assignment.content_title,
                'organization': self.enterprise_customer_name,
            },
        )
        assert mock_braze_client.return_value.send_campaign_message.call_count == 1

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.objects')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.LmsApiClient')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.BrazeApiClient')
    @ddt.data(
        {'is_assigned_course_run': False},
        {'is_assigned_course_run': True},
    )
    @ddt.unpack
    def test_send_bnr_automatically_expired_email(
        self,
        mock_braze_client,
        mock_lms_client,
        mock_subsidy_model,  # pylint: disable=unused-argument
        is_assigned_course_run,
    ):
        """
        Verify `send_bnr_automatically_expired_email` task works as expected
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
        mock_braze_client.return_value.generate_mailto_link.return_value = mock_admin_mailto
        mock_learner_portal_link = f'{settings.ENTERPRISE_LEARNER_PORTAL_URL}/test-slug'

        assignment = self.assignment_course_run if is_assigned_course_run else self.assignment_course

        # Create a learner credit request associated with the assignment
        learner_credit_request = LearnerCreditRequestFactory.create(
            assignment=assignment,
            course_id=assignment.content_key,
            enterprise_customer_uuid=assignment.assignment_configuration.enterprise_customer_uuid,
            learner_credit_request_config__active=False,
        )

        send_bnr_automatically_expired_email(learner_credit_request.uuid)

        mock_braze_client.return_value.send_campaign_message.assert_any_call(
            'test-bnr-expired-campaign',
            recipients=[mock_recipient],
            trigger_properties={
                'contact_admin_link': mock_admin_mailto,
                'course_title': assignment.content_title,
                'organization': self.enterprise_customer_name,
                'learner_portal_link': mock_learner_portal_link,
            },
        )
        assert mock_braze_client.return_value.send_campaign_message.call_count == 1

    @mock.patch('enterprise_access.apps.content_assignments.tasks.LmsApiClient')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.BrazeApiClient')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient')
    @ddt.data(
        {'is_assigned_course_run': False},
        {'is_assigned_course_run': True},
    )
    @ddt.unpack
    def test_get_action_required_by_subsidy_expires_soonest(
        self,
        mock_catalog_client,
        mock_subsidy_client,
        # pylint: disable=unused-argument
        mock_braze_client_class, mock_lms_client_class,
        is_assigned_course_run,
    ):
        """
        Tests that the subsidy_expiration time is returned as the earliest action required by time.
        """
        assignment = self.assignment_course_run if is_assigned_course_run else self.assignment_course
        # Set the metadata enroll_by_date to tomorrow
        mock_metadata = {
            'key': assignment.content_key,
            'normalized_metadata': {
                'start_date': '2020-01-01 12:00:00Z',
                'end_date': '2022-01-01 12:00:00Z',
                'enroll_by_date': (now() + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%SZ'),
            },
        }
        mock_catalog_client.return_value.catalog_content_metadata.return_value = {
            'count': 1,
            'results': [mock_metadata]
        }

        # Set the subsidy expiration time to yesterday
        yesterday = now() - timedelta(days=1)
        mock_subsidy = {
            'uuid': self.policy.subsidy_uuid,
            'expiration_datetime': yesterday.strftime('%Y-%m-%d %H:%M:%SZ'),
        }
        mock_subsidy_client.retrieve_subsidy.return_value = mock_subsidy

        # Add a successful notification action of now-ish
        assignment.add_successful_notified_action()

        sender = BrazeCampaignSender(assignment)
        action_required_by = sender.get_action_required_by_timestamp()
        expected_result = format_datetime_obj(yesterday, output_pattern=BRAZE_TIMESTAMP_FORMAT)
        self.assertEqual(expected_result, action_required_by)

    @mock.patch('enterprise_access.apps.content_assignments.tasks.LmsApiClient')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.BrazeApiClient')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient')
    @ddt.data(
        {'is_assigned_course_run': False},
        {'is_assigned_course_run': True},
    )
    @ddt.unpack
    def test_get_action_required_by_enrollment_deadline_soonest(
        self,
        mock_catalog_client,
        mock_subsidy_client,
        # pylint: disable=unused-argument
        mock_braze_client_class, mock_lms_client_class,
        is_assigned_course_run,
    ):
        """
        Tests that the enroll_by_date is returned as the earliest action required by time.
        """
        yesterday = now() - timedelta(days=1)
        formatted_yesterday = yesterday.strftime('%Y-%m-%d %H:%M:%SZ')
        assignment = self.assignment_course_run if is_assigned_course_run else self.assignment_course
        # Set the metadata enroll_by_date to yesterday
        mock_metadata = {
            'key': assignment.content_key,
            'normalized_metadata': {
                'start_date': '2020-01-01 12:00:00Z',
                'end_date': '2022-01-01 12:00:00Z',
                'enroll_by_date': formatted_yesterday,
            },
            'normalized_metadata_by_run': {
                TEST_COURSE_RUN_KEY: {
                    'start_date': '2020-01-01 12:00:00Z',
                    'end_date': '2022-01-01 12:00:00Z',
                    'enroll_by_date': formatted_yesterday,
                },
            }
        }
        mock_catalog_client.return_value.catalog_content_metadata.return_value = {
            'count': 1,
            'results': [mock_metadata]
        }

        # Set the subsidy expiration time to tomorrow
        mock_subsidy = {
            'uuid': self.policy.subsidy_uuid,
            'expiration_datetime': (now() + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%SZ'),
        }
        mock_subsidy_client.retrieve_subsidy.return_value = mock_subsidy

        # Add a successful notification action of now-ish
        assignment.add_successful_notified_action()

        sender = BrazeCampaignSender(assignment)
        action_required_by = sender.get_action_required_by_timestamp()
        expected_result = format_datetime_obj(yesterday, output_pattern=BRAZE_TIMESTAMP_FORMAT)
        self.assertEqual(expected_result, action_required_by)

    @mock.patch('enterprise_access.apps.content_assignments.tasks.LmsApiClient')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.BrazeApiClient')
    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    @ddt.data(
        {'is_assigned_course_run': False},
        {'is_assigned_course_run': True},
    )
    @ddt.unpack
    def test_get_action_required_by_auto_cancellation_soonest(
        self,
        mock_subsidy_client,
        mock_catalog_client,
        # pylint: disable=unused-argument
        mock_braze_client_class, mock_lms_client_class,
        is_assigned_course_run,
    ):
        """
        Tests that the auto-cancellation date is returned as the earliest action required by time.
        """
        the_future = now() + timedelta(days=120)
        assignment = self.assignment_course_run if is_assigned_course_run else self.assignment_course
        # Set the metadata enroll_by_date to far in the future
        mock_metadata = {
            'key': assignment.content_key,
            'normalized_metadata': {
                'start_date': '2020-01-01 12:00:00Z',
                'end_date': '2022-01-01 12:00:00Z',
                'enroll_by_date': the_future.strftime('%Y-%m-%d %H:%M:%SZ'),
            },
            'normalized_metadata_by_run': {
                TEST_COURSE_RUN_KEY: {
                    'start_date': '2020-01-01 12:00:00Z',
                    'end_date': '2022-01-01 12:00:00Z',
                    'enroll_by_date': the_future.strftime('%Y-%m-%d %H:%M:%SZ'),
                },
            },
        }
        mock_catalog_client.return_value.catalog_content_metadata.return_value = {
            'count': 1,
            'results': [mock_metadata]
        }

        # Set the subsidy expiration time to far in the future
        mock_subsidy = {
            'uuid': self.policy.subsidy_uuid,
            'expiration_datetime': the_future.strftime('%Y-%m-%d %H:%M:%SZ'),
        }
        mock_subsidy_client.retrieve_subsidy.return_value = mock_subsidy

        # Add a successful notification action of now-ish
        assignment.add_successful_notified_action()

        sender = BrazeCampaignSender(assignment)
        action_required_by = sender.get_action_required_by_timestamp()

        expected_result = format_datetime_obj(
            get_automatic_expiration_date_and_reason(assignment)['date'],
            output_pattern=BRAZE_TIMESTAMP_FORMAT
        )
        self.assertEqual(expected_result, action_required_by)


class TestClearPiiForExpiredAssignmentsTask(APITestWithMocks):
    """
    Tests for clear_pii_for_expired_assignments task.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.enterprise_uuid = TEST_ENTERPRISE_UUID
        cls.assignment_configuration = AssignmentConfigurationFactory(
            enterprise_customer_uuid=cls.enterprise_uuid,
        )
        cls.policy = AssignedLearnerCreditAccessPolicyFactory(
            display_name='An assigned learner credit policy, for the test customer.',
            enterprise_customer_uuid=cls.enterprise_uuid,
            active=True,
            assignment_configuration=cls.assignment_configuration,
            spend_limit=1000000,
        )

    def setUp(self):
        super().setUp()
        self.expired_assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_configuration,
            learner_email='expired@test.com',
            lms_user_id=TEST_LMS_USER_ID,
            content_key=TEST_COURSE_KEY,
            content_title='Test Course',
            content_quantity=-100,
            state=LearnerContentAssignmentStateChoices.EXPIRED,
            expired_at=now() - timedelta(hours=2),
        )
        self.expired_assignment.created = now() - timedelta(days=100)
        self.expired_assignment.save()

    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    def test_clear_pii_for_expired_assignments_task(
        self,
        mock_subsidy_client,
        mock_catalog_client,
    ):
        """
        Test that the task clears PII for assignments expired due to 90-day timeout
        after expiration email has been sent.
        """
        import re

        from enterprise_access.apps.content_assignments.constants import RETIRED_EMAIL_ADDRESS_FORMAT
        from enterprise_access.apps.content_assignments.tasks import clear_pii_for_expired_assignments

        self.expired_assignment.add_successful_expiration_action()

        subsidy_expiry = now() + timedelta(days=365)
        enrollment_end = now() + timedelta(days=365)

        mock_subsidy_client.retrieve_subsidy.return_value = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'expiration_datetime': subsidy_expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            'is_active': True,
        }
        mock_catalog_client.return_value.catalog_content_metadata.return_value = {
            'count': 1,
            'results': [{
                'key': TEST_COURSE_KEY,
                'normalized_metadata': {
                    'start_date': '2020-01-01 12:00:00Z',
                    'end_date': '2030-01-01 12:00:00Z',
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 100,
                },
            }],
        }

        result = clear_pii_for_expired_assignments(dry_run=False)

        self.expired_assignment.refresh_from_db()
        pattern = RETIRED_EMAIL_ADDRESS_FORMAT.format('[a-f0-9]{16}')
        assert re.match(pattern, self.expired_assignment.learner_email) is not None
        assert result['cleared_count'] == 1

    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    def test_clear_pii_skipped_without_expiration_email(
        self,
        mock_subsidy_client,
        mock_catalog_client,
    ):
        """
        Test that PII is NOT cleared if expiration email wasn't successfully sent.
        """
        from enterprise_access.apps.content_assignments.tasks import clear_pii_for_expired_assignments

        # Note: NOT adding successful expiration action
        original_email = self.expired_assignment.learner_email

        subsidy_expiry = now() + timedelta(days=365)
        enrollment_end = now() + timedelta(days=365)

        mock_subsidy_client.retrieve_subsidy.return_value = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'expiration_datetime': subsidy_expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            'is_active': True,
        }
        mock_catalog_client.return_value.catalog_content_metadata.return_value = {
            'count': 1,
            'results': [{
                'key': TEST_COURSE_KEY,
                'normalized_metadata': {
                    'start_date': '2020-01-01 12:00:00Z',
                    'end_date': '2030-01-01 12:00:00Z',
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 100,
                },
            }],
        }

        result = clear_pii_for_expired_assignments(dry_run=False)

        self.expired_assignment.refresh_from_db()
        assert self.expired_assignment.learner_email == original_email
        assert result['cleared_count'] == 0
