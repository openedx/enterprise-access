"""
Top-level views module for convenience of maintaining
existing imports of browse and request AND access policy views.
"""
from .browse_and_request import (
    CouponCodeRequestViewSet,
    LicenseRequestViewSet,
    SubsidyRequestCustomerConfigurationViewSet,
    SubsidyRequestViewSet
)
from .content_assignments.assignment_configuration import AssignmentConfigurationViewSet
from .content_assignments.assignments import LearnerContentAssignmentViewSet
from .content_assignments.assignments_admin import LearnerContentAssignmentAdminViewSet
from .subsidy_access_policy import (
    SubsidyAccessPolicyAllocateViewset,
    SubsidyAccessPolicyGroupViewset,
    SubsidyAccessPolicyRedeemViewset,
    SubsidyAccessPolicyViewSet
)
from .bffs import LearnerPortalBFFViewSet
