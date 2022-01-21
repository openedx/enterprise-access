""" Admin configuration for subsidy_request models. """

from django.contrib import admin

from enterprise_access.apps.subsidy_request import models


class BaseSubsidyRequestAdmin:
    """ Base admin configuration for the subsidy request models. """

    list_display = (
        'uuid',
        'lms_user_id',
        'enterprise_customer_uuid',
        'course_id',
    )

    list_filter = (
        'enterprise_customer_uuid',
        'state',
    )

    read_only_fields = (
        'uuid',
        'denial_reason',
        'state',
        'reviewer_lms_user_id',
        'reviewed_at',
    )

    fields = (
        'lms_user_id',
        'course_id',
        'enterprise_customer_uuid',
        'denial_reason',
        'reviewer_lms_user_id',
        'reviewed_at',
        'state',
    )

@admin.register(models.LicenseRequest)
class LicenseRequestAdmin(BaseSubsidyRequestAdmin, admin.ModelAdmin):
    """ Admin configuration for the LicenseRequest model. """

    read_only_fields = (
        'subscription_plan_uuid',
        'license_uuid',
    )

    fields = (
        'subscription_plan_uuid',
        'license_uuid',
    )

    class Meta:
        """
        Meta class for ``LicenseRequestAdmin``.
        """

        model = models.LicenseRequest

    def get_readonly_fields(self, request, obj=None):
        return super().read_only_fields + self.read_only_fields

    def get_fields(self, request, obj=None):
        return super().fields + self.fields

@admin.register(models.CouponCodeRequest)
class CouponCodeRequestAdmin(BaseSubsidyRequestAdmin, admin.ModelAdmin):
    """ Admin configuration for the CouponCodeRequest model. """

    read_only_fields = (
        'coupon_id',
        'coupon_code',
    )

    fields = (
        'coupon_id',
        'coupon_code',
    )

    class Meta:
        """
        Meta class for ``CouponCodeRequestAdmin``.
        """

        model = models.CouponCodeRequest

    def get_readonly_fields(self, request, obj=None):
        return super().read_only_fields + self.read_only_fields

    def get_fields(self, request, obj=None):
        return super().fields + self.fields
