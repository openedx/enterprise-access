"""
Tests for the ``api.py`` module of the content_assignments app.
"""
from django.test import TestCase

from ..api import (
    AllocationException,
    allocate_assignments,
    get_allocated_quantity_for_configuration,
    get_assignments_for_configuration
)
from ..constants import LearnerContentAssignmentStateChoices
from ..models import AssignmentConfiguration
from .factories import LearnerContentAssignmentFactory


class TestContentAssignmentApi(TestCase):
    """
    Tests functions of the ``content_assignment.api`` module.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.assignment_configuration = AssignmentConfiguration.objects.create()

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

    def test_allocate_assignments_positive_quantity(self):
        """
        Tests the allocation of new assignments for a price > 0
        raises an exception.
        """
        content_key = 'demoX'
        content_price_cents = 1
        learners_to_assign = [
            f'{name}@foo.com' for name in ('alice', 'bob', 'carol', 'david', 'eugene')
        ]

        with self.assertRaisesRegex(AllocationException, 'price must be <= 0'):
            allocate_assignments(
                self.assignment_configuration,
                learners_to_assign,
                content_key,
                content_price_cents,
            )

    def test_allocate_assignments_happy_path(self):
        """
        Tests the allocation of new assignments against a given configuration.
        """
        content_key = 'demoX'
        content_price_cents = -100
        learners_to_assign = [
            f'{name}@foo.com' for name in ('alice', 'bob', 'carol', 'david', 'eugene')
        ]

        allocated_assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            learner_email='alice@foo.com',
            content_key=content_key,
            content_quantity=content_price_cents,
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
        )
        accepted_assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            learner_email='bob@foo.com',
            content_key=content_key,
            content_quantity=content_price_cents,
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
        )
        cancelled_assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            learner_email='carol@foo.com',
            content_key=content_key,
            content_quantity=-200,
            state=LearnerContentAssignmentStateChoices.CANCELLED,
        )
        errored_assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            learner_email='david@foo.com',
            content_key=content_key,
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
        self.assertEqual(created_assignment.content_key, content_key)
        self.assertEqual(created_assignment.content_quantity, content_price_cents)
        self.assertEqual(created_assignment.state, LearnerContentAssignmentStateChoices.ALLOCATED)
