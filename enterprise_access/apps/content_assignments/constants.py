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
