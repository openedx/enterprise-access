"""
Top-level views module for convenience of maintaining
existing imports of browse and request AND access policy views.
"""
from .admin_portal_learner_profile import AdminLearnerProfileViewSet
from .bffs.checkout import CheckoutBFFViewSet
from .bffs.common import PingViewSet
from .bffs.learner_portal import LearnerPortalBFFViewSet
from .browse_and_request import (
    CouponCodeRequestViewSet,
    LearnerCreditRequestViewSet,
    LicenseRequestViewSet,
    SubsidyRequestCustomerConfigurationViewSet,
    SubsidyRequestViewSet
)
from .content_assignments.assignment_configuration import AssignmentConfigurationViewSet
from .content_assignments.assignments import LearnerContentAssignmentViewSet
from .content_assignments.assignments_admin import LearnerContentAssignmentAdminViewSet
from .customer_billing import CheckoutIntentViewSet, CustomerBillingViewSet
from .provisioning import ProvisioningCreateView
from .subsidy_access_policy import (
    SubsidyAccessPolicyAllocateViewset,
    SubsidyAccessPolicyGroupViewset,
    SubsidyAccessPolicyRedeemViewset,
    SubsidyAccessPolicyViewSet
)
