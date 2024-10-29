"""
Tests for `automatically_nudge_assignments` management command.
"""

from unittest import TestCase, mock
from unittest.mock import call, patch
from uuid import uuid4

import ddt
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
@ddt.ddt
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
            content_key='course-v1:edX+edXPrivacy101+1T2022',
            parent_content_key='edX+edXPrivacy101',
            is_assigned_course_run=True,
            preferred_course_run_key='course-v1:edX+edXPrivacy101+1T2022',
            content_title='edx: Privacy 101',
            content_quantity=-123,
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
        )

        self.bob_assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_configuration,
            learner_email='bob@foo.com',
            lms_user_id=None,
            content_key='edX+edXAccessibility101',
            parent_content_key=None,
            is_assigned_course_run=False,
            preferred_course_run_key='course-v1:edX+edXAccessibility101+1T2022',
            content_title='edx: Accessibility 101',
            content_quantity=-456,
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
        )

        self.rob_assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_configuration,
            learner_email='rob@foo.com',
            lms_user_id=None,
            content_key='edX+edXQuadrilateral306090',
            parent_content_key=None,
            is_assigned_course_run=False,
            preferred_course_run_key='course-v1:edX+edXQuadrilateral306090+1T2022',
            content_title='edx: Quadrilateral 306090',
            content_quantity=-456,
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
        )
        self.richard_assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_configuration,
            learner_email='richard@foo.com',
            lms_user_id=None,
            content_key='edX+edXTesseract4D',
            parent_content_key=None,
            is_assigned_course_run=False,
            preferred_course_run_key='course-v1:edX+edXTesseract4D+1T2022',
            content_title='edx: Tesseract 4D',
            content_quantity=-456,
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
        )

        self.ella_assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_configuration,
            learner_email='ella@foo.com',
            lms_user_id=None,
            content_key='edX+edXIsoscelesPyramid2012',
            parent_content_key=None,
            is_assigned_course_run=False,
            preferred_course_run_key='course-v1:edX+edXIsoscelesPyramid2012+1T2022',
            content_title='edx: IsoscelesPyramid 2012',
            content_quantity=-456,
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
        )
        self.bella_assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_configuration,
            learner_email='bella@foo.com',
            lms_user_id=None,
            content_key='edX+edXBeeHivesAlive0220',
            parent_content_key=None,
            is_assigned_course_run=False,
            preferred_course_run_key='course-v1:edX+edXBeeHivesAlive0220+1T2022',
            content_title='edx: BeeHivesAlive 0220',
            content_quantity=-456,
            state=LearnerContentAssignmentStateChoices.CANCELLED,
        )

    @mock.patch(COMMAND_PATH + '.get_content_metadata_for_assignments')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.send_exec_ed_enrollment_warmer.delay')
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
        subsidy_expiry = timezone.now().replace(microsecond=0) + timezone.timedelta(days=5)
        start_date = timezone.now().replace(microsecond=0) + timezone.timedelta(days=30)

        mock_subsidy_client.retrieve_subsidy.return_value = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'expiration_datetime': subsidy_expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            'is_active': True,
        }
        mock_content_metadata_for_assignments.return_value = {
            'edX+edXAccessibility101': {
                'key': 'edX+edXAccessibility101',
                'course_runs': [
                    {
                        'key': 'course-v1:edX+edXAccessibility101+1T2022',
                        'start': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                ],
                'course_type': 'executive-education-2u',
            },
            'edX+edXPrivacy101': {
                'key': 'edX+edXPrivacy101',
                'course_runs': [
                    {
                        'key': 'course-v1:edX+edXPrivacy101+1T2022',
                        'start': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                ],
                'course_type': 'executive-education-2u',
            },
            'edX+edXTesseract4D': {
                'key': 'edX+edXTesseract4D',
                'course_runs': [
                    {
                        'key': 'course-v1:edX+edXTesseract4D+1T2022',
                        'start': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                ],
                'course_type': 'executive-education-2u',
            },
            'edX+edXQuadrilateral306090': {
                'key': 'edX+edXQuadrilateral306090',
                'course_runs': [
                    {
                        'key': 'course-v1:edX+edXQuadrilateral306090+1T2022',
                        'start': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                ],
                'course_type': 'executive-education-2u',
            },
            'edX+edXIsoscelesPyramid2012': {
                'key': 'edX+edXIsoscelesPyramid2012',
                'course_runs': [
                    {
                        'key': 'course-v1:edX+edXIsoscelesPyramid2012+1T2022',
                        'start': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                ],
                'course_type': 'executive-education-2u',
            },
            'edX+edXBeeHivesAlive0220': {
                'key': 'edX+edXBeeHivesAlive0220',
                'course_runs': [
                    {
                        'key': 'course-v1:edX+edXBeeHivesAlive0220+1T2022',
                        'start': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                ],
                'course_type': 'executive-education-2u',
            },
        }

        call_command(self.command, '--dry-run')

        mock_send_reminder_email_for_pending_assignment_task.assert_not_called()

    @mock.patch(COMMAND_PATH + '.get_content_metadata_for_assignments')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.send_exec_ed_enrollment_warmer.delay')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    def test_command(
            self,
            mock_subsidy_client,
            mock_send_exec_ed_enrollment_warmer_task,
            mock_content_metadata_for_assignments,
    ):
        """
        Verify that management command work as expected.
        """
        subsidy_expiry = timezone.now().replace(microsecond=0) - timezone.timedelta(days=5)
        start_date = timezone.now().replace(microsecond=0) + timezone.timedelta(days=14)

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
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXAccessibility101+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
                'course_type': 'executive-education-2u',
            },
            # `self.alice_assignment` is an assignment for a course run, so its run-based content_key
            # should be used as the key in this dict.
            'course-v1:edX+edXPrivacy101+1T2022': {
                'key': 'edX+edXPrivacy101',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXPrivacy101+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXTesseract4D': {
                'key': 'edX+edXTesseract4D',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXTesseract4D+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXQuadrilateral306090': {
                'key': 'edX+edXQuadrilateral306090',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXQuadrilateral306090+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXIsoscelesPyramid2012': {
                'key': 'edX+edXIsoscelesPyramid2012',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXIsoscelesPyramid2012+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
                'course_type': 'executive-education-2u',
            },
            'edX+edXBeeHivesAlive0220': {
                'key': 'edX+edXBeeHivesAlive0220',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXBeeHivesAlive0220+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
                'course_type': 'executive-education-2u',
            },
        }

        call_command(self.command, days_before_course_start_date=14)

        mock_send_exec_ed_enrollment_warmer_task.assert_has_calls([
            call(self.alice_assignment.uuid, 14),
            call(self.bob_assignment.uuid, 14),
            call(self.rob_assignment.uuid, 14),
            call(self.richard_assignment.uuid, 14)
        ])

    @mock.patch(COMMAND_PATH + '.get_content_metadata_for_assignments')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.send_exec_ed_enrollment_warmer.delay')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    def test_command_multiple_assignment_dates(
            self,
            mock_subsidy_client,
            mock_send_exec_ed_enrollment_warmer_task,
            mock_content_metadata_for_assignments,
    ):
        """
        Verify that management command work as expected given multiple dates have been mocked.
        """
        subsidy_expiry = timezone.now().replace(microsecond=0) - timezone.timedelta(days=5)
        start_date = timezone.now().replace(microsecond=0) + timezone.timedelta(days=14)

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
                'course_type': 'executive-education-2u',
                'normalized_metadata': {
                    'start_date': start_date_between_30_and_14_days.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXAccessibility101+1T2022': {
                        'start_date': start_date_between_30_and_14_days.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
            },
            'edX+edXTesseract4D': {
                'key': 'edX+edXTesseract4D',
                'course_type': 'executive-education-2u',
                'normalized_metadata': {
                    'start_date': start_date_already_started.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXTesseract4D+1T2022': {
                        'start_date': start_date_already_started.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
            },
            'edX+edXQuadrilateral306090': {
                'key': 'edX+edXQuadrilateral306090',
                'normalized_metadata': {
                    'start_date': start_date_beyond_30_days.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXQuadrilateral306090+1T2022': {
                        'start_date': start_date_beyond_30_days.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
                'course_type': 'executive-education-2u',
            },
            # `self.alice_assignment` is an assignment for a course run, so its run-based content_key
            # should be used as the key in this dict.
            'course-v1:edX+edXPrivacy101+1T2022': {
                'key': 'edX+edXPrivacy101',
                'course_runs': [
                    {
                        'key': 'course-v1:edX+edXPrivacy101+1T2022',
                        'start': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                ],
                'course_type': 'executive-education-2u',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXPrivacy101+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
            },
            'edX+edXIsoscelesPyramid2012': {
                'key': 'edX+edXIsoscelesPyramid2012',
                'course_type': 'executive-education-2u',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXIsoscelesPyramid2012+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
            },
            'edX+edXBeeHivesAlive0220': {
                'key': 'edX+edXBeeHivesAlive0220',
                'course_runs': [
                    {
                        'key': 'course-v1:edX+edXBeeHivesAlive0220+1T2022',
                        'start': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                ],
                'course_type': 'executive-education-2u',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXBeeHivesAlive0220+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
            },
        }

        call_command(self.command, days_before_course_start_date=14)

        mock_send_exec_ed_enrollment_warmer_task.assert_has_calls([
            call(self.alice_assignment.uuid, 14),
        ])

    @mock.patch(COMMAND_PATH + '.get_content_metadata_for_assignments')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.send_exec_ed_enrollment_warmer.delay')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    def test_command_multiple_assignment_course_types(
            self,
            mock_subsidy_client,
            mock_send_exec_ed_enrollment_warmer_task,
            mock_content_metadata_for_assignments,
    ):
        """
        Verify that management command work as expected given a course_type is not executive-education.
        """
        subsidy_expiry = timezone.now().replace(microsecond=0) - timezone.timedelta(days=5)
        start_date = timezone.now().replace(microsecond=0) + timezone.timedelta(days=14)

        mock_subsidy_client.retrieve_subsidy.return_value = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'expiration_datetime': subsidy_expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            'is_active': True,
        }

        # Update course_type to check for only 'executive-education' or 'executive-education-2u' course types
        mock_content_metadata_for_assignments.return_value = {
            'edX+edXAccessibility101': {
                'key': 'edX+edXAccessibility101',
                'course_type': 'verified-audit',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXAccessibility101+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
            },
            # `self.alice_assignment` is an assignment for a course run, so its run-based content_key
            # should be used as the key in this dict.
            'course-v1:edX+edXPrivacy101+1T2022': {
                'key': 'edX+edXPrivacy101',
                'course_type': 'executive-education-2u',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXPrivacy101+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
            },
            'edX+edXTesseract4D': {
                'key': 'edX+edXTesseract4D',
                'course_type': 'professional',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXTesseract4D+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
            },
            'edX+edXQuadrilateral306090': {
                'key': 'edX+edXQuadrilateral306090',
                'course_type': 'bootcamp-2u',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXQuadrilateral306090+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
            },
            'edX+edXIsoscelesPyramid2012': {
                'key': 'edX+edXIsoscelesPyramid2012',
                'course_type': 'executive-education-2u',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXIsoscelesPyramid2012+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
            },
            'edX+edXBeeHivesAlive0220': {
                'key': 'edX+edXBeeHivesAlive0220',
                'course_type': 'executive-education-2u',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXBeeHivesAlive0220+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
            },
        }

        call_command(self.command, days_before_course_start_date=14)

        mock_send_exec_ed_enrollment_warmer_task.assert_has_calls([
            call(self.alice_assignment.uuid, 14),
        ])

    @mock.patch(COMMAND_PATH + '.get_content_metadata_for_assignments')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.send_exec_ed_enrollment_warmer.delay')
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client')
    def test_command_multiple_assignment_states(
            self,
            mock_subsidy_client,
            mock_send_exec_ed_enrollment_warmer_task,
            mock_content_metadata_for_assignments,
    ):
        """
        Verify that management command work as expected given the state of the course is allocated,
        cancelled or errored state.
        """
        subsidy_expiry = timezone.now().replace(microsecond=0) - timezone.timedelta(days=5)
        start_date = timezone.now().replace(microsecond=0) + timezone.timedelta(days=14)

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
                'course_type': 'executive-education-2u',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXAccessibility101+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
            },
            'edX+edXTesseract4D': {
                'key': 'edX+edXTesseract4D',
                'course_type': 'executive-education-2u',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXTesseract4D+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
            },
            'edX+edXQuadrilateral306090': {
                'key': 'edX+edXQuadrilateral306090',
                'course_type': 'executive-education-2u',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXQuadrilateral306090+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
            },
            # `self.alice_assignment` is an assignment for a course run, so its run-based content_key
            # should be used as the key in this dict.
            'course-v1:edX+edXPrivacy101+1T2022': {
                'key': 'edX+edXPrivacy101',
                'course_type': 'executive-education-2u',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXPrivacy101+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
            },
            'edX+edXIsoscelesPyramid2012': {
                'key': 'edX+edXIsoscelesPyramid2012',
                'course_type': 'executive-education-2u',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXIsoscelesPyramid2012+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
            },
            'edX+edXBeeHivesAlive0220': {
                'key': 'edX+edXBeeHivesAlive0220',
                'course_type': 'executive-education-2u',
                'normalized_metadata': {
                    'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                'normalized_metadata_by_run': {
                    'course-v1:edX+edXBeeHivesAlive0220+1T2022': {
                        'start_date': start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
            },
        }

        call_command(self.command, days_before_course_start_date=14)

        mock_send_exec_ed_enrollment_warmer_task.assert_has_calls([
            call(self.alice_assignment.uuid, 14),
        ])


@pytest.mark.django_db
@ddt.ddt
class TestAutomaticallyNudgeAssignmentCommand2(TestCase):
    """
    Tests `automatically_nudge_assignments` management command.

    An alternative to TestAutomaticallyNudgeAssignmentCommand, but just more DRY by leveraging ddt.
    """
    COURSE_KEY = 'edX+edXPrivacy101'
    COURSE_RUN_KEY = f'course-v1:{COURSE_KEY}+1T2022'

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
        subsidy_expiry = timezone.now().replace(microsecond=0) + timezone.timedelta(days=5)
        subsidy_client_patcher = patch(
            'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client'
        )
        self.mock_subsidy_client = subsidy_client_patcher.start()
        self.mock_subsidy_client.retrieve_subsidy.return_value = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'expiration_datetime': subsidy_expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            'is_active': True,
        }
        self.addCleanup(subsidy_client_patcher.stop)

    @ddt.data(
        # Happy case: course starts in 14 days, and everything about the course and assignment are nudgeable.
        {},
        # Not nudgeable due to course metadata being missing from enterprise-catalog.
        {
            'missing_content_metadata': True,
            'expected_task_call_days_before_course_start_date': None,
        },
        # Not nudgeable due to course start not being exactly 14 days from now (test course starts in 15 days).
        {
            'course_starts_in_x_days': 15,  # Not exactly 14.
            'expected_task_call_days_before_course_start_date': None,
        },
        # Not nudgeable due to course start not being exactly 14 days from now (test course starts in 13 days).
        {
            'course_starts_in_x_days': 13,  # Not exactly 14.
            'expected_task_call_days_before_course_start_date': None,
        },
        # Not nudgeable due to course start not being exactly 14 days from now (test course started yesterday).
        {
            'course_starts_in_x_days': -1,  # Not exactly 14.
            'expected_task_call_days_before_course_start_date': None,
        },
        # Not nudgeable due to course type not being "executive-education-2u".
        {
            'course_type': 'verified-audit',  # Not exactly "executive-education-2u".
            'expected_task_call_days_before_course_start_date': None,
        },
        # Not nudgeable due to assignment `preferred_course_run_key` field not being set.
        {
            'assignment_preferred_course_run_key': None,  # Legacy assignent has no preferred_course_run_key.
            'expected_task_call_days_before_course_start_date': None,
        },
        # Not nudgeable due to assignment `preferred_course_run_key` field being blank.
        {
            'assignment_preferred_course_run_key': '',  # Not sure how this can happen, but lets test it anyway.
            'expected_task_call_days_before_course_start_date': None,
        },
        # Not nudgeable due to assignment state not being accepted (test state=allcoated).
        {
            'assignment_state': LearnerContentAssignmentStateChoices.ALLOCATED,
            'expected_task_call_days_before_course_start_date': None,
        },
        # Not nudgeable due to assignment state not being accepted (test state=errored).
        {
            'assignment_state': LearnerContentAssignmentStateChoices.ERRORED,
            'expected_task_call_days_before_course_start_date': None,
        },
        # Not nudgeable due to assignment state not being accepted (test state=cancelled).
        {
            'assignment_state': LearnerContentAssignmentStateChoices.CANCELLED,
            'expected_task_call_days_before_course_start_date': None,
        },
    )
    @ddt.unpack
    @mock.patch(COMMAND_PATH + '.get_content_metadata_for_assignments')
    @mock.patch('enterprise_access.apps.content_assignments.tasks.send_exec_ed_enrollment_warmer.delay')
    def test_command(
            self,
            mock_send_exec_ed_enrollment_warmer_task,
            mock_content_metadata_for_assignments,
            course_starts_in_x_days=14,
            course_type='executive-education-2u',
            missing_content_metadata=False,
            assignment_preferred_course_run_key=COURSE_RUN_KEY,
            assignment_state=LearnerContentAssignmentStateChoices.ACCEPTED,
            expected_task_call_days_before_course_start_date=14,
    ):
        """
        Test that a nudge email either was or was not sent.

        This also tests skipping legacy assignments with a blank/null `preferred_course_run_key` field.
        """
        assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_configuration,
            learner_email='alice@foo.com',
            lms_user_id=None,
            content_key=self.COURSE_KEY,
            is_assigned_course_run=False,
            preferred_course_run_key=assignment_preferred_course_run_key,
            content_title='edx: Privacy 101',
            content_quantity=-123,
            state=assignment_state,
        )
        course_start_date = timezone.now().replace(microsecond=0) + timezone.timedelta(days=course_starts_in_x_days)
        if missing_content_metadata:
            mock_content_metadata_for_assignments.return_value = {}
        else:
            mock_content_metadata_for_assignments.return_value = {
                self.COURSE_KEY: {
                    'key': self.COURSE_KEY,
                    'course_type': course_type,
                    'normalized_metadata': {
                        'start_date': course_start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                    'normalized_metadata_by_run': {
                        self.COURSE_RUN_KEY: {
                            'start_date': course_start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        },
                    },
                },
            }
        call_command(self.command, days_before_course_start_date=14)
        if expected_task_call_days_before_course_start_date:
            # Happy case: a nudge email was sent.
            mock_send_exec_ed_enrollment_warmer_task.assert_called_once_with(
                assignment.uuid,
                expected_task_call_days_before_course_start_date,
            )
        else:
            mock_send_exec_ed_enrollment_warmer_task.assert_not_called()
