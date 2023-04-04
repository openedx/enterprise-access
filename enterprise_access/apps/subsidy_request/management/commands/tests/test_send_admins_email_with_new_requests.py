"""
Tests for Subsidy Request Management commands.
"""
from datetime import datetime
from uuid import uuid4

import mock
from django.conf import settings
from django.core.management import call_command
from pytest import mark
from requests.exceptions import HTTPError

from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates
from enterprise_access.apps.subsidy_request.tests import factories
from test_utils import APITestWithMocks


@mark.django_db
class TestManagementCommands(APITestWithMocks):
    """
    Tests for Subsidy Request Management Commands.
    """

    def setUp(self):
        super().setUp()
        self.enterprise_customer_uuid = uuid4()
        self.admin_users = [
            {
                'lms_user_id': 1,
                'email': 'pieguy@example.com',
            },
            {
                'lms_user_id': 2,
                'email': 'cakeman@example.com',
            }
        ]

    @mock.patch('enterprise_access.apps.subsidy_request.management.commands'
                '.send_admins_email_with_new_requests'
                '.sleep'
                )
    @mock.patch(
        'enterprise_access.apps.subsidy_request.management.commands'
        '.send_admins_email_with_new_requests.send_admins_email_with_new_requests_task'
        '.delay'
    )
    def test_new_requests_command_task_count(self, mock_task, mock_sleep):
        """
        Verify send_admins_email_with_new_requests spins off right amount of celery tasks
        """
        command_name = 'send_admins_email_with_new_requests'

        uuids = [uuid4() for _ in range(5)]
        for uuid in uuids:
            factories.SubsidyRequestCustomerConfigurationFactory(
                enterprise_customer_uuid=uuid,
                subsidy_requests_enabled=True,
            )
            # Make some with subsidy_requests disabled
            factories.SubsidyRequestCustomerConfigurationFactory(
                enterprise_customer_uuid=uuid4(),
                subsidy_requests_enabled=False,
            )
        call_command(command_name, '--batch-size=3')

        expected_calls = [mock.call(customer_uuid) for customer_uuid in uuids]
        actual_calls = mock_task.call_args_list
        self.assertEqual(sorted(expected_calls), sorted(actual_calls))
        assert mock_sleep.call_count == 1

    @mock.patch('enterprise_access.apps.subsidy_request.tasks.LmsApiClient.get_enterprise_customer_data')
    @mock.patch('enterprise_access.apps.subsidy_request.tasks.BrazeApiClient')
    def test_new_requests_never_sent_before(self, mock_braze_client, mock_get_ent_customer_data):
        """
        Verify send_admins_email_with_new_requests sends braze message including all
        subsidy requests if task has never been run before
        """
        mock_get_ent_customer_data.return_value = {
            'uuid': self.enterprise_customer_uuid,
            'slug': 'test-slug',
            'admin_users': self.admin_users
        }

        command_name = 'send_admins_email_with_new_requests'

        # Config object
        factories.SubsidyRequestCustomerConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            subsidy_requests_enabled=True,
            last_remind_date=None
        )
        # 3 License requests in REQUESTED
        expected_requests = [
            factories.LicenseRequestFactory(
                enterprise_customer_uuid=self.enterprise_customer_uuid,
                state=SubsidyRequestStates.REQUESTED,
            )
            for _ in range(3)
        ]
        # We expected latest first
        expected_requests.reverse()

        # 1 License not in REQUESTED
        factories.LicenseRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            state=SubsidyRequestStates.ERROR,
        )

        mock_admin_recipient_1 = {
            'external_user_id': 1
        }

        mock_admin_recipient_2 = {
            'external_user_id': 2
        }

        mock_braze_client.return_value.create_recipient.side_effect = [
            mock_admin_recipient_1, mock_admin_recipient_2
        ]

        call_command(command_name, '--batch-size=100')

        mock_braze_client.return_value.send_campaign_message.assert_called_once()
        call_args = mock_braze_client.return_value.send_campaign_message.call_args[0]
        call_kwargs = mock_braze_client.return_value.send_campaign_message.call_args[1]

        actual_campaign_id = call_args[0]
        actual_recipients = call_kwargs['recipients']
        actual_trigger_properties = call_kwargs['trigger_properties']

        assert actual_campaign_id == settings.BRAZE_NEW_REQUESTS_NOTIFICATION_CAMPAIGN
        assert actual_recipients[0] == mock_admin_recipient_1
        assert actual_recipients[1] == mock_admin_recipient_2
        for index, request in enumerate(expected_requests):
            request.refresh_from_db()
            expected_email = request.user.email
            expected_title = request.course_title
            expected_url = f'{settings.ENTERPRISE_ADMIN_PORTAL_URL}/test-slug/admin/subscriptions/manage-requests'
            assert actual_trigger_properties['requests'][index]['user_email'] == expected_email
            assert actual_trigger_properties['requests'][index]['course_title'] == expected_title
            assert actual_trigger_properties['manage_requests_url'] == expected_url

    @mock.patch('enterprise_access.apps.subsidy_request.tasks.LmsApiClient.get_enterprise_customer_data')
    @mock.patch('enterprise_access.apps.subsidy_request.tasks.BrazeApiClient')
    def test_new_requests_task_sent_before(self, mock_braze_client, mock_get_ent_customer_data):
        """
        Verify requests created before the last time the last_remind_date
        don't get included in the braze email that gets sent out.
        """
        mock_get_ent_customer_data.return_value = {
            'uuid': self.enterprise_customer_uuid,
            'slug': 'test-enterprise',
            'admin_users': self.admin_users
        }

        command_name = 'send_admins_email_with_new_requests'

        for _ in range(2):
            factories.LicenseRequestFactory(
                enterprise_customer_uuid=self.enterprise_customer_uuid,
                state=SubsidyRequestStates.REQUESTED,
            )

        factories.SubsidyRequestCustomerConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            subsidy_requests_enabled=True,
            last_remind_date=datetime.now()
        )

        new_request = factories.LicenseRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            state=SubsidyRequestStates.REQUESTED,
        )

        mock_admin_recipient_1 = {
            'external_user_id': 1
        }

        mock_admin_recipient_2 = {
            'external_user_id': 2
        }

        mock_braze_client.return_value.create_recipient.side_effect = [
            mock_admin_recipient_1, mock_admin_recipient_2
        ]

        call_command(command_name, '--batch-size=100')

        mock_braze_client.return_value.send_campaign_message.assert_called_once()
        call_kwargs = mock_braze_client.return_value.send_campaign_message.call_args[1]

        actual_recipients = call_kwargs['recipients']
        actual_trigger_properties = call_kwargs['trigger_properties']

        assert actual_recipients[0] == mock_admin_recipient_1
        assert actual_recipients[1] == mock_admin_recipient_2
        assert actual_trigger_properties['requests'][0]['user_email'] == new_request.user.email
        assert len(actual_trigger_properties['requests']) == 1

    @mock.patch('enterprise_access.apps.subsidy_request.tasks.LmsApiClient.get_enterprise_customer_data')
    @mock.patch('enterprise_access.apps.subsidy_request.tasks.BrazeApiClient')
    def test_new_requests_task_error(self, mock_braze_client, mock_get_ent_customer_data):
        """
        Verify last_remind_date is not updated if braze email fails.
        """
        mock_get_ent_customer_data.return_value = {
            'uuid': self.enterprise_customer_uuid,
            'slug': 'test-enterprise',
            'admin_users': self.admin_users
        }
        mock_braze_client.side_effect = HTTPError

        command_name = 'send_admins_email_with_new_requests'

        for _ in range(2):
            factories.LicenseRequestFactory(
                enterprise_customer_uuid=self.enterprise_customer_uuid,
                state=SubsidyRequestStates.REQUESTED,
            )

        config = factories.SubsidyRequestCustomerConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            subsidy_requests_enabled=True,
            last_remind_date=None
        )

        call_command(command_name, '--batch-size=100')

        config.refresh_from_db()
        assert config.last_remind_date is None

    @mock.patch('enterprise_access.apps.subsidy_request.tasks.LmsApiClient.get_enterprise_customer_data')
    @mock.patch('enterprise_access.apps.subsidy_request.tasks.BrazeApiClient')
    def test_no_new_requests(self, mock_braze_client, mock_get_ent_customer_data):
        """
        Verify no braze emails sent if no new requests.
        """
        mock_get_ent_customer_data.return_value = {
            'uuid': self.enterprise_customer_uuid,
            'slug': 'test-enterprise',
        }

        command_name = 'send_admins_email_with_new_requests'

        for _ in range(2):
            factories.LicenseRequestFactory(
                enterprise_customer_uuid=self.enterprise_customer_uuid,
                state=SubsidyRequestStates.REQUESTED,
            )

        factories.SubsidyRequestCustomerConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            subsidy_requests_enabled=True,
            last_remind_date=datetime.now()
        )

        call_command(command_name, '--batch-size=100')

        mock_braze_client.return_value.send_campaign_message.assert_not_called()
