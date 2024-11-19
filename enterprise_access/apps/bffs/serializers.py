"""
Serializers for bffs.
"""

from rest_framework import serializers


class BaseBFFMessageSerializer(serializers.Serializer):
    """
    Base Serializer for BFF messages.

    Fields:
        user_message (str): A user-friendly message.
        developer_message (str): A more detailed message for debugging purposes.
    """
    developer_message = serializers.CharField()
    user_message = serializers.CharField()

    def create(self, validated_data):
        return validated_data

    def update(self, instance, validated_data):
        return validated_data


class ErrorSerializer(BaseBFFMessageSerializer):
    pass


class WarningSerializer(BaseBFFMessageSerializer):
    pass


class BaseResponseSerializer(serializers.Serializer):
    """
    Serializer for base response.
    """

    errors = ErrorSerializer(many=True, required=False, default=list)
    warnings = WarningSerializer(many=True, required=False, default=list)

    def create(self, validated_data):
        return validated_data

    def update(self, instance, validated_data):
        return validated_data


class CustomerAgreementSerializer(serializers.Serializer):
    """
    Serializer for customer agreement.
    """

    uuid = serializers.UUIDField()
    available_subscription_catalogs = serializers.ListField(child=serializers.UUIDField())
    default_enterprise_catalog_uuid = serializers.UUIDField(allow_null=True)
    net_days_until_expiration = serializers.IntegerField()
    disable_expiration_notifications = serializers.BooleanField()
    enable_auto_applied_subscriptions_with_universal_link = serializers.BooleanField()
    subscription_for_auto_applied_licenses = serializers.UUIDField(allow_null=True)

    def create(self, validated_data):
        return validated_data

    def update(self, instance, validated_data):
        return validated_data


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

    def create(self, validated_data):
        return validated_data

    def update(self, instance, validated_data):
        return validated_data


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

    def create(self, validated_data):
        return validated_data

    def update(self, instance, validated_data):
        return validated_data


class SubscriptionLicenseStatusSerializer(serializers.Serializer):
    """
    Serializer for subscription license status.
    """

    activated = SubscriptionLicenseSerializer(many=True, required=False, default=list)
    assigned = SubscriptionLicenseSerializer(many=True, required=False, default=list)
    expired = SubscriptionLicenseSerializer(many=True, required=False, default=list)
    revoked = SubscriptionLicenseSerializer(many=True, required=False, default=list)

    def create(self, validated_data):
        return validated_data

    def update(self, instance, validated_data):
        return validated_data


class SubscriptionsSerializer(serializers.Serializer):
    """
    Serializer for enterprise customer user subsidies.
    """

    customer_agreement = CustomerAgreementSerializer(required=False)
    subscription_licenses = SubscriptionLicenseSerializer(many=True, required=False, default=list)
    subscription_licenses_by_status = SubscriptionLicenseStatusSerializer()

    def create(self, validated_data):
        return validated_data

    def update(self, instance, validated_data):
        return validated_data


class EnterpriseCustomerUserSubsidiesSerializer(serializers.Serializer):
    """
    Serializer for enterprise customer user subsidies.
    """

    subscriptions = SubscriptionsSerializer()

    def create(self, validated_data):
        return validated_data

    def update(self, instance, validated_data):
        return validated_data


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
    certificate_download_url = serializers.CharField(allow_null=True)
    created = serializers.DateTimeField()
    start_date = serializers.DateTimeField(allow_null=True)
    end_date = serializers.DateTimeField(allow_null=True)
    mode = serializers.CharField()
    is_enrollment_active = serializers.BooleanField()
    product_source = serializers.CharField()
    enroll_by = serializers.DateTimeField(allow_null=True)
    pacing = serializers.CharField()
    course_run_url = serializers.URLField()
    resume_course_run_url = serializers.URLField(allow_null=True)
    is_revoked = serializers.BooleanField()

    def create(self, validated_data):
        return validated_data

    def update(self, instance, validated_data):
        return validated_data


class BFFRequestSerializer(serializers.Serializer):
    """
    Serializer for the BFF request.
    """

    enterprise_customer_uuid = serializers.UUIDField(
        required=False,
        help_text="The UUID of the enterprise customer.",
    )
    enterprise_customer_slug = serializers.CharField(
        required=False,
        help_text="The slug of the enterprise customer.",
    )

    def create(self, validated_data):
        return validated_data

    def update(self, instance, validated_data):
        return validated_data


class LearnerDashboardRequestSerializer(BFFRequestSerializer):
    """
    Serializer for the learner dashboard request.
    """


class LearnerDashboardResponseSerializer(BaseLearnerPortalResponseSerializer, serializers.Serializer):
    """
    Serializer for the learner dashboard response.
    """

    enterprise_course_enrollments = EnterpriseCourseEnrollmentSerializer(many=True)
