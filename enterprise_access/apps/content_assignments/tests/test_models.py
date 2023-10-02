"""
Tests for the ``api.py`` module of the content_assignments app.
"""
from django.test import TestCase
from django.utils import timezone

from ..constants import AssignmentActions
from ..models import AssignmentConfiguration
from .factories import LearnerContentAssignmentFactory


class TestAssignmentActions(TestCase):
    """
    Test functions around LearnerContentAssignmentActions.
    """

    @classmethod
    def setUpClass(cls):
        """
        Set up a single assignment record.
        """
        super().setUpClass()
        cls.assignment_configuration = AssignmentConfiguration.objects.create()
        cls.assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=cls.assignment_configuration,
        )

    def tearDown(self):
        """
        Clear all actions after each test function.
        """
        super().tearDown()
        self.assignment.actions.all().delete()

    def test_get_set_linked_action(self):
        """
        Tests that we can idempotently get/set the linked action for an assignment.
        """
        # Start with no linked actions
        self.assertIsNone(self.assignment.get_successful_linked_action())

        # now create one
        linked_action, was_created = self.assignment.add_successful_linked_action()

        self.assertTrue(was_created)
        self.assertEqual(linked_action.action_type, AssignmentActions.LEARNER_LINKED)
        self.assertIsNone(linked_action.error_reason)
        self.assertAlmostEqual(
            timezone.now(),
            linked_action.completed_at,
            delta=timezone.timedelta(seconds=2),
        )

        # now if we fetch the linked action for this assignment, we'll
        # get the thing we just created
        self.assertEqual(
            self.assignment.get_successful_linked_action(),
            linked_action,
        )

        # ...and adding a linked action through this method is idempotent
        linked_action_again, was_created_again = self.assignment.add_successful_linked_action()
        self.assertEqual(linked_action_again, linked_action)
        self.assertFalse(was_created_again)

    def test_get_set_notified_action(self):
        """
        Tests that we can idempotently get/set the notified action for an assignment.
        """
        # Start with no notified actions
        self.assertIsNone(self.assignment.get_successful_notified_action())

        # now create one
        notified_action, was_created = self.assignment.add_successful_notified_action()

        self.assertTrue(was_created)
        self.assertEqual(notified_action.action_type, AssignmentActions.NOTIFIED)
        self.assertIsNone(notified_action.error_reason)
        self.assertAlmostEqual(
            timezone.now(),
            notified_action.completed_at,
            delta=timezone.timedelta(seconds=2),
        )

        # now if we fetch the notified action for this assignment, we'll
        # get the thing we just created
        self.assertEqual(
            self.assignment.get_successful_notified_action(),
            notified_action,
        )

        # ...and adding a notified action through this method is idempotent
        notified_action_again, was_created_again = self.assignment.add_successful_notified_action()
        self.assertEqual(notified_action_again, notified_action)
        self.assertFalse(was_created_again)

    def test_get_set_reminded_actions(self):
        """
        Tests that we can idempotently get/set the reminded action for an assignment.
        """
        # Start with no reminded actions
        self.assertIsNone(self.assignment.get_last_successful_reminded_action())

        # now create one
        reminded_action = self.assignment.add_successful_reminded_action()

        self.assertEqual(reminded_action.action_type, AssignmentActions.REMINDED)
        self.assertIsNone(reminded_action.error_reason)
        self.assertAlmostEqual(
            timezone.now(),
            reminded_action.completed_at,
            delta=timezone.timedelta(seconds=2),
        )

        # now if we fetch the reminded action for this assignment, we'll
        # get the thing we just created
        self.assertEqual(
            self.assignment.get_last_successful_reminded_action(),
            reminded_action,
        )

        # we can have multiple, successful reminded actions for our assignment
        reminded_action_again = self.assignment.add_successful_reminded_action()
        self.assertNotEqual(reminded_action_again.uuid, reminded_action.uuid)
        self.assertIsNone(reminded_action_again.error_reason)
        self.assertAlmostEqual(
            timezone.now(),
            reminded_action_again.completed_at,
            delta=timezone.timedelta(seconds=2),
        )

        # now `reminded_action_again` is the most recent reminded action
        self.assertEqual(
            self.assignment.get_last_successful_reminded_action(),
            reminded_action_again,
        )
