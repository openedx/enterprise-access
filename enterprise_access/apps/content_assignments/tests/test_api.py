"""
Tests for the ``api.py`` module of the content_assignments app.
"""
import uuid

from django.test import TestCase

from ...subsidy_access_policy.tests.factories import AssignedLearnerCreditAccessPolicyFactory
from ..api import get_allocated_quantity_for_policy, get_assignments_for_policy
from ..constants import LearnerContentAssignmentStateChoices
from ..models import AssignmentPolicy
from .factories import LearnerContentAssignmentFactory

ACTIVE_ASSIGNED_LEARNER_CREDIT_POLICY_UUID = uuid.uuid4()


class TestContentAssignmentApi(TestCase):
    """
    Tests functions of the ``content_assignment.api`` module.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.active_policy = AssignedLearnerCreditAccessPolicyFactory(
            uuid=ACTIVE_ASSIGNED_LEARNER_CREDIT_POLICY_UUID,
            spend_limit=10000,
        )
        cls.assignment_policy = AssignmentPolicy.objects.create(
            subsidy_access_policy=cls.active_policy,
        )

    def test_get_assignments_for_policy(self):
        """
        Simple test to fetch assignment records related to a given policy.
        """
        expected_assignments = [
            LearnerContentAssignmentFactory.create(
                assignment_policy=self.assignment_policy,
            ) for _ in range(10)
        ]

        with self.assertNumQueries(1):
            actual_assignments = list(get_assignments_for_policy(self.active_policy))

        self.assertEqual(
            sorted(actual_assignments, key=lambda record: record.uuid),
            sorted(expected_assignments, key=lambda record: record.uuid),
        )

    def test_get_assignments_for_policy_different_states(self):
        """
        Simple test to fetch assignment records related to a given policy,
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
                LearnerContentAssignmentFactory.create(assignment_policy=self.assignment_policy, state=state)
            )

        for filter_state in (
            LearnerContentAssignmentStateChoices.CANCELLED,
            LearnerContentAssignmentStateChoices.ACCEPTED,
        ):
            with self.assertNumQueries(1):
                actual_assignments = list(get_assignments_for_policy(self.active_policy, filter_state))

            self.assertEqual(
                sorted(actual_assignments, key=lambda record: record.uuid),
                sorted(expected_assignments[filter_state], key=lambda record: record.uuid),
            )

    def test_get_allocated_quantity_for_policy(self):
        """
        Tests to verify that we can fetch the total allocated quantity across a set of assignments
        related to some policy.
        """
        for amount in (1000, 2000, 3000):
            LearnerContentAssignmentFactory.create(
                assignment_policy=self.assignment_policy,
                content_quantity=amount,
            )

        with self.assertNumQueries(1):
            actual_amount = get_allocated_quantity_for_policy(self.active_policy)
            self.assertEqual(actual_amount, 6000)
