"""
Tests for `automatically_expire_assignments` management command.
"""

from unittest import TestCase, mock
from unittest.mock import call
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.utils import timezone

from enterprise_access.apps.subsidy_access_policy.management.commands import groups_reminder_emails
from enterprise_access.apps.subsidy_access_policy.tests.factories import PolicyGroupAssociationFactory, AssignedLearnerCreditAccessPolicyFactory
from enterprise_access.apps.subsidy_access_policy.models import AssignedLearnerCreditAccessPolicy, SubsidyAccessPolicy

COMMAND_PATH = 'enterprise_access.apps.subsidy_access_policy.management.commands.groups_reminder_emails'


@pytest.mark.django_db
class TestGroupsReminderEmails(TestCase):
    """
    Tests `groups_reminder_emails` management command.
    """

    def setUp(self):
        super().setUp()
        self.command = groups_reminder_emails.Command()
        self.access_policy = AssignedLearnerCreditAccessPolicyFactory()

        self.enterprise_group_uuid = uuid4()
        self.policy_group_association = PolicyGroupAssociationFactory(
            enterprise_group_uuid=self.enterprise_group_uuid,
            subsidy_access_policy=self.access_policy
        )

    @mock.patch.object(SubsidyAccessPolicy, 'subsidy_record', autospec=True)
    @mock.patch('enterprise_access.apps.subsidy_access_policy.tasks.send_group_reminder_emails.delay')
    def test_email_groups_command_dry_run(
        self,
        mock_send_group_reminder_emails,
        mock_subsidy_record
    ):
        """
        Verify that management command work as expected in dry run mode.
        """
        # Mock results from the subsidy record.
        mock_subsidy_record.return_value = {
            'uuid': str(uuid4()),
            'title': 'Test Subsidy',
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'expiration_datetime': '2030-01-01 12:00:00Z',
            'active_datetime': '2020-01-01 12:00:00Z',
            'current_balance': '5000',
            'is_active': True,
        }
        call_command(self.command)

        mock_send_group_reminder_emails.assert_not_called()


    # @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient')
    # @mock.patch('enterprise_access.apps.content_assignments.api.send_assignment_automatically_expired_email.delay')
    # @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    # def test_command(
    #     self,
    #     mock_subsidy_client,
    #     mock_send_assignment_automatically_expired_email_task,
    #     mock_catalog_client,
    # ):
    #     """
    #     Verify that management command work as expected.
    #     """
    #     enrollment_end = timezone.now() + timezone.timedelta(days=5)
    #     enrollment_end = enrollment_end.replace(microsecond=0)
    #     subsidy_expiry = timezone.now() - timezone.timedelta(days=5)
    #     subsidy_expiry = subsidy_expiry.replace(microsecond=0)

    #     mock_subsidy_client.retrieve_subsidy.return_value = {
    #         'enterprise_customer_uuid': str(self.enterprise_uuid),
    #         'expiration_datetime': subsidy_expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
    #         'is_active': True,
    #     }
    #     mock_catalog_client.return_value.catalog_content_metadata.return_value = {
    #         'count': 1,
    #         'results': [
    #             {
    #                 'key': 'edX+edXAccessibility101',
    #                 'normalized_metadata': {
    #                     'start_date': '2020-01-01 12:00:00Z',
    #                     'end_date': '2022-01-01 12:00:00Z',
    #                     'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    #                     'content_price': 123,
    #                 },
    #             },
    #             {
    #                 'key': 'edX+edXPrivacy101',
    #                 'normalized_metadata': {
    #                     # test that some other datetime format is handled gracefully
    #                     'enroll_by_date': enrollment_end.strftime("%Y-%m-%d %H:%M"),
    #                 }
    #             }
    #         ],
    #     }

    #     all_assignment = LearnerContentAssignment.objects.all()
    #     allocated_assignments = LearnerContentAssignment.objects.filter(
    #         state=LearnerContentAssignmentStateChoices.ALLOCATED
    #     )
    #     # verify that all assignments are in `allocated` state
    #     assert all_assignment.count() == allocated_assignments.count()

    #     call_command(self.command)

    #     mock_send_assignment_automatically_expired_email_task.assert_has_calls([
    #         call(self.alice_assignment.uuid),
    #         call(self.bob_assignment.uuid),
    #     ])

    #     all_assignment = LearnerContentAssignment.objects.all()
    #     cancelled_assignments = LearnerContentAssignment.objects.filter(
    #         state=LearnerContentAssignmentStateChoices.EXPIRED
    #     )
    #     # verify that state has not changed for any assignment
    #     assert all_assignment.count() == cancelled_assignments.count()
