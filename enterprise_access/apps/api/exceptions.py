"""
Custom Exception classes for Enterprise Access API v1.
"""


class SubsidyRequestError(Exception):
    """
    A general exception dealing with subsidy requests.
    """

    def __init__(self, message, http_status_code=None):
        super().__init__(message)
        self.message = message
        self.http_status_code = http_status_code


class SubsidyRequestCreationError(SubsidyRequestError):
    """
    An exception indicating that a subsidy request cannot be created.
    """
