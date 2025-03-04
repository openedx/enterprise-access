"""
Custom Exception classes for Enterprise BFFs.
"""


class EnterpriseCustomerNotFoundError(Exception):
    """Raised when the enterprise customer data is missing."""


class LearnerPortalNotEnabledError(Exception):
    """Raised when the learner portal is not enabled for the enterprise customer."""
