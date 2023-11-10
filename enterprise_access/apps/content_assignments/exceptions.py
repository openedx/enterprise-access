"""
Exceptions that can be raised by the ``content_assignments`` app.
"""


class ContentAssignmentsException(Exception):
    """
    Base exception class for the ``content_assignments`` app.
    """


class MissingContentAssignment(ContentAssignmentsException):
    """
    Raised in rare/impossible cases where an assignment couldn't be
    found while emailing an assignment notification.
    """


class MissingPolicy(ContentAssignmentsException):
    """
    Raised in rare/impossible cases where an associated policy couldn't be
    found while emailing an assignment notification.
    """
