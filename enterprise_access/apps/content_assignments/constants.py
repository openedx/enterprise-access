"""
Constants for the content_assignments app.
"""


class LearnerContentAssignmentStateChoices:
    """
    LearnerContentAssignment states.
    """
    ALLOCATED = 'allocated'
    ACCEPTED = 'accepted'
    CANCELLED = 'cancelled'
    ERRORED = 'errored'

    CHOICES = (
        (ALLOCATED, 'Allocated'),
        (ACCEPTED, 'Accepted'),
        (CANCELLED, 'Cancelled'),
        (ERRORED, 'Errored'),
    )

    # States which allow reallocation by an admin.
    REALLOCATE_STATES = (CANCELLED, ERRORED)

    # States which allow cancellation by an admin.
    CANCELABLE_STATES = (ALLOCATED, ERRORED)

    # States which allow reminders by an admin.
    REMINDABLE_STATES = (ALLOCATED,)


class AssignmentActions:
    """
    Actions allowed on a given LearnerContentAssignment.
    """
    LEARNER_LINKED = 'learner_linked'
    NOTIFIED = 'notified'
    REMINDED = 'reminded'
    CANCELLED_NOTIFICATION = 'cancelled'
    AUTOMATIC_CANCELLATION_NOTIFICATION = 'automatic_cancellation'

    CHOICES = (
        (LEARNER_LINKED, 'Learner linked to customer'),
        (NOTIFIED, 'Learner notified of assignment'),
        (REMINDED, 'Learner reminded about assignment'),
        (CANCELLED_NOTIFICATION, 'Learner assignment cancelled'),
        (AUTOMATIC_CANCELLATION_NOTIFICATION, 'Learner assignment cancelled automatically'),
    )


class AssignmentActionErrors:
    """
    Error reasons (like an error code) for errors encountered
    during an assignment action.
    """
    EMAIL_ERROR = 'email_error'
    INTERNAL_API_ERROR = 'internal_api_error'

    CHOICES = (
        (EMAIL_ERROR, 'Email error'),
        (INTERNAL_API_ERROR, 'Internal API error'),
    )


class AssignmentRecentActionTypes:
    """
    Types for dynamic field: assignment.recent_action.
    """
    ASSIGNED = 'assigned'
    REMINDED = 'reminded'
    CHOICES = (
        (ASSIGNED, 'Learner assigned content.'),
        (REMINDED, 'Learner sent reminder message.'),
    )


class AssignmentLearnerStates:
    """
    States for dynamic field: assignment.learner_state.
    """
    NOTIFYING = 'notifying'
    WAITING = 'waiting'
    FAILED = 'failed'
    CHOICES = (
        (NOTIFYING, 'Sending assignment notification message to learner.'),
        (WAITING, 'Waiting on learner to accept assignment.'),
        (FAILED, 'Assignment unexpectedly failed creation or acceptance.'),
    )
    SORT_ORDER = (
        NOTIFYING,
        WAITING,
        FAILED,
    )


class AssignmentAutomaticExpiredReason:
    """
    Reason for assignment automatic expiry.
    """
    NIENTY_DAYS_PASSED = 'NIENTY_DAYS_PASSED'
    ENROLLMENT_DATE_PASSED = 'ENROLLMENT_DATE_PASSED'
    SUBSIDY_EXPIRED = 'SUBSIDY_EXPIRED'


NUM_DAYS_BEFORE_AUTO_CANCELLATION = 90
