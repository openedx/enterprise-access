"""
Constants module for the BFFs.
"""


class COURSE_ENROLLMENT_STATUSES:
    """
    Course enrollment statuses.
    """
    IN_PROGRESS = 'in_progress'
    UPCOMING = 'upcoming'
    COMPLETED = 'completed'
    SAVED_FOR_LATER = 'saved_for_later'
    # Not a realized enrollment status, but used for the purpose of requesting enrollment
    REQUESTED = 'requested'
    # Not a realized enrollment status, but used for the purpose of assigned enrollment
    ASSIGNED = 'assigned'


UNENROLLABLE_COURSE_STATUSES = {
    COURSE_ENROLLMENT_STATUSES.IN_PROGRESS,
    COURSE_ENROLLMENT_STATUSES.UPCOMING,
    COURSE_ENROLLMENT_STATUSES.COMPLETED,
    COURSE_ENROLLMENT_STATUSES.SAVED_FOR_LATER,
}
