"""
Exceptions that can be raised by the ``subsidy_access_policy`` app.
"""
import requests


class SubsidyAccessPolicyException(Exception):
    """
    Base exception class for the ``subsidy_access_policy`` app.
    """


class UnredeemableContentException(SubsidyAccessPolicyException):
    """
    Raised from any exceptional state that causes content to not be redeemable.
    """


class ContentPriceNullException(UnredeemableContentException):
    """
    Raised whenever the fetched price for some content is null.
    """


class SubsidyAccessPolicyLockAttemptFailed(SubsidyAccessPolicyException):
    """
    Raised when an attempt to lock SubsidyAccessPolicy failed due to an
    already existing lock acquired on the same resource.
    """


class SubsidyAPIHTTPError(requests.exceptions.HTTPError):
    """
    Exception that distinguishes HTTPErrors that arise from
    calls to the enterprise-subsidy API.

    You should expect to use this as ``raise SubsidyAPIHTTPError() from {some HTTPError instance}``,
    so that the cause (i.e. the original HTTPError) can be found in this Exception class'
    ``__cause__`` attribute, which will allow us to access the original error response object,
    including the payload and status code.
    """
    @property
    def error_response(self):
        """ Fetch the response object from the HTTPError that caused this exception. """
        return self.__cause__.response  # pylint: disable=no-member

    def error_payload(self):
        if self.error_response:
            return self.error_response.json()
        return {
            'detail': str(self),
        }
