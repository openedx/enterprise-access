"""
Tests for `automatically_expire_assignments` management command.
"""

from unittest import TestCase, mock
from unittest.mock import call
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.utils import timezone

from enterprise_access.apps.content_assignments.constants import LearnerContentAssignmentStateChoices
from enterprise_access.apps.content_assignments.management.commands import automatically_expire_assignments
from enterprise_access.apps.content_assignments.models import LearnerContentAssignment
from enterprise_access.apps.content_assignments.tests.factories import (
    AssignmentConfigurationFactory,
    LearnerContentAssignmentFactory
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import AssignedLearnerCreditAccessPolicyFactory

COMMAND_PATH = 'enterprise_access.apps.content_assignments.management.commands.automatically_expire_assignments'


@pytest.mark.django_db
class TesAutomaticallyExpireAssignmentCommand(TestCase):
    """
    Tests `automatically_expire_assignments` management command.
    """

    def setUp(self):
        super().setUp()
        self.command = automatically_expire_assignments.Command()

        self.enterprise_uuid = uuid4()
        self.assignment_configuration = AssignmentConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
        )
        self.assigned_learner_credit_policy = AssignedLearnerCreditAccessPolicyFactory(
            display_name='An assigned learner credit policy, for the test customer.',
            enterprise_customer_uuid=self.enterprise_uuid,
            active=True,
            assignment_configuration=self.assignment_configuration,
            spend_limit=10000 * 100,
        )

        self.alice_assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_configuration,
            learner_email='alice@foo.com',
            lms_user_id=None,
            content_key='edX+edXPrivacy101',
            content_title='edx: Privacy 101',
            content_quantity=-123,
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
        )

        self.bob_assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_configuration,
            learner_email='bob@foo.com',
            lms_user_id=None,
            content_key='edX+edXAccessibility101',
            content_title='edx: Accessibility 101',
            content_quantity=-456,
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
        )

    @mock.patch(COMMAND_PATH + '.get_content_metadata_for_assignments')
    @mock.patch(COMMAND_PATH + '.send_assignment_automatically_expired_email.delay')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    def test_command_dry_run(
        self,
        mock_subsidy_client,
        mock_send_assignment_automatically_expired_email_task,
        mock_content_metadata_for_assignments,
    ):
        """
        Verify that management command work as expected in dry run mode.
        """
        enrollment_end = timezone.now() - timezone.timedelta(days=5)
        enrollment_end = enrollment_end.replace(microsecond=0)
        subsidy_expiry = timezone.now() + timezone.timedelta(days=5)
        subsidy_expiry = subsidy_expiry.replace(microsecond=0)

        # make an assignment expired
        self.alice_assignment.created = timezone.now() - timezone.timedelta(days=100)
        self.alice_assignment.save()

        mock_subsidy_client.retrieve_subsidy.return_value = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'expiration_datetime': subsidy_expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            'is_active': True,
        }
        mock_content_metadata_for_assignments.return_value = {
            'edX+edXAccessibility101': {
                'key': 'edX+edXAccessibility101',
                'normalized_metadata': {
                    'start_date': '2020-01-01 12:00:00Z',
                    'end_date': '2022-01-01 12:00:00Z',
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
            },
        }
        mock_path = COMMAND_PATH + '.logger.info'

        all_assignment = LearnerContentAssignment.objects.all()
        allocated_assignments = LearnerContentAssignment.objects.filter(
            state=LearnerContentAssignmentStateChoices.ALLOCATED
        )
        # verify that all assignments are in `allocated` state
        assert all_assignment.count() == allocated_assignments.count()

        with mock.patch(mock_path) as mock_logger:
            call_command(self.command, '--dry-run')
            mock_logger.assert_has_calls(
                [
                    call(
                        '%s Processing Assignment Configuration. UUID: [%s], Policy: [%s], Catalog: [%s], Enterprise: [%s]',  # nopep8 pylint: disable=line-too-long
                        '[DRY_RUN]',
                        self.assignment_configuration.uuid,
                        self.assignment_configuration.subsidy_access_policy.uuid,
                        self.assignment_configuration.subsidy_access_policy.catalog_uuid,
                        self.assignment_configuration.enterprise_customer_uuid
                    ),
                    call(
                        '%s AssignmentUUID: [%s], ContentKey: [%s], AssignmentExpiry: [%s], EnrollmentEnd: [%s], SubsidyExpiry: [%s]',  # nopep8 pylint: disable=line-too-long
                        '[DRY_RUN]',
                        self.alice_assignment.uuid,
                        'edX+edXPrivacy101',
                        self.alice_assignment.created + timezone.timedelta(days=90),
                        None,
                        subsidy_expiry
                    ),
                    call(
                        '%s Assignment Expired. AssignmentConfigUUID: [%s], AssignmentUUID: [%s], Reason: [%s]',
                        '[DRY_RUN]',
                        self.assignment_configuration.uuid,
                        self.alice_assignment.uuid,
                        'NIENTY_DAYS_PASSED'
                    ),
                    call(
                        '%s AssignmentUUID: [%s], ContentKey: [%s], AssignmentExpiry: [%s], EnrollmentEnd: [%s], SubsidyExpiry: [%s]',  # nopep8 pylint: disable=line-too-long
                        '[DRY_RUN]',
                        self.bob_assignment.uuid,
                        'edX+edXAccessibility101',
                        self.bob_assignment.created + timezone.timedelta(days=90),
                        enrollment_end,
                        subsidy_expiry
                    ),
                    call(
                        '%s Assignment Expired. AssignmentConfigUUID: [%s], AssignmentUUID: [%s], Reason: [%s]',
                        '[DRY_RUN]',
                        self.assignment_configuration.uuid,
                        self.bob_assignment.uuid,
                        'ENROLLMENT_DATE_PASSED'
                    ),
                ]
            )
            mock_send_assignment_automatically_expired_email_task.assert_not_called()

        all_assignment = LearnerContentAssignment.objects.all()
        allocated_assignments = LearnerContentAssignment.objects.filter(
            state=LearnerContentAssignmentStateChoices.ALLOCATED
        )
        # verify that state has not changed for any assignment
        assert all_assignment.count() == allocated_assignments.count()

    @mock.patch(COMMAND_PATH + '.get_content_metadata_for_assignments')
    @mock.patch(COMMAND_PATH + '.send_assignment_automatically_expired_email.delay')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    def test_command(
        self,
        mock_subsidy_client,
        mock_send_assignment_automatically_expired_email_task,
        mock_content_metadata_for_assignments,
    ):
        """
        Verify that management command work as expected in dry run mode.
        """
        enrollment_end = timezone.now() + timezone.timedelta(days=5)
        enrollment_end = enrollment_end.replace(microsecond=0)
        subsidy_expiry = timezone.now() - timezone.timedelta(days=5)
        subsidy_expiry = subsidy_expiry.replace(microsecond=0)

        mock_subsidy_client.retrieve_subsidy.return_value = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'expiration_datetime': subsidy_expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            'is_active': True,
        }
        mock_content_metadata_for_assignments.return_value = {
            'edX+edXAccessibility101': {
                'key': 'edX+edXAccessibility101',
                'normalized_metadata': {
                    'start_date': '2020-01-01 12:00:00Z',
                    'end_date': '2022-01-01 12:00:00Z',
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
            },
            'edX+edXPrivacy101': {
                'normalized_metadata': {
                    # test that some other datetime format is handled gracefully
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%d %H:%M"),
                }
            }
        }
        mock_path = COMMAND_PATH + '.logger.info'

        all_assignment = LearnerContentAssignment.objects.all()
        allocated_assignments = LearnerContentAssignment.objects.filter(
            state=LearnerContentAssignmentStateChoices.ALLOCATED
        )
        # verify that all assignments are in `allocated` state
        assert all_assignment.count() == allocated_assignments.count()

        with mock.patch(mock_path) as mock_logger:
            call_command(self.command)
            mock_logger.assert_has_calls(
                [
                    call(
                        '%s Processing Assignment Configuration. UUID: [%s], Policy: [%s], Catalog: [%s], Enterprise: [%s]',  # nopep8 pylint: disable=line-too-long
                        '[AUTOMATICALLY_EXPIRE_ASSIGNMENTS]',
                        self.assignment_configuration.uuid,
                        self.assignment_configuration.subsidy_access_policy.uuid,
                        self.assignment_configuration.subsidy_access_policy.catalog_uuid,
                        self.assignment_configuration.enterprise_customer_uuid
                    ),
                    call(
                        '%s AssignmentUUID: [%s], ContentKey: [%s], AssignmentExpiry: [%s], EnrollmentEnd: [%s], SubsidyExpiry: [%s]',  # nopep8 pylint: disable=line-too-long
                        '[AUTOMATICALLY_EXPIRE_ASSIGNMENTS]',
                        self.alice_assignment.uuid,
                        'edX+edXPrivacy101',
                        self.alice_assignment.created + timezone.timedelta(days=90),
                        None,
                        subsidy_expiry
                    ),
                    call(
                        '%s Assignment Expired. AssignmentConfigUUID: [%s], AssignmentUUID: [%s], Reason: [%s]',
                        '[AUTOMATICALLY_EXPIRE_ASSIGNMENTS]',
                        self.assignment_configuration.uuid,
                        self.alice_assignment.uuid,
                        'SUBSIDY_EXPIRED'
                    ),
                    call(
                        '%s AssignmentUUID: [%s], ContentKey: [%s], AssignmentExpiry: [%s], EnrollmentEnd: [%s], SubsidyExpiry: [%s]',  # nopep8 pylint: disable=line-too-long
                        '[AUTOMATICALLY_EXPIRE_ASSIGNMENTS]',
                        self.bob_assignment.uuid,
                        'edX+edXAccessibility101',
                        self.bob_assignment.created + timezone.timedelta(days=90),
                        enrollment_end,
                        subsidy_expiry
                    ),
                    call(
                        '%s Assignment Expired. AssignmentConfigUUID: [%s], AssignmentUUID: [%s], Reason: [%s]',
                        '[AUTOMATICALLY_EXPIRE_ASSIGNMENTS]',
                        self.assignment_configuration.uuid,
                        self.bob_assignment.uuid,
                        'SUBSIDY_EXPIRED'
                    ),
                ]
            )
            mock_send_assignment_automatically_expired_email_task.assert_has_calls(
                [
                    call(self.alice_assignment.uuid),
                    call(self.bob_assignment.uuid)
                ]
            )

        all_assignment = LearnerContentAssignment.objects.all()
        cancelled_assignments = LearnerContentAssignment.objects.filter(
            state=LearnerContentAssignmentStateChoices.CANCELLED
        )
        # verify that state has not changed for any assignment
        assert all_assignment.count() == cancelled_assignments.count()
