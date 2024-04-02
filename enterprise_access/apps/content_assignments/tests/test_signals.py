"""
Signals and handlers tests.
"""
from unittest import mock
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone
from openedx_events.enterprise.data import SubsidyRedemption
from openedx_events.enterprise.signals import SUBSIDY_REDEMPTION_REVERSED

from enterprise_access.apps.content_assignments.constants import LearnerContentAssignmentStateChoices
from enterprise_access.apps.content_assignments.signals import update_assignment_status_for_reversed_transaction
from enterprise_access.apps.content_assignments.tests.factories import (
    AssignmentConfigurationFactory,
    LearnerContentAssignmentFactory
)
from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.subsidy_access_policy.tests.factories import AssignedLearnerCreditAccessPolicyFactory

TEST_EMAIL = 'test@example.com'


class SignalsTests(TestCase):
    """
    Tests for signals and handlers.
    """
    def setUp(self):
        super().setUp()
        self.subsidy_redemption = SubsidyRedemption(
            subsidy_identifier='31164bce-e95c-49ef-b578-b1236d3e6e50',
            content_key='test-course-1',
            lms_user_id='1543675'
        )
        self.user = UserFactory(lms_user_id=self.subsidy_redemption.lms_user_id)
        self.enterprise_uuid = uuid4()
        self.assignment_configuration = AssignmentConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
        )
        self.assigned_learner_credit_policy = AssignedLearnerCreditAccessPolicyFactory(
            uuid=self.subsidy_redemption.subsidy_identifier,
            display_name='An assigned learner credit policy, for the test customer.',
            enterprise_customer_uuid=self.enterprise_uuid,
            active=True,
            assignment_configuration=self.assignment_configuration,
            spend_limit=1000000,
        )
        self.content_title = 'edx: Demo 101'
        self.assigned_price_cents = 25000
        self.assignment = LearnerContentAssignmentFactory.create(
            assignment_configuration=self.assignment_configuration,
            learner_email='alice@foo.com',
            lms_user_id=self.user.lms_user_id,
            content_key=self.subsidy_redemption.content_key,
            content_title=self.content_title,
            content_quantity=-self.assigned_price_cents,
            state=LearnerContentAssignmentStateChoices.ALLOCATED,
        )
        self.receiver_called = False

    def test_update_assignment_lms_user_id_from_user_email_registration(self):
        """
        Test that `update_assignment_lms_user_id_from_user_email()` correctly updates the `lms_user_id` field on any
        assignments for new learners registering.
        """
        # Simulate creating asignments for the test learner BEFORE learner registration.
        assignments_pre_register = [
            LearnerContentAssignmentFactory(learner_email=TEST_EMAIL, lms_user_id=None),
            LearnerContentAssignmentFactory(learner_email=TEST_EMAIL, lms_user_id=None),
        ]
        # Simulate registration by creating a user for test learner.
        test_user = UserFactory(email=TEST_EMAIL)
        # User creation *should* trigger the post_save hook to update lms_user_id for assignments_pre_create.
        for assignment in assignments_pre_register:
            assignment.refresh_from_db()
            assert assignment.lms_user_id == test_user.lms_user_id

    def test_update_assignment_lms_user_id_from_user_email_login(self):
        """
        Test that `update_assignment_lms_user_id_from_user_email()` correctly updates the `lms_user_id` field on any
        assignments for existing learners logging in.
        """
        # Simulate registration by creating a user for test learner.
        test_user = UserFactory(email=TEST_EMAIL)
        # Simulate creating asignments for the test learner AFTER user creation.
        assignments_post_register = [
            LearnerContentAssignmentFactory(learner_email=TEST_EMAIL.upper(), lms_user_id=None),
            LearnerContentAssignmentFactory(learner_email=TEST_EMAIL.upper(), lms_user_id=None),
        ]
        # Simulate the learner logging in.
        test_user.last_login = timezone.now()
        test_user.save()
        # User login *should* trigger the post_save hook to update lms_user_id for assignments_post_create.
        for assignment in assignments_post_register:
            assignment.refresh_from_db()
            assert assignment.lms_user_id == test_user.lms_user_id

    def test_update_assignment_lms_user_id_from_user_email_other_assignments(self):
        """
        Test that `update_assignment_lms_user_id_from_user_email()` DOES NOT update any assignments for learners that
        aren't registering/logging in during the test.
        """
        # Assignments for a different learner should never have their lms_user_id set during the course of this test.
        assignments_other_learner = [
            LearnerContentAssignmentFactory(learner_email='other@example.com', lms_user_id=None),
            LearnerContentAssignmentFactory(learner_email='other@example.com', lms_user_id=None),
        ]
        # Simulate registration by creating a user for test learner.
        test_user = UserFactory(email=TEST_EMAIL)
        # Simulate the learner logging in.
        test_user.last_login = timezone.now()
        test_user.save()
        # Assignments for other learners *should not* have their lms_user_id updated.
        for assignment in assignments_other_learner:
            assignment.refresh_from_db()
            assert assignment.lms_user_id is None

    def _event_receiver_side_effect(self, **kwargs):
        """
        Used show that the Open edX Event was called by the Django signal handler.
        """
        self.receiver_called = True

    def test_send_subsidy_reversal_event(self):
        """
        Test the send and receive for the subsidy reversal event.
        Expected result:
            - SUBSIDY_REDEMPTION_REVERSED is sent and received by the mocked receiver.
            - The arguments that the receiver gets are the arguments sent by the event
            except the metadata generated on the fly.
        """
        event_receiver = mock.Mock(side_effect=self._event_receiver_side_effect)
        SUBSIDY_REDEMPTION_REVERSED.connect(event_receiver)
        SUBSIDY_REDEMPTION_REVERSED.send_event(
            redemption=self.subsidy_redemption,
        )
        self.assertTrue(self.receiver_called)
        self.assertEqual(
            event_receiver.call_args.kwargs['redemption'].subsidy_identifier,
            self.subsidy_redemption.subsidy_identifier
        )
        self.assertEqual(
            event_receiver.call_args.kwargs['redemption'].content_key,
            self.subsidy_redemption.content_key
        )
        self.assertEqual(
            event_receiver.call_args.kwargs['redemption'].lms_user_id,
            self.subsidy_redemption.lms_user_id
        )

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.assignments_api')
    def test_subsidy_reversal_event_processing(self, mock_assignments_api):
        """
        Test the receive and correct processing for the subsidy reversal event.
        Expected result:
            - The receiver processes the event correctly and updates the assignment status.
        """
        mock_assignments_api.get_assignment_for_learner.return_value = self.assignment
        SUBSIDY_REDEMPTION_REVERSED.connect(update_assignment_status_for_reversed_transaction)
        SUBSIDY_REDEMPTION_REVERSED.send_event(redemption=self.subsidy_redemption)
        self.assertEqual(self.assignment.state, LearnerContentAssignmentStateChoices.REVERSED)
