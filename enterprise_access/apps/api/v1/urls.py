""" API v1 URLs. """

from django.urls import re_path

from rest_framework.routers import DefaultRouter

from enterprise_access.apps.api.v1 import views

app_name = 'v1'
urlpatterns = [
    re_path(
        r'^policy/redeem$', views.AccessPolicyRedeemAPIView.as_view(),
        name='policy-redeem'
    ),
]

router = DefaultRouter()

router.register("license-requests", views.LicenseRequestViewSet, 'license-requests')
router.register("coupon-code-requests", views.CouponCodeRequestViewSet, 'coupon-code-requests')
router.register("customer-configurations", views.SubsidyRequestCustomerConfigurationViewSet, 'customer-configurations')

urlpatterns += router.urls
