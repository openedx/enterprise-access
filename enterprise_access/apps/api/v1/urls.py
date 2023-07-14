""" API v1 URLs. """

from rest_framework.routers import DefaultRouter

from enterprise_access.apps.api.v1 import views

app_name = 'v1'
urlpatterns = []

router = DefaultRouter()

router.register("admin/policy", views.SubsidyAccessPolicyCRUDViewset, 'admin-policy')  # DEPRECATED viewset
router.register("policy", views.SubsidyAccessPolicyRedeemViewset, 'policy')  # DEPRECATED route
router.register("policy-redemption", views.SubsidyAccessPolicyRedeemViewset, 'policy-redemption')
router.register("subsidy-access-policies", views.SubsidyAccessPolicyViewSet, 'subsidy-access-policies')
router.register("license-requests", views.LicenseRequestViewSet, 'license-requests')
router.register("coupon-code-requests", views.CouponCodeRequestViewSet, 'coupon-code-requests')
router.register("customer-configurations", views.SubsidyRequestCustomerConfigurationViewSet, 'customer-configurations')

urlpatterns += router.urls
