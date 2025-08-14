"""
Tests for the ``api.py`` module of the content_assignments app.
"""
import re
from unittest import mock

import ddt
from django.test import TestCase
from django.utils import timezone

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.subsidy_access_policy.tests.factories import AssignedLearnerCreditAccessPolicyFactory
from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates
from enterprise_access.apps.subsidy_request.tests.factories import LearnerCreditRequestFactory

from ..api import (
    AllocationException,
    allocate_assignment_for_request,
    allocate_assignments,
    cancel_assignments,
    expire_assignment,
    get_allocated_quantity_for_configuration,
    get_assignment_for_learner,
    get_assignments_for_configuration
)
from ..constants import (
    NUM_DAYS_BEFORE_AUTO_EXPIRATION,
    RETIRED_EMAIL_ADDRESS_FORMAT,
    LearnerContentAssignmentStateChoices
)
from ..models import AssignmentConfiguration, LearnerContentAssignment
from .factories import LearnerContentAssignmentFactory

# This is normally much larger (350), but that blows up the test duration.
TEST_USER_EMAIL_READ_BATCH_SIZE = 4


def delta_t(as_string=False, **kwargs):
    """
    Convenience function for getting a datetime different from the current time.
    """
    datetime_obj = timezone.now() + timezone.timedelta(**kwargs)
    if as_string:
        return datetime_obj.strftime('%Y-%m-%d %H:%M:%SZ')
    return datetime_obj


def expirable_assignments_with_content_type():
    """
    Returns a list of tuples containing expirable assignment states and the corresponding
    assignment content type (course-level or run-based).

    Each tuple contains:
    - expirable_assignment_state: The current state of the assignment.
    - is_assigned_course_run: Boolean indicating if the assignment is course-level (True) or run-based (False).
    """
    return [
        (expirable_assignment_state, is_assigned_course_run)
        for expirable_assignment_state in LearnerContentAssignmentStateChoices.EXPIRABLE_STATES
        for is_assigned_course_run in [True, False]
    ]


@ddt.ddt
class TestContentAssignmentApi(TestCase):
    """
    Tests functions of the ``content_assignment.api`` module.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.assignment_configuration = AssignmentConfiguration.objects.create()
        cls.other_assignment_configuration = AssignmentConfiguration.objects.create()

    def test_get_assignments_for_configuration(self):
        """
        Simple test to fetch assignment records related to a given configuration.
        """
        expected_assignments = [
            LearnerContentAssignmentFactory.create(
                assignment_configuration=self.assignment_configuration,
            ) for _ in range(10)
        ]

        with self.assertNumQueries(1):
            actual_assignments = list(get_assignments_for_configuration(self.assignment_configuration))

        self.assertEqual(
            sorted(actual_assignments, key=lambda record: record.uuid),
            sorted(expected_assignments, key=lambda record: record.uuid),
        )

    def test_get_assignments_for_configuration_different_states(self):
        """
        Simple test to fetch assignment records related to a given configuration,
        filtered among different states
        """
        expected_assignments = {
            LearnerContentAssignmentStateChoices.CANCELLED: [],
            LearnerContentAssignmentStateChoices.ACCEPTED: [],
        }
        for index in range(10):
            if index % 2:
                state = LearnerContentAssignmentStateChoices.CANCELLED
            else:
                state = LearnerContentAssignmentStateChoices.ACCEPTED

            expected_assignments[state].append(
                LearnerContentAssignmentFactory.create(
                    assignment_configuration=self.assignment_configuration,
                    state=state,
                )
            )

        for filter_state in (
            LearnerContentAssignmentStateChoices.CANCELLED,
            LearnerContentAssignmentStateChoices.ACCEPTED,
        ):
            with self.assertNumQueries(1):
                actual_assignments = list(
                    get_assignments_for_configuration(
                        self.assignment_configuration,
                        state=filter_state
                    )
                )

            self.assertEqual(
                sorted(actual_assignments, key=lambda record: record.uuid),
                sorted(expected_assignments[filter_state], key=lambda record: record.uuid),
            )

    @mock.patch(
        'enterprise_access.apps.content_assignments.api.get_and_cache_content_metadata',
        return_value=mock.MagicMock(),
    )
    @ddt.data(
        # [course run] Standard happy path.
        {
            'assignment_content_key': 'course-v1:test+course+run',
            'assignment_parent_content_key': 'test+course',
            'assignment_is_course_run': True,
            'assignment_lms_user_id': 1,
            'request_default_assignment_configuration': True,
            'request_lms_user_id': 1,
            'request_content_key': 'course-v1:test+course+run',
            'expect_assignment_found': True,
        },
        # [course] Standard happy path.
        {
            'assignment_content_key': 'test+course',
            'assignment_parent_content_key': None,
            'assignment_is_course_run': False,
            'assignment_lms_user_id': 1,
            'request_default_assignment_configuration': True,
            'request_lms_user_id': 1,
            'request_content_key': 'course-v1:test+course+run',
            'expect_assignment_found': True,
        },
        # [course run] Happy path, requested content is a course
        {
            'assignment_content_key': 'course-v1:test+course+run',
            'assignment_parent_content_key': 'test+course',
            'assignment_is_course_run': True,
            'assignment_lms_user_id': 1,
            'request_default_assignment_configuration': True,
            'request_lms_user_id': 1,
            'request_content_key': 'test+course',
            'expect_assignment_found': True,
        },
        # [course] Happy path, requested content is a course
        {
            'assignment_content_key': 'test+course',
            'assignment_parent_content_key': None,
            'assignment_is_course_run': False,
            'assignment_lms_user_id': 1,
            'request_default_assignment_configuration': True,
            'request_lms_user_id': 1,
            'request_content_key': 'test+course',
            'expect_assignment_found': True,
        },
        # [course run] Different lms_user_id, requested content is a course
        {
            'assignment_content_key': 'course-v1:test+course+run',
            'assignment_parent_content_key': 'test+course',
            'assignment_is_course_run': True,
            'assignment_lms_user_id': 1,
            'request_default_assignment_configuration': True,
            'request_lms_user_id': 2,  # Different lms_user_id!
            'request_content_key': 'test+course',
            'expect_assignment_found': False,
        },
        # [course] Different lms_user_id, requested content is a course
        {
            'assignment_content_key': 'test+course',
            'assignment_parent_content_key': None,
            'assignment_is_course_run': False,
            'assignment_lms_user_id': 1,
            'request_default_assignment_configuration': True,
            'request_lms_user_id': 2,  # Different lms_user_id!
            'request_content_key': 'test+course',
            'expect_assignment_found': False,
        },
        # [course run] Different lms_user_id
        {
            'assignment_content_key': 'course-v1:test+course+run',
            'assignment_parent_content_key': 'test+course',
            'assignment_is_course_run': True,
            'assignment_lms_user_id': 1,
            'request_default_assignment_configuration': True,
            'request_lms_user_id': 2,  # Different lms_user_id!
            'request_content_key': 'course-v1:test+course+run',
            'expect_assignment_found': False,
        },
        # [course] Different lms_user_id
        {
            'assignment_content_key': 'test+course',
            'assignment_parent_content_key': None,
            'assignment_is_course_run': False,
            'assignment_lms_user_id': 1,
            'request_default_assignment_configuration': True,
            'request_lms_user_id': 2,  # Different lms_user_id!
            'request_content_key': 'course-v1:test+course+run',
            'expect_assignment_found': False,
        },
        # [course run] Different customer, requested content is a course
        {
            'assignment_content_key': 'course-v1:test+course+run',
            'assignment_parent_content_key': 'test+course',
            'assignment_is_course_run': True,
            'assignment_lms_user_id': 1,
            'request_default_assignment_configuration': False,  # Different customer!
            'request_lms_user_id': 1,
            'request_content_key': 'test+course',
            'expect_assignment_found': False,
        },
        # [course] Different customer, requested content is a course
        {
            'assignment_content_key': 'test+course',
            'assignment_parent_content_key': None,
            'assignment_is_course_run': False,
            'assignment_lms_user_id': 1,
            'request_default_assignment_configuration': False,  # Different customer!
            'request_lms_user_id': 1,
            'request_content_key': 'test+course',
            'expect_assignment_found': False,
        },
        # [course run] Different customer
        {
            'assignment_content_key': 'course-v1:test+course+run',
            'assignment_parent_content_key': 'test+course',
            'assignment_is_course_run': True,
            'assignment_lms_user_id': 1,
            'request_default_assignment_configuration': False,  # Different customer!
            'request_lms_user_id': 1,
            'request_content_key': 'course-v1:test+course+run',
            'expect_assignment_found': False,
        },
        # [course] Different customer
        {
            'assignment_content_key': 'test+course',
            'assignment_parent_content_key': None,
            'assignment_is_course_run': False,
            'assignment_lms_user_id': 1,
            'request_default_assignment_configuration': False,  # Different customer!
            'request_lms_user_id': 1,
            'request_content_key': 'course-v1:test+course+run',
            'expect_assignment_found': False,
        },
    )
    @ddt.unpack
    def test_get_assignment_for_learner(
        self,
        mock_get_and_cache_content_metadata,
        assignment_content_key,
        assignment_parent_content_key,
        assignment_is_course_run,
        assignment_lms_user_id,
        request_default_assignment_configuration,
        request_lms_user_id,
        request_content_key,
        expect_assignment_found,
    ):
        """
        Test get_assignment_for_learner().
        """
        mock_get_and_cache_content_metadata.return_value = {
            'content_key': assignment_content_key,
        }
        LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            content_key=assignment_content_key,
            parent_content_key=assignment_parent_content_key,
            is_assigned_course_run=assignment_is_course_run,
            lms_user_id=assignment_lms_user_id,
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
        )
        actual_assignment = get_assignment_for_learner(
            (
                self.assignment_configuration
                if request_default_assignment_configuration
                else self.other_assignment_configuration
            ),
            request_lms_user_id,
            request_content_key,
        )
        assert (actual_assignment is not None) == expect_assignment_found

    def test_get_allocated_quantity_for_configuration(self):
        """
        Tests to verify that we can fetch the total allocated quantity across a set of assignments
        related to some configuration.
        """
        for amount in (-1000, -2000, -3000):
            LearnerContentAssignmentFactory.create(
                assignment_configuration=self.assignment_configuration,
                content_quantity=amount,
            )

        with self.assertNumQueries(1):
            actual_amount = get_allocated_quantity_for_configuration(self.assignment_configuration)
            self.assertEqual(actual_amount, -6000)

    def test_get_allocated_quantity_zero(self):
        """
        Tests to verify that the total allocation amount is zero for a given
        configuration if no assignments relate to it.
        """
        other_config = AssignmentConfiguration.objects.create()

        with self.assertNumQueries(1):
            actual_amount = get_allocated_quantity_for_configuration(other_config)
            self.assertEqual(actual_amount, 0)

    def test_allocate_assignment_for_request_negative_quantity(self):
        """
        Tests the allocation of new assignment for a price < 0
        raises an exception.
        """
        content_key = 'edX+demoX'
        content_price_cents = -1
        learners_to_assign = 'test@email.com'
        lms_user_id = 12345

        with self.assertRaisesRegex(AllocationException, 'price must be >= 0'):
            allocate_assignment_for_request(
                self.assignment_configuration,
                learners_to_assign,
                content_key,
                content_price_cents,
                lms_user_id
            )

    def test_allocate_assignments_negative_quantity(self):
        """
        Tests the allocation of new assignments for a price < 0
        raises an exception.
        """
        content_key = 'demoX'
        content_price_cents = -1
        learners_to_assign = [
            f'{name}@foo.com' for name in ('alice', 'bob', 'carol', 'david', 'eugene')
        ]

        with self.assertRaisesRegex(AllocationException, 'price must be >= 0'):
            allocate_assignments(
                self.assignment_configuration,
                learners_to_assign,
                content_key,
                content_price_cents,
            )

    # pylint: disable=too-many-statements
    @mock.patch('enterprise_access.apps.content_assignments.api.send_email_for_new_assignment')
    @mock.patch('enterprise_access.apps.content_assignments.api.create_pending_enterprise_learner_for_assignment_task')
    @mock.patch(
        'enterprise_access.apps.content_assignments.api.get_and_cache_content_metadata',
        return_value=mock.MagicMock(),
    )
    @ddt.data(
        {'is_assigned_course_run': False},
        {'is_assigned_course_run': True},
    )
    @ddt.unpack
    def test_allocate_assignments_happy_path(
        self,
        mock_get_and_cache_content_metadata,
        mock_pending_learner_task,
        mock_new_assignment_email_task,
        is_assigned_course_run,
    ):
        """
        Tests the allocation of new assignments against a given configuration.
        """
        course_key = 'edX+DemoX'
        content_title = 'edx: Demo 101'
        content_price_cents = 100

        # Course-level assignments
        content_key = course_key
        parent_content_key = None
        course_run_key_old = 'course-v1:edX+DemoX+1T2022'
        course_run_key = 'course-v1:edX+DemoX+2T2023'

        # Course run-level assignments
        if is_assigned_course_run:
            content_key = course_run_key
            parent_content_key = course_key

        # add a duplicate email to the input list to ensure only one
        # allocation occurs.
        # We throw a couple ALL UPPER CASE emails into the requested emails to allocate
        # to verify that we filter for pre-existing assignments in a case-insensitive manner.
        learners_to_assign = [
            f'{name}@foo.com' for name in (
                'ALICE',
                'bob',
                'CAROL',
                'david',
                'eugene',
                'eugene',
                'BOB',
                'eugene',
                'faythe',
                'erin',
                'mary',
                'grace',
                'xavier',
                'steve',
                'kian',
            )
        ]
        mock_get_and_cache_content_metadata.return_value = {
            'content_title': content_title,
            'content_key': course_key,
            'course_run_key': course_run_key,
        }

        default_factory_options = {
            'assignment_configuration': self.assignment_configuration,
            'content_key': content_key,
            'parent_content_key': parent_content_key,
            'is_assigned_course_run': is_assigned_course_run,
            'preferred_course_run_key': content_key if is_assigned_course_run else course_run_key,
            'content_title': content_title,
            'content_quantity': -content_price_cents,
        }

        # Allocated assignment should not be updated.
        allocated_assignment = LearnerContentAssignmentFactory.create(**{
            **default_factory_options,
            'learner_email': 'alice@foo.com',
            'state': LearnerContentAssignmentStateChoices.ALLOCATED,
        })
        # Allocated assignment SHOULD be updated because it had an outdated run.
        allocated_assignment_old_run = LearnerContentAssignmentFactory.create(**{
            **default_factory_options,
            'learner_email': 'erin@foo.com',
            'state': LearnerContentAssignmentStateChoices.ALLOCATED,
            # outdated run should trigger update.
            'preferred_course_run_key': course_run_key_old,
        })
        # Allocated assignment SHOULD be updated because it had an outdated parent_content_key.
        allocated_assignment_old_parent_content_key = LearnerContentAssignmentFactory.create(**{
            **default_factory_options,
            'learner_email': 'mary@foo.com',
            'state': LearnerContentAssignmentStateChoices.ALLOCATED,
            # outdated parent_content_key should trigger update.
            'parent_content_key': None,
        })
        # Allocated assignment SHOULD be updated because it had an outdated is_assigned_course_run.
        allocated_assignment_old_run_based = LearnerContentAssignmentFactory.create(**{
            **default_factory_options,
            'learner_email': 'xavier@foo.com',
            'state': LearnerContentAssignmentStateChoices.ALLOCATED,
            # outdated is_assigned_course_run should trigger update.
            'is_assigned_course_run': False,
        })
        # Allocated assignment SHOULD be updated because it had a NULL run.
        allocated_assignment_null_run = LearnerContentAssignmentFactory.create(**{
            **default_factory_options,
            'learner_email': 'faythe@foo.com',
            'state': LearnerContentAssignmentStateChoices.ALLOCATED,
            # outdated run should trigger update.
            'preferred_course_run_key': None,
        })
        # Accepted assignment should not be updated.
        accepted_assignment = LearnerContentAssignmentFactory.create(**{
            **default_factory_options,
            'learner_email': 'BOB@foo.com',
            'state': LearnerContentAssignmentStateChoices.ACCEPTED,
        })
        # Cancelled assignment should be updated/re-allocated.
        cancelled_assignment = LearnerContentAssignmentFactory.create(**{
            **default_factory_options,
            'learner_email': 'carol@foo.com',
            'state': LearnerContentAssignmentStateChoices.CANCELLED,
        })
        # Cancelled assignment should be updated/re-allocated (including recieving a new run).
        cancelled_assignment_old_run = LearnerContentAssignmentFactory.create(**{
            **default_factory_options,
            'learner_email': 'grace@foo.com',
            'state': LearnerContentAssignmentStateChoices.CANCELLED,
            # outdated run should trigger update.
            'preferred_course_run_key': course_run_key_old,
        })
        # Cancelled assignment should be updated/re-allocated (including recieving new parent_content_key).
        cancelled_assignment_old_parent_content_key = LearnerContentAssignmentFactory.create(**{
            **default_factory_options,
            'learner_email': 'steve@foo.com',
            'state': LearnerContentAssignmentStateChoices.CANCELLED,
            # outdated parent_content_key should trigger update.
            'parent_content_key': None,
        })
        # Cancelled assignment should be updated/re-allocated (including recieving new is_assigned_course_run).
        cancelled_assignment_old_run_based = LearnerContentAssignmentFactory.create(**{
            **default_factory_options,
            'learner_email': 'kian@foo.com',
            'state': LearnerContentAssignmentStateChoices.CANCELLED,
            # outdated is_assigned_course_run should trigger update.
            'is_assigned_course_run': False,
        })
        # Errored assignment should be updated/re-allocated.
        errored_assignment = LearnerContentAssignmentFactory.create(**{
            **default_factory_options,
            'learner_email': 'david@foo.com',
            'state': LearnerContentAssignmentStateChoices.ERRORED,
        })

        allocation_results = allocate_assignments(
            self.assignment_configuration,
            learners_to_assign,
            content_key,
            content_price_cents,
        )

        # Refresh from db to get any updates reflected in the python objects.
        assignments_to_refresh = (
            allocated_assignment,
            allocated_assignment_old_run,
            allocated_assignment_null_run,
            accepted_assignment,
            cancelled_assignment,
            cancelled_assignment_old_run,
            cancelled_assignment_old_parent_content_key,
            cancelled_assignment_old_run_based,
            errored_assignment,
        )
        if is_assigned_course_run:
            assignments_to_refresh += (
                allocated_assignment_old_parent_content_key,
                allocated_assignment_old_run_based,
            )

        for record in assignments_to_refresh:
            record.refresh_from_db()

        # Create list of assignments expected to be updated, including:
        #   - The allocated assignments with outdated: preferred course run, parent_content_key, is_assigned_course_run.
        #   - Errored assignments
        #   - Cancelled assignments
        expected_updated_assignments = (
            allocated_assignment_old_run,
            allocated_assignment_null_run,
            cancelled_assignment,
            cancelled_assignment_old_run,
            cancelled_assignment_old_parent_content_key,
            cancelled_assignment_old_run_based,
            errored_assignment,
        )

        if is_assigned_course_run:
            expected_updated_assignments += (
                allocated_assignment_old_parent_content_key,
                allocated_assignment_old_run_based,
            )

        if is_assigned_course_run:
            expected_updated_assignments += (
                allocated_assignment_old_parent_content_key,
                allocated_assignment_old_run_based,
            )

        self.assertEqual(
            {record.uuid for record in allocation_results['updated']},
            {assignment.uuid for assignment in expected_updated_assignments},
        )
        for assignment in expected_updated_assignments:
            self.assertEqual(len(assignment.history.all()), 2)

        # The allocated assignment (with latest run) and accepted assignment should be the only things with no change.
        expected_no_change_assignments = (
            allocated_assignment,
            accepted_assignment,
        )
        if not is_assigned_course_run:
            expected_no_change_assignments += (
                allocated_assignment_old_parent_content_key,
                allocated_assignment_old_run_based,
            )
        self.assertEqual(
            {record.uuid for record in allocation_results['no_change']},
            {assignment.uuid for assignment in expected_no_change_assignments},
        )
        for record in expected_no_change_assignments:
            self.assertEqual(len(record.history.all()), 1)

        # The existing assignments should be 'allocated' now, except for the already accepted one
        self.assertEqual(cancelled_assignment.state, LearnerContentAssignmentStateChoices.ALLOCATED)
        self.assertEqual(cancelled_assignment_old_run.state, LearnerContentAssignmentStateChoices.ALLOCATED)
        self.assertEqual(
            cancelled_assignment_old_parent_content_key.state, LearnerContentAssignmentStateChoices.ALLOCATED
        )
        self.assertEqual(cancelled_assignment_old_run_based.state, LearnerContentAssignmentStateChoices.ALLOCATED)
        self.assertEqual(errored_assignment.state, LearnerContentAssignmentStateChoices.ALLOCATED)
        self.assertEqual(allocated_assignment.state, LearnerContentAssignmentStateChoices.ALLOCATED)
        self.assertEqual(allocated_assignment_old_run.state, LearnerContentAssignmentStateChoices.ALLOCATED)
        self.assertEqual(allocated_assignment_null_run.state, LearnerContentAssignmentStateChoices.ALLOCATED)
        self.assertEqual(
            allocated_assignment_old_parent_content_key.state, LearnerContentAssignmentStateChoices.ALLOCATED
        )
        self.assertEqual(allocated_assignment_old_run_based.state, LearnerContentAssignmentStateChoices.ALLOCATED)
        self.assertEqual(accepted_assignment.state, LearnerContentAssignmentStateChoices.ACCEPTED)

        # We should have created only one new, allocated assignment for eugene@foo.com
        self.assertEqual(len(allocation_results['created']), 1)
        created_assignment = allocation_results['created'][0]
        self.assertEqual(created_assignment.assignment_configuration, self.assignment_configuration)
        self.assertEqual(created_assignment.learner_email, 'eugene@foo.com')
        self.assertEqual(created_assignment.lms_user_id, None)
        self.assertEqual(created_assignment.content_key, content_key)
        self.assertEqual(created_assignment.preferred_course_run_key, course_run_key)
        self.assertEqual(created_assignment.content_title, content_title)
        self.assertEqual(created_assignment.content_quantity, -1 * content_price_cents)
        self.assertEqual(created_assignment.state, LearnerContentAssignmentStateChoices.ALLOCATED)

        # Assert that an async task was enqueued for each of the updated and created assignments
        mock_pending_learner_task.delay.assert_has_calls([
            mock.call(assignment.uuid) for assignment in
            (
                cancelled_assignment,
                cancelled_assignment_old_run,
                errored_assignment,
                created_assignment
            )
        ], any_order=True)
        # Assert that an async task to send notification emails was enqueued
        # for each of the updated and created assignments
        mock_new_assignment_email_task.delay.assert_has_calls([
            mock.call(assignment.uuid) for assignment in
            (
                cancelled_assignment,
                cancelled_assignment_old_run,
                errored_assignment,
                created_assignment,
            )
        ], any_order=True)

    @mock.patch(
        'enterprise_access.apps.content_assignments.api.get_and_cache_content_metadata',
        return_value=mock.MagicMock(),
    )
    @ddt.unpack
    def test_allocate_assignment_for_request_happy_path(
        self,
        mock_get_and_cache_content_metadata,
    ):
        """
        Tests allocate_assignment_for_request retuns a newly created assignment.
        """
        course_key = 'edX+DemoX'
        course_run_key = 'course-v1:edX+DemoX+2T2023'
        content_title = 'Test Demo Course'
        content_price_cents = 100

        mock_get_and_cache_content_metadata.return_value = {
            'content_title': content_title,
            'content_key': course_key,
            'course_run_key': course_run_key,
        }

        learner_to_assign = 'test@email.com'
        lms_user_id = 12345

        created_assignment = allocate_assignment_for_request(
            self.assignment_configuration,
            learner_to_assign,
            course_key,
            content_price_cents,
            lms_user_id
        )
        self.assertEqual(created_assignment.assignment_configuration, self.assignment_configuration)
        self.assertEqual(created_assignment.learner_email, learner_to_assign)
        self.assertEqual(created_assignment.lms_user_id, lms_user_id)
        self.assertEqual(created_assignment.content_key, course_key)
        self.assertEqual(created_assignment.preferred_course_run_key, course_run_key)
        self.assertEqual(created_assignment.content_title, content_title)
        self.assertEqual(created_assignment.content_quantity, -1 * content_price_cents)
        self.assertEqual(created_assignment.state, LearnerContentAssignmentStateChoices.ALLOCATED)

    @mock.patch('enterprise_access.apps.content_assignments.tasks.send_cancel_email_for_pending_assignment')
    def test_cancel_assignments_happy_path(self, mock_notify):
        """
        Tests the allocation of new assignments against a given configuration.
        """
        allocated_assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            learner_email='alice@foo.com',
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
        )
        accepted_assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            learner_email='bob@foo.com',
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
        )
        cancelled_assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            learner_email='carol@foo.com',
            state=LearnerContentAssignmentStateChoices.CANCELLED,
        )
        errored_assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            learner_email='david@foo.com',
            state=LearnerContentAssignmentStateChoices.ERRORED,
        )

        # Cancel all the test assignments we just created.
        assignments_to_cancel = [
            allocated_assignment,
            accepted_assignment,
            cancelled_assignment,
            errored_assignment,
        ]
        cancellation_info = cancel_assignments(assignments_to_cancel)

        # Refresh from db to get any updates reflected in the python objects.
        for record in (allocated_assignment, accepted_assignment, cancelled_assignment, errored_assignment):
            record.refresh_from_db()

        # The two updated assignments PLUS the already cancelled assignment should be considered "cancelled" in the
        # return value.
        assert set(cancellation_info['cancelled']) == set([
            allocated_assignment,
            cancelled_assignment,
            errored_assignment,
        ])
        assert set(cancellation_info['non_cancelable']) == set([
            accepted_assignment,
        ])

        # The allocated and errored assignments should be the only things updated.
        for record in (allocated_assignment, errored_assignment):
            assert len(record.history.all()) == 2

        # The accepted and canceled assignments should be the only things with no change.
        for record in (accepted_assignment, cancelled_assignment):
            assert len(record.history.all()) == 1

        # All but the accepted assignment should be 'canceled' now.
        self.assertEqual(allocated_assignment.state, LearnerContentAssignmentStateChoices.CANCELLED)
        self.assertEqual(accepted_assignment.state, LearnerContentAssignmentStateChoices.ACCEPTED)
        self.assertEqual(cancelled_assignment.state, LearnerContentAssignmentStateChoices.CANCELLED)
        self.assertEqual(errored_assignment.state, LearnerContentAssignmentStateChoices.CANCELLED)
        mock_notify.delay.assert_has_calls([
            mock.call(assignment.uuid) for assignment in (allocated_assignment, errored_assignment)
        ], any_order=True)

    @mock.patch('enterprise_access.apps.content_assignments.api.send_email_for_new_assignment')
    @mock.patch(
        'enterprise_access.apps.content_assignments.api.create_pending_enterprise_learner_for_assignment_task'
    )
    @mock.patch(
        'enterprise_access.apps.content_assignments.api.get_and_cache_content_metadata',
        return_value=mock.MagicMock(),
    )
    @ddt.data(
        {
            'user_exists': True,
            'existing_assignment_state': None,
        },
        {
            'user_exists': False,
            'existing_assignment_state': None,
        },
        {
            'user_exists': True,
            'existing_assignment_state': LearnerContentAssignmentStateChoices.ALLOCATED,
        },
        {
            'user_exists': False,
            'existing_assignment_state': LearnerContentAssignmentStateChoices.ALLOCATED,
        },
        {
            'user_exists': True,
            'existing_assignment_state': LearnerContentAssignmentStateChoices.ACCEPTED,
        },
        {
            'user_exists': False,
            'existing_assignment_state': LearnerContentAssignmentStateChoices.ACCEPTED,
        },
        {
            'user_exists': True,
            'existing_assignment_state': LearnerContentAssignmentStateChoices.CANCELLED,
        },
        {
            'user_exists': False,
            'existing_assignment_state': LearnerContentAssignmentStateChoices.CANCELLED,
        },
        {
            'user_exists': True,
            'existing_assignment_state': LearnerContentAssignmentStateChoices.ERRORED,
        },
        {
            'user_exists': False,
            'existing_assignment_state': LearnerContentAssignmentStateChoices.ERRORED,
        },
    )
    @ddt.unpack
    def test_allocate_assignments_set_lms_user_id(
        self,
        mock_get_and_cache_content_metadata,
        mock_pending_learner_task,
        _mock_send_email_for_new_assignment,
        user_exists,
        existing_assignment_state,
    ):
        """
        Tests that allocating assignments correctly sets the lms_user_id when a user pre-exists with a matching email.
        """
        content_key = 'demoX'
        course_run_key = 'demoX+1T2022'
        content_title = 'edx: Demo 101'
        content_price_cents = 100
        learner_email = 'alice@foo.com'
        lms_user_id = 999
        mock_get_and_cache_content_metadata.return_value = {
            'content_title': content_title,
            'content_key': content_key,
            'content_price': content_price_cents,
            'course_run_key': course_run_key,
        }

        if user_exists:
            UserFactory(username='alice', email=learner_email.upper(), lms_user_id=lms_user_id)

        assignment = None
        if existing_assignment_state:
            assignment = LearnerContentAssignmentFactory.create(
                assignment_configuration=self.assignment_configuration,
                learner_email=learner_email,
                lms_user_id=None,
                content_key=content_key,
                content_title=content_title,
                content_quantity=-content_price_cents,
                state=existing_assignment_state,
            )

        allocate_assignments(
            self.assignment_configuration,
            [learner_email],
            content_key,
            content_price_cents,
        )

        # Get the latest assignment from the db.
        assignment = LearnerContentAssignment.objects.get(learner_email=learner_email)

        # We should have updated the lms_user_id of the assignment IFF a user pre-existed.
        if user_exists:
            assert assignment.lms_user_id == lms_user_id
        else:
            assert assignment.lms_user_id is None

        if not existing_assignment_state or (
            existing_assignment_state in (LearnerContentAssignmentStateChoices.REALLOCATE_STATES)
        ):
            mock_pending_learner_task.delay.assert_called_once_with(assignment.uuid)


@ddt.ddt
class TestAssignmentExpiration(TestCase):
    """
    Tests of the following API methods:
      - ``api.expire_assignment()``
      - ``api.get_automatic_expiration_date_and_reason()``
      - ``api.get_subsidy_expiration()``
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.assignment_configuration = AssignmentConfiguration.objects.create()
        cls.policy = AssignedLearnerCreditAccessPolicyFactory.create(
            assignment_configuration=cls.assignment_configuration,
            spend_limit=1000000,
        )

    def mock_content_metadata(self, content_key, course_run_key, enroll_by_date):
        """
        Helper to produce content metadata with a given enroll_by_date.
        """
        return {
            'key': content_key,
            'normalized_metadata': {
                'enroll_by_date': enroll_by_date,
            },
            'normalized_metadata_by_run': {
                course_run_key: {
                    'enroll_by_date': enroll_by_date,
                },
            },
        }

    def test_dont_expire_accepted_assignment(self):
        """
        Don't expire an accepted assignment, even if one of the key dates has passed.
        """
        assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
        )
        original_modified_time = assignment.modified

        # set a policy-subsidy expiration date in the past
        mock_subsidy_record = {'expiration_datetime': delta_t(days=-100, as_string=True)}
        with mock.patch.object(self.policy, 'subsidy_record', return_value=mock_subsidy_record):
            expire_assignment(
                assignment,
                content_metadata=self.mock_content_metadata('edX+DemoX', 'course-v1:edX+DemoX+T2024', None),
                modify_assignment=True,
            )

        assignment.refresh_from_db()
        self.assertEqual(assignment.state, LearnerContentAssignmentStateChoices.ACCEPTED)
        self.assertEqual(assignment.modified, original_modified_time)

    @mock.patch('enterprise_access.apps.content_assignments.api.send_assignment_automatically_expired_email')
    @ddt.data(
        *LearnerContentAssignmentStateChoices.EXPIRABLE_STATES
    )
    def test_dont_expire_assignments_with_future_expiration_dates(self, assignment_state, mock_expired_email):
        """
        Tests that we don't expire assignments with all types of expiration dates in the future.
        """
        assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            state=assignment_state,
            learner_email='larry@stooges.com',
            lms_user_id=12345,
        )
        original_modified_time = assignment.modified
        assignment.add_successful_notified_action()

        # set a policy-subsidy expiration date in the future
        mock_subsidy_record = {'expiration_datetime': delta_t(days=100, as_string=True)}
        with mock.patch.object(self.policy, 'subsidy_record', return_value=mock_subsidy_record):
            expire_assignment(
                assignment,
                content_metadata=self.mock_content_metadata(
                    'edX+DemoX',
                    'course-v1:edX+DemoX+T2024',
                    delta_t(days=100, as_string=True)
                ),
                modify_assignment=True,
            )

        assignment.refresh_from_db()
        self.assertEqual(assignment.state, assignment_state)
        self.assertFalse(mock_expired_email.delay.called)
        self.assertEqual(assignment.modified, original_modified_time)

    @ddt.data(
        *LearnerContentAssignmentStateChoices.EXPIRABLE_STATES
    )
    @mock.patch('enterprise_access.apps.content_assignments.api.send_assignment_automatically_expired_email')
    def test_expire_one_assignment_automatically(
        self,
        assignment_state,
        mock_expired_email,
    ):
        """
        Test that we expire an assignment and clear
        its PII, as long the state is not "accepted".
        """
        # set the allocation time to be more than the threshold number of days ago
        enough_days_to_be_cancelled = NUM_DAYS_BEFORE_AUTO_EXPIRATION + 1
        assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            state=assignment_state,
            learner_email='larry@stooges.com',
            lms_user_id=12345,
            allocated_at=delta_t(days=-enough_days_to_be_cancelled),
        )

        # set a policy-subsidy expiration date in the future
        mock_subsidy_record = {'expiration_datetime': delta_t(days=100, as_string=True)}
        with mock.patch.object(self.policy, 'subsidy_record', return_value=mock_subsidy_record):
            expire_assignment(
                assignment,
                content_metadata=self.mock_content_metadata('edX+DemoX', 'course-v1:edX+DemoX+T2024', None),
                modify_assignment=True,
            )

        assignment.refresh_from_db()
        self.assertEqual(assignment.state, LearnerContentAssignmentStateChoices.EXPIRED)
        self.assertEqual(12345, assignment.lms_user_id)
        pattern = RETIRED_EMAIL_ADDRESS_FORMAT.format('[a-f0-9]{16}')
        self.assertIsNotNone(re.match(pattern, assignment.learner_email))

        for historical_record in assignment.history.all():
            self.assertEqual(12345, historical_record.lms_user_id)
            self.assertIsNotNone(re.match(pattern, historical_record.learner_email))

        mock_expired_email.delay.assert_called_once_with(assignment.uuid)

    @ddt.data(
        *expirable_assignments_with_content_type()
    )
    @ddt.unpack
    @mock.patch('enterprise_access.apps.content_assignments.api.send_assignment_automatically_expired_email')
    def test_expire_assignments_with_passed_enroll_by_date(
        self,
        expirable_assignment_state,
        is_assigned_course_run,
        mock_expired_email,
    ):
        """
        Tests that we expire assignments with a passed enroll_by_date
        """
        course_key = 'edX+DemoX'
        course_run_key = 'course-v1:edX+DemoX+T2024'
        content_key = course_key
        parent_content_key = None
        if is_assigned_course_run:
            content_key = course_run_key
            parent_content_key = course_key

        assignment = LearnerContentAssignmentFactory.create(
            content_key=content_key,
            parent_content_key=parent_content_key,
            is_assigned_course_run=is_assigned_course_run,
            assignment_configuration=self.assignment_configuration,
            state=expirable_assignment_state,
            learner_email='larry@stooges.com',
            lms_user_id=12345,
        )
        assignment.add_successful_notified_action()

        # create expired content metadata
        mock_content_metadata = self.mock_content_metadata(
            content_key=content_key,
            course_run_key=course_run_key,
            enroll_by_date=delta_t(days=-1, as_string=True),
        )

        # set a policy-subsidy expiration date in the future
        mock_subsidy_record = {'expiration_datetime': delta_t(days=100, as_string=True)}
        with mock.patch.object(self.policy, 'subsidy_record', return_value=mock_subsidy_record):
            expire_assignment(
                assignment,
                content_metadata=mock_content_metadata,
                modify_assignment=True,
            )

        assignment.refresh_from_db()

        self.assertEqual(assignment.state, LearnerContentAssignmentStateChoices.EXPIRED)
        # we don't clear PII in this case
        self.assertEqual(assignment.learner_email, 'larry@stooges.com')
        self.assertEqual(assignment.lms_user_id, 12345)
        mock_expired_email.delay.assert_called_once_with(assignment.uuid)

    @ddt.data(
        *expirable_assignments_with_content_type()
    )
    @ddt.unpack
    @mock.patch('enterprise_access.apps.content_assignments.api.send_assignment_automatically_expired_email')
    def test_expire_assignments_with_expired_subsidy(
        self,
        expirable_assignment_state,
        is_assigned_course_run,
        mock_expired_email,
    ):
        """
        Tests that we expire assignments with an underlying subsidy that has expired.
        """
        course_key = 'edX+DemoX'
        course_run_key = 'course-v1:edX+DemoX+T2024'
        content_key = course_key
        parent_content_key = None
        if is_assigned_course_run:
            content_key = course_run_key
            parent_content_key = course_key

        assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            content_key=content_key,
            parent_content_key=parent_content_key,
            is_assigned_course_run=is_assigned_course_run,
            state=expirable_assignment_state,
            learner_email='larry@stooges.com',
            lms_user_id=12345,
        )
        assignment.add_successful_notified_action()

        # create non-expired content metadata
        mock_content_metadata = self.mock_content_metadata(
            content_key=content_key,
            course_run_key=course_run_key,
            enroll_by_date=delta_t(days=100, as_string=True),
        )

        # set a policy-subsidy expiration date in the past
        mock_subsidy_record = {'expiration_datetime': delta_t(days=-1, as_string=True)}
        with mock.patch.object(self.policy, 'subsidy_record', return_value=mock_subsidy_record):
            expire_assignment(
                assignment,
                content_metadata=mock_content_metadata,
                modify_assignment=True,
            )

        assignment.refresh_from_db()

        self.assertEqual(assignment.state, LearnerContentAssignmentStateChoices.EXPIRED)
        # we don't clear PII in this case
        self.assertEqual(assignment.learner_email, 'larry@stooges.com')
        self.assertEqual(assignment.lms_user_id, 12345)
        mock_expired_email.delay.assert_called_once_with(assignment.uuid)

    @ddt.data(
        *LearnerContentAssignmentStateChoices.EXPIRABLE_STATES
    )
    @mock.patch('enterprise_access.apps.content_assignments.api.send_assignment_automatically_expired_email')
    def test_expire_credit_request_when_assigment_expires(self, expirable_assignment_state, mock_expired_email):
        """
        Tests that we expire any open credit requests when we expire an assignment.
        """
        course_key = 'edX+DemoX'
        course_run_key = 'course-v1:edX+DemoX+T2024'
        content_key = course_key

        assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            content_key=content_key,
            parent_content_key=content_key,
            is_assigned_course_run=True,
            state=expirable_assignment_state,
            learner_email='larry@stooges.com',
            lms_user_id=12345,
        )

        credit_request = LearnerCreditRequestFactory.create(
            assignment=assignment
        )

        assignment.add_successful_notified_action()

        # create non-expired content metadata
        mock_content_metadata = self.mock_content_metadata(
            content_key=content_key,
            course_run_key=course_run_key,
            enroll_by_date=delta_t(days=100, as_string=True),
        )

        # set a policy-subsidy expiration date in the past
        mock_subsidy_record = {'expiration_datetime': delta_t(days=-1, as_string=True)}
        with mock.patch.object(self.policy, 'subsidy_record', return_value=mock_subsidy_record):
            expire_assignment(
                assignment,
                content_metadata=mock_content_metadata,
                modify_assignment=True,
            )

        credit_request.refresh_from_db()
        assignment.refresh_from_db()

        self.assertEqual(assignment.state, LearnerContentAssignmentStateChoices.EXPIRED)
        self.assertEqual(credit_request.state, SubsidyRequestStates.EXPIRED)
        mock_expired_email.delay.assert_not_called()
