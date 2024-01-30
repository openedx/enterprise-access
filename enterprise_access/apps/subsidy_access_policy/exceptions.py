"""
Exceptions that can be raised by the ``subsidy_access_policy`` app.
"""
import requests
from django.core.exceptions import ValidationError


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
        """
        Fetch the response object from the HTTPError that caused this exception.

        Returns:
            requests.models.Response or None.
        """
        return self.__cause__.response  # pylint: disable=no-member

    def error_payload(self):
        """
        Generate a useful error payload for logging purposes.
        """
        # requests.models.Response is falsey for HTTP status codes greater than or equal to 400!  We must explicitly
        # check if the response object is not None before giving up on it.
        if self.error_response is not None:
            error_payload = self.error_response.json()
            error_payload['subsidy_status_code'] = self.error_response.status_code
            return error_payload
        return {
            'detail': str(self),
        }


class MissingAssignment(SubsidyAccessPolicyException):
    """
    Raised in rare/impossible cases where attempts to redeem assigned content resulted in a race condition where an
    assignment couldn't be found.
    """


class PriceValidationError(ValidationError):
    """
    Raised in cases related to assignment allocation when the requested price
    fails our validation checks.
    """
    user_message = 'An error occurred while validating the provided price.'
