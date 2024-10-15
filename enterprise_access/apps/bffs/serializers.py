"""
Serializers for bffs.
"""

from collections import OrderedDict

from rest_framework import serializers


class ErrorSerializer(serializers.Serializer):
    """
    Serializer for error.
    """

    developer_message = serializers.CharField()
    user_message = serializers.CharField()


class WarningSerializer(serializers.Serializer):
    """
    Serializer for warning.
    """

    developer_message = serializers.CharField()
    user_message = serializers.CharField()


class BaseResponseSerializer(serializers.Serializer):
    """
    Serializer for base response.
    """

    errors = ErrorSerializer(many=True, required=False, default=list)
    warnings = WarningSerializer(many=True, required=False, default=list)

    def to_representation(self, instance):
        """
        Override to_representation method to return ordered representation
        with errors/warnings at the end of the response.
        """
        representation = super().to_representation(instance)

        ordered_representation = OrderedDict(representation)

        # Remove errors and warnings from the main response (they will be re-added at the end)
        errors = ordered_representation.pop('errors', [])
        warnings = ordered_representation.pop('warnings', [])

        # Add errors and warnings at the end of the response
        ordered_representation['errors'] = errors
        ordered_representation['warnings'] = warnings

        return ordered_representation


class CustomerAgreementSerializer(serializers.Serializer):
    """
    Serializer for customer agreement.
    """

    uuid = serializers.UUIDField()
    available_subscription_catalogs = serializers.ListField(child=serializers.UUIDField())
    default_enterprise_catalog_uuid = serializers.UUIDField()
    net_days_until_expiration = serializers.IntegerField()
    disable_expiration_notifications = serializers.BooleanField()
    enable_auto_applied_subscriptions_with_universal_link = serializers.BooleanField()
    subscription_for_auto_applied_licenses = serializers.UUIDField(allow_null=True)


class SubscriptionPlanSerializer(serializers.Serializer):
    """
    Serializer for subscription plan.
    """

    uuid = serializers.UUIDField()
    title = serializers.CharField()
    enterprise_catalog_uuid = serializers.UUIDField()
    is_active = serializers.BooleanField()
    is_current = serializers.BooleanField()
    start_date = serializers.DateTimeField()
    expiration_date = serializers.DateTimeField()
    days_until_expiration = serializers.IntegerField()
    days_until_expiration_including_renewals = serializers.IntegerField()
    should_auto_apply_licenses = serializers.BooleanField()


class SubscriptionLicenseSerializer(serializers.Serializer):
    """
    Serializer for subscription license.
    """

    uuid = serializers.UUIDField()
    status = serializers.CharField()
    user_email = serializers.EmailField()
    activation_date = serializers.DateTimeField(allow_null=True)
    last_remind_date = serializers.DateTimeField(allow_null=True)
    revoked_date = serializers.DateTimeField(allow_null=True)
    activation_key = serializers.CharField()
    subscription_plan = SubscriptionPlanSerializer()


class SubscriptionLicenseStatusSerializer(serializers.Serializer):
    """
    Serializer for subscription license status.
    """

    activated = SubscriptionLicenseSerializer(many=True, required=False, default=list)
    assigned = SubscriptionLicenseSerializer(many=True, required=False, default=list)
    expired = SubscriptionLicenseSerializer(many=True, required=False, default=list)
    revoked = SubscriptionLicenseSerializer(many=True, required=False, default=list)


class SubscriptionsSerializer(serializers.Serializer):
    """
    Serializer for enterprise customer user subsidies.
    """

    customer_agreement = CustomerAgreementSerializer()
    subscription_licenses = SubscriptionLicenseSerializer(many=True)
    subscription_licenses_by_status = SubscriptionLicenseStatusSerializer()


class EnterpriseCustomerUserSubsidiesSerializer(serializers.Serializer):
    """
    Serializer for enterprise customer user subsidies.
    """

    subscriptions = SubscriptionsSerializer()


class BaseLearnerPortalResponseSerializer(BaseResponseSerializer, serializers.Serializer):
    """
    Serializer for base learner portal response.
    """

    enterprise_customer_user_subsidies = EnterpriseCustomerUserSubsidiesSerializer()


class EnterpriseCourseEnrollmentSerializer(serializers.Serializer):
    """
    Serializer for enterprise course enrollment.
    """

    course_run_id = serializers.CharField()
    course_key = serializers.CharField()
    course_type = serializers.CharField()
    org_name = serializers.CharField()
    course_run_status = serializers.CharField()
    display_name = serializers.CharField()
    emails_enabled = serializers.BooleanField()
    certificate_download_url = serializers.URLField(allow_null=True)
    created = serializers.DateTimeField()
    start_date = serializers.DateTimeField()
    end_date = serializers.DateTimeField()
    mode = serializers.CharField()
    is_enrollment_active = serializers.BooleanField()
    product_source = serializers.CharField()
    enroll_by = serializers.DateTimeField()
    pacing = serializers.CharField()
    course_run_url = serializers.URLField()
    resume_course_run_url = serializers.URLField(allow_null=True)
    is_revoked = serializers.BooleanField()


class LearnerDashboardResponseSerializer(BaseLearnerPortalResponseSerializer, serializers.Serializer):
    """
    Serializer for the learner dashboard response.
    """

    enterprise_course_enrollments = EnterpriseCourseEnrollmentSerializer(many=True)
