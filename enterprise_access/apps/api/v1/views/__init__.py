"""
Top-level views module for convenience of maintaining
existing imports of browse and request AND access policy views.
"""
from .assignment_configuration import AssignmentConfigurationViewSet
from .browse_and_request import (
    CouponCodeRequestViewSet,
    LicenseRequestViewSet,
    SubsidyRequestCustomerConfigurationViewSet,
    SubsidyRequestViewSet
)
from .subsidy_access_policy import SubsidyAccessPolicyRedeemViewset, SubsidyAccessPolicyViewSet
