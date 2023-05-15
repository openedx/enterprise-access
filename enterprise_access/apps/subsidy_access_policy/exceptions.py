"""
Exceptions that can be raised by the ``subsidy_access_policy`` app.
"""


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
