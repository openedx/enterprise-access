""" Admin configuration for subsidy_request models. """

import logging

from django.contrib import admin
from djangoql.admin import DjangoQLSearchMixin

from enterprise_access.apps.subsidy_request import models
from enterprise_access.apps.subsidy_request.utils import get_data_from_jwt_payload, get_user_from_request_session

logger = logging.getLogger(__name__)


class BaseSubsidyRequestAdmin(DjangoQLSearchMixin):
    """ Base admin configuration for the subsidy request models. """

    list_display = (
        'uuid',
        'user',
        'enterprise_customer_uuid',
        'course_id',
        'state',
    )

    list_filter = (
        'enterprise_customer_uuid',
        'state',
    )

    read_only_fields = (
        'uuid',
        'decline_reason',
        'course_title',
        'get_course_partners',
        'state',
        'reviewer',
        'reviewed_at',
    )

    fields = (
        'uuid',
        'user',
        'course_id',
        'course_title',
        'get_course_partners',
        'enterprise_customer_uuid',
        'decline_reason',
        'reviewer',
        'reviewed_at',
        'state',
    )

    @admin.display(
        description='Course partners'
    )
    def get_course_partners(self, obj):
        """
        Formats JSON list of course partners as human-readable partner names.
        """
        if not obj.course_partners:
            return '-'
        partner_names = [partner['name'] for partner in obj.course_partners]
        return ', '.join(partner_names)


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


@admin.register(models.SubsidyRequestCustomerConfiguration)
class SubsidyRequestCustomerConfigurationAdmin(DjangoQLSearchMixin, admin.ModelAdmin):
    """ Admin configuration for the SubsidyRequestCustomerConfiguration model. """
    writable_fields = [
        'subsidy_requests_enabled',
        'subsidy_type',
    ]
    exclude = ['changed_by']

    def get_readonly_fields(self, request, obj=None):
        """
        Override to only display some fields on creation of object in admin, as well
        as limit what is editable after creation.
        """
        if obj:
            return [
                'enterprise_customer_uuid',
                'last_changed_by',
            ]
        else:
            return []

    def last_changed_by(self, obj):
        return 'LMS User: {} ({})'.format(
            obj.changed_by.lms_user_id,
            obj.changed_by.email,
        )

    def save_model(self, request, obj, form, change):
        """
        Override save_model method to keep our change records up to date.
        """
        current_user = get_user_from_request_session(request)
        jwt_data = get_data_from_jwt_payload(request, ['user_id'])
        # Make sure we update the user object's lms_user_id if it's not set
        # to keep our DB up to date, because we have
        # no way to predict if the user has hit a rest endpoint and had
        # their info prepopulated already.
        lms_user_id = jwt_data['user_id']
        if not current_user.lms_user_id:
            current_user.lms_user_id = lms_user_id
            current_user.save()

        obj.changed_by = current_user

        super().save_model(request, obj, form, change)


@admin.register(models.LearnerCreditRequest)
class LearnerCreditRequestAdmin(BaseSubsidyRequestAdmin, admin.ModelAdmin):
    """ Admin configuration for the LearnerCreditRequest model. """

    list_display = (
        'uuid',
        'user',
        'enterprise_customer_uuid',
        'course_id',
        'state',
        'assignment',
    )

    read_only_fields = (
        'assignment',
    )

    fields = (
        'assignment',
        'learner_credit_request_config',
    )

    class Meta:
        """
        Meta class for ``LearnerCreditRequestAdmin``.
        """

        model = models.LearnerCreditRequest

    def get_readonly_fields(self, request, obj=None):
        return super().read_only_fields + self.read_only_fields

    def get_fields(self, request, obj=None):
        return super().fields + self.fields


@admin.register(models.LearnerCreditRequestConfiguration)
class LearnerCreditRequestConfigurationAdmin(DjangoQLSearchMixin, admin.ModelAdmin):
    """ Admin configuration for the LearnerCreditRequestConfiguration model. """

    list_display = (
        'uuid',
        'active',
        'created',
        'modified',
    )

    search_fields = ('uuid',)

    list_filter = ('active',)

    fields = (
        'uuid',
        'active',
        'created',
        'modified',
    )

    readonly_fields = (
        'uuid',
        'created',
        'modified',
    )

    class Meta:
        """
        Meta class for ``LearnerCreditRequestConfigurationAdmin``.
        """

        model = models.LearnerCreditRequestConfiguration
