"""
Tests for the `enterprise_groups` tasks module.
"""
import unittest
from datetime import datetime, timedelta
from unittest import mock
from uuid import uuid4

from braze.exceptions import BrazeClientError
from django.conf import settings
from pytest import raises

from enterprise_access.apps.enterprise_groups.tasks import send_group_reminder_emails


class TestEnterpriseTasks(unittest.TestCase):
    """
    Tests tasks associated with enterprise_groups.
    """

    def setUp(self):
        """
        Setup for `TestEnterpriseTasks` test.
        """
        self.enterprise_group_membership_uuid = uuid4()
        self.pending_enterprise_customer_users = []
        self.recent_action = datetime.strftime(datetime.today() - timedelta(days=5), '%B %d, %Y')

        self.pending_enterprise_customer_users.append({
            "pending_enterprise_customer_user_id": 1,
            "enterprise_group_membership_uuid": self.enterprise_group_membership_uuid,
            "user_email": "test1@2u.com",
            "recent_action": f'Invited: {self.recent_action}',
            "enterprise_customer_name": "test enterprise",
            "catalog_count": 5,
            "subsidy_expiration_datetime": "2060-03-25T20:46:28Z",
            "enterprise_group_uuid": uuid4(),
        })

        super().setUp()

    @mock.patch('enterprise_access.apps.enterprise_groups.tasks.BrazeApiClient', return_value=mock.MagicMock())
    def test_send_group_reminder_emails(self, mock_braze_api_client):
        """
        Verify test_send_group_reminder_emails hits braze client with expected args
        """
        mock_braze_api_client().create_recipient_no_external_id.return_value = (
            self.pending_enterprise_customer_users[0]['user_email'])
        send_group_reminder_emails(
            self.pending_enterprise_customer_users,
        )
        recipient = self.pending_enterprise_customer_users[0]['user_email']
        invitation_end_date = (datetime.strptime(self.recent_action, '%B %d, %Y') +
                               timedelta(days=90)).strftime("%B %d, %Y")
        subsidy_expiration_date = datetime.strptime(
            self.pending_enterprise_customer_users[0]['subsidy_expiration_datetime'],
            '%Y-%m-%dT%H:%M:%SZ').strftime("%B %d, %Y")
        calls = [mock.call(
            settings.BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_5_CAMPAIGN,
            recipients=[recipient],
            trigger_properties={
                'enterprise_customer': self.pending_enterprise_customer_users[0]['enterprise_customer_name'],
                'catalog_content_count': self.pending_enterprise_customer_users[0]['catalog_count'],
                'invitation_end_date': invitation_end_date,
                'subsidy_expiration_datetime': subsidy_expiration_date,
            },
        )]
        mock_braze_api_client().send_campaign_message.assert_has_calls(calls)

    @mock.patch('enterprise_access.apps.enterprise_groups.tasks.LmsApiClient', return_value=mock.MagicMock())
    @mock.patch('enterprise_access.apps.enterprise_groups.tasks.BrazeApiClient', return_value=mock.MagicMock())
    def test_fail_send_group_reminder_emails(self, mock_braze_api_client, mock_lms_client):
        """
        Verify braze fails send email and calls update_pending_learner_status with correct params
        """
        mock_braze_api_client().create_recipient_no_external_id.return_value = (
            self.pending_enterprise_customer_users[0]['user_email'])
        mock_braze_api_client().send_campaign_message.side_effect = BrazeClientError(
            "Any thing that happens during email")

        with raises(BrazeClientError):
            send_group_reminder_emails(
                self.pending_enterprise_customer_users)
            mock_lms_client().update_pending_learner_status.assert_called_with(
                enterprise_group_uuid=self.pending_enterprise_customer_users[0]["enterprise_group_uuid"],
                learner_email=self.pending_enterprise_customer_users[0]['user_email']
            )
