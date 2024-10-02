""" API v1 URLs. """

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from viewflow.workflow.flow import FlowViewset

from enterprise_access.apps.workflows import flows

from enterprise_access.apps.api.v1 import views

app_name = 'v1'

router = DefaultRouter()

router.register("policy-redemption", views.SubsidyAccessPolicyRedeemViewset, 'policy-redemption')
router.register("policy-allocation", views.SubsidyAccessPolicyAllocateViewset, 'policy-allocation')
router.register("subsidy-access-policies", views.SubsidyAccessPolicyViewSet, 'subsidy-access-policies')
router.register("license-requests", views.LicenseRequestViewSet, 'license-requests')
router.register("coupon-code-requests", views.CouponCodeRequestViewSet, 'coupon-code-requests')
router.register("customer-configurations", views.SubsidyRequestCustomerConfigurationViewSet, 'customer-configurations')
router.register("assignment-configurations", views.AssignmentConfigurationViewSet, 'assignment-configurations')
router.register(
    r'assignment-configurations/(?P<assignment_configuration_uuid>[\S]+)/admin/assignments',
    views.LearnerContentAssignmentAdminViewSet,
    'admin-assignments',
)
router.register(
    r'assignment-configurations/(?P<assignment_configuration_uuid>[\S]+)/assignments',
    views.LearnerContentAssignmentViewSet,
    'assignments',
)

urlpatterns = [
    path(
        'subsidy-access-policies/<uuid>/group-members',
        views.SubsidyAccessPolicyGroupViewset.as_view({'get': 'get_group_member_data_with_aggregates'}),
        name='aggregated-subsidy-enrollments'
    ),
]

urlpatterns += router.urls
