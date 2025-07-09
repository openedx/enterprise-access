"""
Signals and handlers tests.
"""
from unittest import mock
from uuid import uuid4

from django.db import DatabaseError
from django.test import TestCase
from django.utils import timezone

from enterprise_access.apps.content_assignments.constants import LearnerContentAssignmentStateChoices
from enterprise_access.apps.content_assignments.models import LearnerContentAssignment
from enterprise_access.apps.content_assignments.signals import update_assignment_status_for_reversed_transaction
from enterprise_access.apps.content_assignments.tests.factories import LearnerContentAssignmentFactory
from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.subsidy_request.models import (
    LearnerCreditRequestActionErrorReasons,
    LearnerCreditRequestActions,
    SubsidyRequestStates
)
from enterprise_access.apps.subsidy_request.tests.factories import LearnerCreditRequestFactory

TEST_EMAIL = 'test@example.com'


class SignalsTests(TestCase):
    """
    Tests for signals and handlers.
    """

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


class TestReversalSignal(TestCase):
    """
    Tests for reversal signals.
    """

    def setUp(self):
        self.user = UserFactory()
        self.assignment = LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
            lms_user_id=self.user.lms_user_id,
            transaction_uuid=uuid4()
        )
        self.lcr = LearnerCreditRequestFactory(
            user=self.user,
            assignment=self.assignment,
            state=SubsidyRequestStates.ACCEPTED,
        )
        self.assignment.refresh_from_db()

    def test_assignment_and_lcr_reversal_happy_path(self):
        update_assignment_status_for_reversed_transaction(
            ledger_transaction=mock.Mock(uuid=self.assignment.transaction_uuid)
        )
        self.assignment.refresh_from_db()
        self.lcr.refresh_from_db()
        actions = LearnerCreditRequestActions.objects.filter(learner_credit_request=self.lcr)
        assert actions.exists(), "No LearnerCreditRequestActions created"
        action = actions.latest('created')

        assert self.assignment.state == LearnerContentAssignmentStateChoices.REVERSED
        assert self.lcr.state == SubsidyRequestStates.REVERSED
        assert action.recent_action == SubsidyRequestStates.REVERSED
        assert action.status == SubsidyRequestStates.REVERSED
        assert not action.error_reason

    def test_assignment_reversal_no_lcr(self):
        assignment = LearnerContentAssignmentFactory(
            state=LearnerContentAssignmentStateChoices.ACCEPTED,
            lms_user_id=self.user.lms_user_id,
            transaction_uuid=uuid4()
        )
        update_assignment_status_for_reversed_transaction(
            ledger_transaction=mock.Mock(uuid=assignment.transaction_uuid)
        )
        assignment.refresh_from_db()
        assert assignment.state == LearnerContentAssignmentStateChoices.REVERSED
        assert not LearnerCreditRequestActions.objects.filter(learner_credit_request__assignment=assignment).exists()

    def test_reversal_error_updates_action(self):
        with mock.patch.object(LearnerContentAssignment, "save", side_effect=DatabaseError("Simulated DB error")):
            update_assignment_status_for_reversed_transaction(
                ledger_transaction=mock.Mock(uuid=self.assignment.transaction_uuid)
            )
        actions = LearnerCreditRequestActions.objects.filter(learner_credit_request=self.lcr)
        assert actions.exists(), "No LearnerCreditRequestActions created"
        action = actions.latest('created')
        assert action.error_reason == LearnerCreditRequestActionErrorReasons.FAILED_REVERSAL
        assert action.status == SubsidyRequestStates.ACCEPTED
        assert "Simulated DB error" in action.traceback
