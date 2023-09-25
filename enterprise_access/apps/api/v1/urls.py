""" API v1 URLs. """

from rest_framework.routers import DefaultRouter

from enterprise_access.apps.api.v1 import views

app_name = 'v1'
urlpatterns = []

router = DefaultRouter()

router.register("policy-redemption", views.SubsidyAccessPolicyRedeemViewset, 'policy-redemption')
router.register("policy-allocation", views.SubsidyAccessPolicyAllocateViewset, 'policy-allocation')
router.register("subsidy-access-policies", views.SubsidyAccessPolicyViewSet, 'subsidy-access-policies')
router.register("license-requests", views.LicenseRequestViewSet, 'license-requests')
router.register("coupon-code-requests", views.CouponCodeRequestViewSet, 'coupon-code-requests')
router.register("customer-configurations", views.SubsidyRequestCustomerConfigurationViewSet, 'customer-configurations')
router.register("assignment-configurations", views.AssignmentConfigurationViewSet, 'assignment-configurations')

urlpatterns += router.urls
