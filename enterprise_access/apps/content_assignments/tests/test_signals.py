"""
Signals and handlers tests.
"""
from django.test import TestCase
from django.utils import timezone

from enterprise_access.apps.content_assignments.tests.factories import LearnerContentAssignmentFactory
from enterprise_access.apps.core.tests.factories import UserFactory

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
            LearnerContentAssignmentFactory(learner_email=TEST_EMAIL, lms_user_id=None),
            LearnerContentAssignmentFactory(learner_email=TEST_EMAIL, lms_user_id=None),
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
