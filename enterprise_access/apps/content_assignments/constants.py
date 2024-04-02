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
    EXPIRED = 'expired'
    REVERSED = 'reversed'

    CHOICES = (
        (ALLOCATED, 'Allocated'),
        (ACCEPTED, 'Accepted'),
        (CANCELLED, 'Cancelled'),
        (ERRORED, 'Errored'),
        (EXPIRED, 'Expired'),
    )

    # States which allow reallocation by an admin.
    REALLOCATE_STATES = (CANCELLED, ERRORED, EXPIRED, REVERSED)

    # States which allow cancellation by an admin.
    CANCELABLE_STATES = (ALLOCATED, ERRORED)

    # States which allow reminders by an admin.
    REMINDABLE_STATES = (ALLOCATED,)

    # States from which an assignment can be expired
    EXPIRABLE_STATES = (ALLOCATED,)

    # States from which an assignment can be reversed
    REVERSIBLE_STATES = (ALLOCATED,)


class AssignmentActions:
    """
    Actions allowed on a given LearnerContentAssignment.
    """
    LEARNER_LINKED = 'learner_linked'
    NOTIFIED = 'notified'
    REMINDED = 'reminded'
    REDEEMED = 'redeemed'
    CANCELLED = 'cancelled'
    CANCELLED_ACKNOWLEDGED = 'cancelled_acknowledged'
    EXPIRED = 'expired'
    EXPIRED_ACKNOWLEDGED = 'expired_acknowledged'

    CHOICES = (
        (LEARNER_LINKED, 'Learner linked to customer'),
        (NOTIFIED, 'Learner notified of assignment'),
        (REMINDED, 'Learner reminded about assignment'),
        (REDEEMED, 'Learner redeemed the assigned content'),
        (CANCELLED, 'Learner assignment cancelled'),
        (CANCELLED_ACKNOWLEDGED, 'Learner assignment cancellation acknowledged by learner'),
        (EXPIRED, 'Learner assignment expired'),
        (EXPIRED_ACKNOWLEDGED, 'Learner assignment expiration acknowledged by learner'),
    )


class AssignmentActionErrors:
    """
    Error reasons (like an error code) for errors encountered
    during an assignment action.
    """
    EMAIL_ERROR = 'email_error'
    INTERNAL_API_ERROR = 'internal_api_error'
    ENROLLMENT_ERROR = 'enrollment_error'

    CHOICES = (
        (EMAIL_ERROR, 'Email error'),
        (INTERNAL_API_ERROR, 'Internal API error'),
        (ENROLLMENT_ERROR, 'Enrollment error'),
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
    EXPIRED = 'expired'
    CHOICES = (
        (NOTIFYING, 'Sending assignment notification message to learner.'),
        (WAITING, 'Waiting on learner to accept assignment.'),
        (FAILED, 'Assignment unexpectedly failed creation or acceptance.'),
        (EXPIRED, 'Assignment expired due to 90-day timeout, subsidy expiration, or content enrollment deadline.'),
    )
    SORT_ORDER = (
        NOTIFYING,
        WAITING,
        EXPIRED,
        FAILED,
    )


class AssignmentAutomaticExpiredReason:
    """
    Reason for assignment automatic expiry.
    """
    NINETY_DAYS_PASSED = 'NINETY_DAYS_PASSED'
    ENROLLMENT_DATE_PASSED = 'ENROLLMENT_DATE_PASSED'
    SUBSIDY_EXPIRED = 'SUBSIDY_EXPIRED'


NUM_DAYS_BEFORE_AUTO_EXPIRATION = 90

RETIRED_EMAIL_ADDRESS = 'retired_user@retired.invalid'
