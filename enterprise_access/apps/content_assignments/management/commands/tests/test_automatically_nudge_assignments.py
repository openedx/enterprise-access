"""
Tests for `automatically_nudge_assignments` management command.
"""

from unittest import TestCase, mock
from unittest.mock import call
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.utils import timezone

from enterprise_access.apps.content_assignments.constants import LearnerContentAssignmentStateChoices
from enterprise_access.apps.content_assignments.management.commands import automatically_nudge_assignments
from enterprise_access.apps.content_assignments.tests.factories import (
    AssignmentConfigurationFactory,
    LearnerContentAssignmentFactory
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import AssignedLearnerCreditAccessPolicyFactory

COMMAND_PATH = 'enterprise_access.apps.content_assignments.management.commands.automatically_nudge_assignments'


@pytest.mark.django_db
class TestAutomaticallyNudgeAssignmentCommand(TestCase):
    """
    Tests `automatically_nudge_assignments` management command.
    """

    def setUp(self):
        super().setUp()
        self.command = automatically_nudge_assignments.Command()

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
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
        )

        self.bob_assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_configuration,
            learner_email='bob@foo.com',
            lms_user_id=None,
            content_key='edX+edXAccessibility101',
            content_title='edx: Accessibility 101',
            content_quantity=-456,
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
        )

        self.rob_assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_configuration,
            learner_email='rob@foo.com',
            lms_user_id=None,
            content_key='edX+edXQuadrilateral306090',
            content_title='edx: Quadrilateral 306090',
            content_quantity=-456,
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
        )
        self.richard_assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_configuration,
            learner_email='richard@foo.com',
            lms_user_id=None,
            content_key='edX+edXTesseract4D',
            content_title='edx: Tesseract 4D',
            content_quantity=-456,
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
        )

        self.ella_assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_configuration,
            learner_email='ella@foo.com',
            lms_user_id=None,
            content_key='edX+edXIsoscelesPyramid2012',
            content_title='edx: IsoscelesPyramid 2012',
            content_quantity=-456,
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
        )
        self.bella_assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_configuration,
            learner_email='bella@foo.com',
            lms_user_id=None,
            content_key='edX+edXBeeHivesAlive0220',
            content_title='edx: BeeHivesAlive 0220',
            content_quantity=-456,
            state=LearnerContentAssignmentStateChoices.CANCELLED,
        )

    @mock.patch(COMMAND_PATH + '.get_content_metadata_for_assignments')
    @mock.patch('enterprise_access.apps.content_assignments.api.send_reminder_email_for_pending_assignment.delay')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    def test_command_dry_run(
            self,
            mock_subsidy_client,
            mock_send_reminder_email_for_pending_assignment_task,
            mock_content_metadata_for_assignments,
    ):
        """
        Verify that management command work as expected in dry run mode.
        """
        enrollment_end = timezone.now() - timezone.timedelta(days=5)
        enrollment_end = enrollment_end.replace(microsecond=0)
        subsidy_expiry = timezone.now() + timezone.timedelta(days=5)
        subsidy_expiry = subsidy_expiry.replace(microsecond=0)
        start_date = timezone.now() + timezone.timedelta(days=30)
        start_date = start_date.replace(microsecond=0)
        end_date = timezone.now() + timezone.timedelta(days=180)
        end_date = end_date.replace(microsecond=0)

        mock_subsidy_client.retrieve_subsidy.return_value = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'expiration_datetime': subsidy_expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            'is_active': True,
        }
        mock_content_metadata_for_assignments.return_value = {
            'edX+edXAccessibility101': {
                'key': 'edX+edXAccessibility101',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXPrivacy101': {
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%d %H:%M"),
                    'content_price': 321,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXTesseract4D': {
                'key': 'edX+edXTesseract4D',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXQuadrilateral306090': {
                'key': 'edX+edXQuadrilateral306090',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXIsoscelesPyramid2012': {
                'key': 'edX+edXIsoscelesPyramid2012',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXBeeHivesAlive0220': {
                'key': 'edX+edXBeeHivesAlive0220',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
        }

        call_command(self.command, '--dry-run')

        mock_send_reminder_email_for_pending_assignment_task.assert_not_called()

    @mock.patch(COMMAND_PATH + '.get_content_metadata_for_assignments')
    @mock.patch('enterprise_access.apps.content_assignments.api.send_reminder_email_for_pending_assignment.delay')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    def test_command(
            self,
            mock_subsidy_client,
            mock_send_reminder_email_for_pending_assignment_task,
            mock_content_metadata_for_assignments,
    ):
        """
        Verify that management command work as expected.
        """
        enrollment_end = timezone.now() + timezone.timedelta(days=5)
        enrollment_end = enrollment_end.replace(microsecond=0)
        subsidy_expiry = timezone.now() - timezone.timedelta(days=5)
        subsidy_expiry = subsidy_expiry.replace(microsecond=0)
        start_date = timezone.now() + timezone.timedelta(days=14)
        start_date = start_date.replace(microsecond=0)
        end_date = timezone.now() + timezone.timedelta(days=180)
        end_date = end_date.replace(microsecond=0)

        mock_subsidy_client.retrieve_subsidy.return_value = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'expiration_datetime': subsidy_expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            'is_active': True,
        }
        mock_content_metadata_for_assignments.return_value = {
            'edX+edXAccessibility101': {
                'key': 'edX+edXAccessibility101',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXPrivacy101': {
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%d %H:%M"),
                    'content_price': 321,
                },
                'course_type': 'executive-education',
            },
            'edX+edXTesseract4D': {
                'key': 'edX+edXTesseract4D',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXQuadrilateral306090': {
                'key': 'edX+edXQuadrilateral306090',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXIsoscelesPyramid2012': {
                'key': 'edX+edXIsoscelesPyramid2012',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXBeeHivesAlive0220': {
                'key': 'edX+edXBeeHivesAlive0220',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
        }

        call_command(self.command, days_before_course_start_date=14)

        mock_send_reminder_email_for_pending_assignment_task.assert_has_calls([
            call(self.alice_assignment.uuid),
            call(self.bob_assignment.uuid),
        ])

    @mock.patch(COMMAND_PATH + '.get_content_metadata_for_assignments')
    @mock.patch('enterprise_access.apps.content_assignments.api.send_reminder_email_for_pending_assignment.delay')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    def test_command_multiple_assignment_dates(
            self,
            mock_subsidy_client,
            mock_send_reminder_email_for_pending_assignment_task,
            mock_content_metadata_for_assignments,
    ):
        """
        Verify that management command work as expected given multiple dates have been mocked.
        """
        enrollment_end = timezone.now() + timezone.timedelta(days=5)
        enrollment_end = enrollment_end.replace(microsecond=0)
        subsidy_expiry = timezone.now() - timezone.timedelta(days=5)
        subsidy_expiry = subsidy_expiry.replace(microsecond=0)
        start_date = timezone.now() + timezone.timedelta(days=14)
        start_date = start_date.replace(microsecond=0)
        end_date = timezone.now() + timezone.timedelta(days=180)
        end_date = end_date.replace(microsecond=0)

        # Three nonpassing dates for assignments
        start_date_beyond_30_days = timezone.now() + timezone.timedelta(days=90)
        start_date_beyond_30_days = start_date_beyond_30_days.replace(microsecond=0)

        start_date_between_30_and_14_days = timezone.now() + timezone.timedelta(days=9)
        start_date_between_30_and_14_days = start_date_between_30_and_14_days.replace(microsecond=0)

        start_date_already_started = timezone.now() + timezone.timedelta(days=-9)
        start_date_already_started = start_date_already_started.replace(microsecond=0)

        mock_subsidy_client.retrieve_subsidy.return_value = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'expiration_datetime': subsidy_expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            'is_active': True,
        }
        mock_content_metadata_for_assignments.return_value = {
            'edX+edXAccessibility101': {
                'key': 'edX+edXAccessibility101',
                'normalized_metadata': {
                    'start_date': start_date_between_30_and_14_days.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXTesseract4D': {
                'key': 'edX+edXTesseract4D',
                'normalized_metadata': {
                    'start_date': start_date_already_started.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXQuadrilateral306090': {
                'key': 'edX+edXQuadrilateral306090',
                'normalized_metadata': {
                    'start_date': start_date_beyond_30_days.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXPrivacy101': {
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%d %H:%M"),
                    'content_price': 321,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXIsoscelesPyramid2012': {
                'key': 'edX+edXIsoscelesPyramid2012',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXBeeHivesAlive0220': {
                'key': 'edX+edXBeeHivesAlive0220',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
        }

        call_command(self.command, days_before_course_start_date=14)

        mock_send_reminder_email_for_pending_assignment_task.assert_has_calls([
            call(self.alice_assignment.uuid),
        ])

    @mock.patch(COMMAND_PATH + '.get_content_metadata_for_assignments')
    @mock.patch('enterprise_access.apps.content_assignments.api.send_reminder_email_for_pending_assignment.delay')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    def test_command_multiple_assignment_course_types(
            self,
            mock_subsidy_client,
            mock_send_reminder_email_for_pending_assignment_task,
            mock_content_metadata_for_assignments,
    ):
        """
        Verify that management command work as expected given a course_type is not executive-education.
        """
        enrollment_end = timezone.now() + timezone.timedelta(days=5)
        enrollment_end = enrollment_end.replace(microsecond=0)
        subsidy_expiry = timezone.now() - timezone.timedelta(days=5)
        subsidy_expiry = subsidy_expiry.replace(microsecond=0)
        start_date = timezone.now() + timezone.timedelta(days=14)
        start_date = start_date.replace(microsecond=0)
        end_date = timezone.now() + timezone.timedelta(days=180)
        end_date = end_date.replace(microsecond=0)

        mock_subsidy_client.retrieve_subsidy.return_value = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'expiration_datetime': subsidy_expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            'is_active': True,
        }

        # Update course_type to check for only 'executive-education' or 'executive-education-2u' course types
        mock_content_metadata_for_assignments.return_value = {
            'edX+edXAccessibility101': {
                'key': 'edX+edXAccessibility101',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'verified-audit',
            },
            'edX+edXPrivacy101': {
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%d %H:%M"),
                    'content_price': 321,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXTesseract4D': {
                'key': 'edX+edXTesseract4D',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'professional',
            },
            'edX+edXQuadrilateral306090': {
                'key': 'edX+edXQuadrilateral306090',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'bootcamp-2u',
            },
            'edX+edXIsoscelesPyramid2012': {
                'key': 'edX+edXIsoscelesPyramid2012',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXBeeHivesAlive0220': {
                'key': 'edX+edXBeeHivesAlive0220',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
        }

        call_command(self.command, days_before_course_start_date=14)

        mock_send_reminder_email_for_pending_assignment_task.assert_has_calls([
            call(self.alice_assignment.uuid),
        ])

    @mock.patch(COMMAND_PATH + '.get_content_metadata_for_assignments')
    @mock.patch('enterprise_access.apps.content_assignments.api.send_reminder_email_for_pending_assignment.delay')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    def test_command_multiple_assignment_states(
            self,
            mock_subsidy_client,
            mock_send_reminder_email_for_pending_assignment_task,
            mock_content_metadata_for_assignments,
    ):
        """
        Verify that management command work as expected given the state of the course is allocated,
        cancelled or errored state.
        """
        enrollment_end = timezone.now() + timezone.timedelta(days=5)
        enrollment_end = enrollment_end.replace(microsecond=0)
        subsidy_expiry = timezone.now() - timezone.timedelta(days=5)
        subsidy_expiry = subsidy_expiry.replace(microsecond=0)
        start_date = timezone.now() + timezone.timedelta(days=14)
        start_date = start_date.replace(microsecond=0)
        end_date = timezone.now() + timezone.timedelta(days=180)
        end_date = end_date.replace(microsecond=0)

        # Update bobs assignment state
        self.bob_assignment.state = LearnerContentAssignmentStateChoices.ERRORED

        mock_subsidy_client.retrieve_subsidy.return_value = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'expiration_datetime': subsidy_expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            'is_active': True,
        }
        mock_content_metadata_for_assignments.return_value = {
            'edX+edXAccessibility101': {
                'key': 'edX+edXAccessibility101',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXTesseract4D': {
                'key': 'edX+edXTesseract4D',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXQuadrilateral306090': {
                'key': 'edX+edXQuadrilateral306090',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXPrivacy101': {
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%d %H:%M"),
                    'content_price': 321,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXIsoscelesPyramid2012': {
                'key': 'edX+edXIsoscelesPyramid2012',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXBeeHivesAlive0220': {
                'key': 'edX+edXBeeHivesAlive0220',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'end_date': end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'enroll_by_date': enrollment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    'content_price': 123,
                },
                'course_type': 'executive-education-2u',
            },
        }

        call_command(self.command, days_before_course_start_date=14)

        mock_send_reminder_email_for_pending_assignment_task.assert_has_calls([
            call(self.alice_assignment.uuid),
        ])
