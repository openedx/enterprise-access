"""
Tests for `groups_reminder_emails` management command.
"""
from unittest import TestCase, mock
from uuid import uuid4

import pytest
from django.core.management import call_command

from enterprise_access.apps.enterprise_groups.management.commands import groups_reminder_emails
from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    AssignedLearnerCreditAccessPolicyFactory,
    PolicyGroupAssociationFactory
)

COMMON = "enterprise_access.apps.enterprise_groups.management.commands.groups_reminder_emails."


@pytest.mark.django_db
class TestGroupsReminderEmails(TestCase):
    """
    Tests `groups_reminder_emails` management command.
    """

    def setUp(self):
        super().setUp()
        self.command = groups_reminder_emails.Command()
        self.access_policy = AssignedLearnerCreditAccessPolicyFactory()
        self.enterprise_uuid = uuid4()
        self.enterprise_group_uuid = uuid4()
        self.policy_group_association = PolicyGroupAssociationFactory(
            enterprise_group_uuid=self.enterprise_group_uuid,
            subsidy_access_policy=self.access_policy,
        )

    @mock.patch(COMMON + "EnterpriseCatalogApiClient", return_value=mock.MagicMock())
    @mock.patch(COMMON + "LmsApiClient", return_value=mock.MagicMock())
    @mock.patch.object(SubsidyAccessPolicy, "subsidy_record", autospec=True)
    @mock.patch(
        "enterprise_access.apps.enterprise_groups.tasks.send_group_reminder_emails.delay"
    )
    def test_email_groups_command(
        self,
        mock_send_group_reminder_emails,
        mock_subsidy_record,
        mock_lms_api_client,
        mock_enterprise_catalog_client,
    ):
        """
        Verify that management command work as expected in dry run mode.
        """
        # Mock results from the subsidy record.
        subsidy_expiration_datetime = "2030-01-01 12:00:00Z"
        mock_subsidy_record.return_value = {
            "uuid": str(uuid4()),
            "title": "Test Subsidy",
            "enterprise_customer_uuid": str(self.enterprise_uuid),
            "expiration_datetime": subsidy_expiration_datetime,
            "active_datetime": "2020-01-01 12:00:00Z",
            "current_balance": "5000",
            "is_active": True,
        }
        pending_group_memberships = [
            {
                "enterprise_customer_user_id": None,
                "lms_user_id": None,
                "pending_enterprise_customer_user_id": 1,
                "enterprise_group_membership_uuid": self.enterprise_group_uuid,
                "recent_action": "Invited: March 25, 2024",
                "enterprise_customer": {"name": "Blk Dot Coffee"},
                "user_email": "test1@2u.com",
            },
            {
                "pending_enterprise_customer_user_id": 2,
                "enterprise_group_membership_uuid": self.enterprise_group_uuid,
                "recent_action": "Invited: March 30, 2024",
                "enterprise_customer": {"name": "Blk Dot Coffee"},
                "user_email": "test2@2u.com",
            },
        ]
        mock_enterprise_catalog_client().catalog_content_metadata.return_value = {
            'count': 5
        }
        mock_lms_api_client().get_pending_enterprise_group_memberships.return_value = (
            pending_group_memberships
        )
        call_command(self.command)
        mock_send_group_reminder_emails.assert_called_once_with(
            pending_group_memberships
        )
