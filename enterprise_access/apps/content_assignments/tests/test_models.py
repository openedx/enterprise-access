"""
Tests for the ``api.py`` module of the content_assignments app.
"""
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy
from enterprise_access.apps.subsidy_access_policy.tests.factories import AssignedLearnerCreditAccessPolicyFactory

from ..constants import NUM_DAYS_BEFORE_AUTO_EXPIRATION, RETIRED_EMAIL_ADDRESS, AssignmentActions
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
        cls.subsidy_access_policy = AssignedLearnerCreditAccessPolicyFactory.create(
            assignment_configuration=cls.assignment_configuration,
        )
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
        self.assertIsNone(self.assignment.get_last_successful_linked_action())

        # now create one
        linked_action = self.assignment.add_successful_linked_action()

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
            self.assignment.get_last_successful_linked_action(),
            linked_action,
        )

        # ...and adding a linked action through this method will create a new action record
        linked_action_again = self.assignment.add_successful_linked_action()
        self.assertNotEqual(linked_action_again, linked_action)
        self.assertEqual(
            self.assignment.get_last_successful_linked_action(),
            linked_action_again,
        )

    def test_get_set_notified_action(self):
        """
        Tests that we can idempotently get/set the notified action for an assignment.
        """
        # Start with no notified actions
        self.assertIsNone(self.assignment.get_last_successful_notified_action())

        # now create one
        notified_action = self.assignment.add_successful_notified_action()

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
            self.assignment.get_last_successful_notified_action(),
            notified_action,
        )

        # ...and adding a notified action through this method creates a new action record
        notified_action_again = self.assignment.add_successful_notified_action()
        self.assertNotEqual(notified_action_again, notified_action)
        self.assertEqual(
            self.assignment.get_last_successful_notified_action(),
            notified_action_again,
        )

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

    @mock.patch.object(SubsidyAccessPolicy, 'subsidy_record', autospec=True)
    def test_get_automatic_expiration_date_and_reason_no_action(self, mock_subsidy_record):
        """
        Test that this method returns a date 90 days from the assignment
        creation time if there is no last successful
        notified action.
        """
        mock_subsidy_record.return_value = {
            'uuid': 'test-uuid',
            'title': 'Test Subsidy',
            'enterprise_customer_uuid': 'test-enterprise-customer-uuid',
            'expiration_datetime': '2030-01-01 12:00:00Z',
            'active_datetime': '2020-01-01 12:00:00Z',
            'current_balance': '5000',
            'is_active': True,
        }

        result = self.assignment.get_automatic_expiration_date_and_reason()
        self.assertEqual(
            result['date'],
            self.assignment.created + timezone.timedelta(days=NUM_DAYS_BEFORE_AUTO_EXPIRATION),
        )
        self.assignment.add_errored_notified_action(Exception('foo'))

        result = self.assignment.get_automatic_expiration_date_and_reason()
        self.assertEqual(
            result['date'],
            self.assignment.created + timezone.timedelta(days=NUM_DAYS_BEFORE_AUTO_EXPIRATION),
        )

    @mock.patch('enterprise_access.apps.content_metadata.api.EnterpriseCatalogApiClient')
    @mock.patch.object(SubsidyAccessPolicy, 'subsidy_record', autospec=True)
    def test_get_automatic_expiration_date_and_reason_date(self, mock_subsidy_record, mock_catalog_client):
        """
        Test that this method returns None if there is no last successful
        notified action.
        """
        mock_catalog_client.return_value.catalog_content_metadata.return_value = {
            'count': 1,
            'results': [{
                'key': self.assignment.content_key,
            }],
        }
        mock_subsidy_record.return_value = {
            'uuid': 'test-uuid',
            'title': 'Test Subsidy',
            'enterprise_customer_uuid': 'test-enterprise-customer-uuid',
            'expiration_datetime': '2030-01-01 12:00:00Z',
            'active_datetime': '2020-01-01 12:00:00Z',
            'current_balance': '5000',
            'is_active': True,
        }
        first_notification = self.assignment.add_successful_notified_action()
        result = self.assignment.get_automatic_expiration_date_and_reason()
        self.assertEqual(
            result['date'],
            first_notification.completed_at + timezone.timedelta(days=NUM_DAYS_BEFORE_AUTO_EXPIRATION),
        )

        second_notification = self.assignment.add_successful_notified_action()
        result = self.assignment.get_automatic_expiration_date_and_reason()
        self.assertEqual(
            result['date'],
            second_notification.completed_at + timezone.timedelta(days=NUM_DAYS_BEFORE_AUTO_EXPIRATION),
        )

    def test_clear_pii(self):
        """
        Tests that we can clear pii on an assignment.
        """
        self.assignment.learner_email = 'foo@bar.com'
        self.assignment.lms_user_id = 12345
        self.assignment.save()

        self.assignment.clear_pii()
        self.assignment.save()
        self.assignment.clear_historical_pii()

        self.assignment.refresh_from_db()

        self.assertEqual(12345, self.assignment.lms_user_id)
        self.assertEqual(self.assignment.learner_email, RETIRED_EMAIL_ADDRESS)

        for historical_record in self.assignment.history.all():
            self.assertEqual(historical_record.learner_email, RETIRED_EMAIL_ADDRESS)
