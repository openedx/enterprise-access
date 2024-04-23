"""
Tests for the ``api.py`` module of the content_assignments app.
"""
import re
from unittest import mock

import ddt
from django.test import TestCase
from django.utils import timezone

from enterprise_access.apps.core.models import User
from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.subsidy_access_policy.tests.factories import AssignedLearnerCreditAccessPolicyFactory

from ..api import (
    AllocationException,
    _try_populate_assignments_lms_user_id,
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
        # Standard happy path.
        {
            'assignment_content_key': 'test+course',
            'assignment_lms_user_id': 1,
            'request_default_assignment_configuration': True,
            'request_lms_user_id': 1,
            'request_content_key': 'course-v1:test+course+run',
            'expect_assignment_found': True,
        },
        # Happy path, requested content is a course (with prefix).
        {
            'assignment_content_key': 'test+course',
            'assignment_lms_user_id': 1,
            'request_default_assignment_configuration': True,
            'request_lms_user_id': 1,
            'request_content_key': 'course-v1:test+course',  # This is a course! With a prefix!
            'expect_assignment_found': True,
        },
        # Happy path, requested content is a course (without prefix).
        {
            'assignment_content_key': 'test+course',
            'assignment_lms_user_id': 1,
            'request_default_assignment_configuration': True,
            'request_lms_user_id': 1,
            'request_content_key': 'test+course',  # This is a course! Without a prefix!
            'expect_assignment_found': True,
        },
        # Different lms_user_id.
        {
            'assignment_content_key': 'test+course',
            'assignment_lms_user_id': 1,
            'request_default_assignment_configuration': True,
            'request_lms_user_id': 2,  # Different lms_user_id!
            'request_content_key': 'test+course',
            'expect_assignment_found': False,
        },
        # Different customer.
        {
            'assignment_content_key': 'test+course',
            'assignment_lms_user_id': 1,
            'request_default_assignment_configuration': False,  # Different customer!
            'request_lms_user_id': 1,
            'request_content_key': 'test+course',
            'expect_assignment_found': False,
        },
    )
    @ddt.unpack
    def test_get_assignment_for_learner(
        self,
        mock_get_and_cache_content_metadata,
        assignment_content_key,
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

    @mock.patch('enterprise_access.apps.content_assignments.api.send_email_for_new_assignment')
    @mock.patch('enterprise_access.apps.content_assignments.api.create_pending_enterprise_learner_for_assignment_task')
    @mock.patch(
        'enterprise_access.apps.content_assignments.api.get_and_cache_content_metadata',
        return_value=mock.MagicMock(),
    )
    def test_allocate_assignments_happy_path(
        self, mock_get_and_cache_content_metadata, mock_pending_learner_task, mock_new_assignment_email_task
    ):
        """
        Tests the allocation of new assignments against a given configuration.
        """
        content_key = 'demoX'
        course_run_key = 'demoX+1T2022'
        content_title = 'edx: Demo 101'
        content_price_cents = 100
        # add a duplicate email to the input list to ensure only one
        # allocation occurs.
        # We throw a couple ALL UPPER CASE emails into the requested emails to allocate
        # to verify that we filter for pre-existing assignments in a case-insensitive manner.
        learners_to_assign = [
            f'{name}@foo.com' for name in ('ALICE', 'bob', 'CAROL', 'david', 'eugene', 'eugene', 'bob', 'eugene')
        ]
        mock_get_and_cache_content_metadata.return_value = {
            'content_title': content_title,
            'course_run_key': course_run_key,
        }

        allocated_assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            learner_email='alice@foo.com',
            content_key=content_key,
            content_title=content_title,
            content_quantity=-content_price_cents,
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
        )
        accepted_assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            learner_email='BOB@foo.com',
            content_key=content_key,
            content_title=content_title,
            content_quantity=-content_price_cents,
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
        )
        cancelled_assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            learner_email='carol@foo.com',
            content_key=content_key,
            content_title=content_title,
            content_quantity=-200,
            state=LearnerContentAssignmentStateChoices.CANCELLED,
        )
        errored_assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            learner_email='david@foo.com',
            content_key=content_key,
            content_title=content_title,
            content_quantity=-200,
            state=LearnerContentAssignmentStateChoices.ERRORED,
        )

        allocation_results = allocate_assignments(
            self.assignment_configuration,
            learners_to_assign,
            content_key,
            content_price_cents,
        )

        # Refresh from db to get any updates reflected in the python objects.
        for record in (allocated_assignment, accepted_assignment, cancelled_assignment, errored_assignment):
            record.refresh_from_db()

        # The errored and cancelled assignments should be the only things updated
        self.assertEqual(
            {record.uuid for record in allocation_results['updated']},
            {cancelled_assignment.uuid, errored_assignment.uuid},
        )
        for record in (cancelled_assignment, errored_assignment):
            self.assertEqual(len(record.history.all()), 2)

        # The allocated and accepted assignments should be the only things with no change
        self.assertEqual(
            {record.uuid for record in allocation_results['no_change']},
            {allocated_assignment.uuid, accepted_assignment.uuid},
        )
        for record in (allocated_assignment, accepted_assignment):
            self.assertEqual(len(record.history.all()), 1)

        # The existing assignments should be 'allocated' now, except for the already accepted one
        self.assertEqual(cancelled_assignment.state, LearnerContentAssignmentStateChoices.ALLOCATED)
        self.assertEqual(errored_assignment.state, LearnerContentAssignmentStateChoices.ALLOCATED)
        self.assertEqual(allocated_assignment.state, LearnerContentAssignmentStateChoices.ALLOCATED)
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
            (cancelled_assignment, errored_assignment, created_assignment)
        ], any_order=True)
        # Assert that an async task to send notification emails was enqueued
        # for each of the updated and created assignments
        mock_new_assignment_email_task.delay.assert_has_calls([
            mock.call(assignment.uuid) for assignment in
            (cancelled_assignment, errored_assignment, created_assignment)
        ], any_order=True)

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

    @mock.patch(
        'enterprise_access.apps.content_assignments.api.USER_EMAIL_READ_BATCH_SIZE',
        TEST_USER_EMAIL_READ_BATCH_SIZE,
    )
    def test_try_populate_assignments_lms_user_id_with_batching(self):
        """
        Tests the _try_populate_assignments_lms_user_id() function, especially the read batching feature.

        Make 2*N users with unique emails,
        Call function on:
            0.5*N assignment with new emails,
            and 2*N assignments with existing emails,
        Where N is the max batch size.

        The result should be 3 queries (3 batches).
        """
        num_users_to_create = 2 * TEST_USER_EMAIL_READ_BATCH_SIZE
        existing_users = [UserFactory() for _ in range(num_users_to_create)]

        assignments_for_non_existing_users = [
            LearnerContentAssignmentFactory.create(
                assignment_configuration=self.assignment_configuration,
                lms_user_id=None,
                state=LearnerContentAssignmentStateChoices.ALLOCATED,
            )
            for _ in range(int(0.5 * TEST_USER_EMAIL_READ_BATCH_SIZE))
        ]
        assignments_for_existing_users = [
            LearnerContentAssignmentFactory.create(
                assignment_configuration=self.assignment_configuration,
                learner_email=existing_user.email,
                lms_user_id=None,
                state=LearnerContentAssignmentStateChoices.ALLOCATED,
            )
            for existing_user in existing_users
        ]
        # Assemble a list of assignments 2.5 longer than the max batch size.
        all_assignments = assignments_for_non_existing_users + assignments_for_existing_users

        # Call the function under test, and actually make sure only 3 queries were executed.
        with self.assertNumQueries(3):
            _ = _try_populate_assignments_lms_user_id(all_assignments)

        # save() and update all assignments from the db.
        for assignment in all_assignments:
            assignment.save()
            assignment.refresh_from_db()

        # We should not have updated the lms_user_id of the assignment if no user existed.
        for assignment in assignments_for_non_existing_users:
            assert assignment.lms_user_id is None

        # We should have updated the lms_user_id of the assignment if a user existed.
        for assignment in assignments_for_existing_users:
            assert assignment.lms_user_id == User.objects.get(email=assignment.learner_email).lms_user_id


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

    def mock_content_metadata(self, content_key, enroll_by_date):
        """
        Helper to produce content metadata with a given enroll_by_date.
        """
        return {
            'key': content_key,
            'normalized_metadata': {
                'enroll_by_date': enroll_by_date,
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
                content_metadata=self.mock_content_metadata('edX+DemoX', None),
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
                content_metadata=self.mock_content_metadata('edX+DemoX', delta_t(days=100, as_string=True)),
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
        assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            state=assignment_state,
            learner_email='larry@stooges.com',
            lms_user_id=12345,
        )
        action = assignment.add_successful_notified_action()
        # set the last notified action to be more than the threshold number of days ago
        enough_days_to_be_cancelled = NUM_DAYS_BEFORE_AUTO_EXPIRATION + 1
        action.completed_at = delta_t(days=-enough_days_to_be_cancelled)
        action.save()

        # set a policy-subsidy expiration date in the future
        mock_subsidy_record = {'expiration_datetime': delta_t(days=100, as_string=True)}
        with mock.patch.object(self.policy, 'subsidy_record', return_value=mock_subsidy_record):
            expire_assignment(
                assignment,
                content_metadata=self.mock_content_metadata('edX+DemoX', None),
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
        *LearnerContentAssignmentStateChoices.EXPIRABLE_STATES
    )
    @mock.patch('enterprise_access.apps.content_assignments.api.send_assignment_automatically_expired_email')
    def test_expire_assignments_with_passed_enroll_by_date(
        self,
        assignment_state,
        mock_expired_email,
    ):
        """
        Tests that we expire assignments with a passed enroll_by_date
        """
        content_key = 'demoX'
        assignment = LearnerContentAssignmentFactory.create(
            content_key='demoX',
            assignment_configuration=self.assignment_configuration,
            state=assignment_state,
            learner_email='larry@stooges.com',
            lms_user_id=12345,
        )
        assignment.add_successful_notified_action()

        # create expired content metadata
        mock_content_metadata = self.mock_content_metadata(
            content_key=content_key,
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
        *LearnerContentAssignmentStateChoices.EXPIRABLE_STATES
    )
    @mock.patch('enterprise_access.apps.content_assignments.api.send_assignment_automatically_expired_email')
    def test_expire_assignments_with_expired_subsidy(
        self,
        assignment_state,
        mock_expired_email,
    ):
        """
        Tests that we expire assignments with an underlying subsidy that has expired.
        """
        content_key = 'demoX'
        assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            content_key=content_key,
            state=assignment_state,
            learner_email='larry@stooges.com',
            lms_user_id=12345,
        )
        assignment.add_successful_notified_action()

        # create non-expired content metadata
        mock_content_metadata = self.mock_content_metadata(
            content_key=content_key,
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
