"""
Serializers for bffs.
"""

from rest_framework import serializers


class BaseBffSerializer(serializers.Serializer):
    """
    Base Serializer for BFF.
    """

    def create(self, validated_data):
        return validated_data

    def update(self, instance, validated_data):
        return validated_data


class BaseBFFMessageSerializer(BaseBffSerializer):
    """
    Base Serializer for BFF messages.

    Fields:
        user_message (str): A user-friendly message.
        developer_message (str): A more detailed message for debugging purposes.
    """
    developer_message = serializers.CharField()
    user_message = serializers.CharField()


class ErrorSerializer(BaseBFFMessageSerializer):
    pass


class WarningSerializer(BaseBFFMessageSerializer):
    pass


class EnterpriseCustomerSiteSerializer(BaseBffSerializer):
    """
    Serializer for enterprise customer site.
    """

    domain = serializers.CharField()
    name = serializers.CharField()


class EnterpriseCustomerBrandingConfiguration(BaseBffSerializer):
    """
    Serializer for enterprise customer branding configuration.
    """

    logo = serializers.URLField(required=False, allow_null=True)
    primary_color = serializers.CharField()
    secondary_color = serializers.CharField()
    tertiary_color = serializers.CharField()


class EnterpriseCustomerNotificationBanner(BaseBffSerializer):
    """
    Serializer for enterprise customer notification banner.
    """
    title = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    text = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class EnterpriseCustomerAdminUser(BaseBffSerializer):
    """
    Serializer for enterprise customer admin user.
    """
    email = serializers.EmailField(required=False, allow_null=True)
    lms_user_id = serializers.IntegerField()


class EnterpriseCustomerActiveIntegration(BaseBffSerializer):
    """
    Serializer for enterprise customer integration.
    """
    channel_code = serializers.CharField()
    created = serializers.DateTimeField()
    modified = serializers.DateTimeField()
    display_name = serializers.CharField()
    active = serializers.BooleanField()


class EnterpriseCustomerIdentityProvider(BaseBffSerializer):
    """
    Serializer for enterprise customer identity provider.
    """
    provider_id = serializers.CharField()
    default_provider = serializers.BooleanField()


class EnterpriseCustomerSerializer(BaseBffSerializer):
    """
    Serializer for enterprise customer.
    """

    uuid = serializers.UUIDField()
    slug = serializers.CharField()
    name = serializers.CharField()
    active = serializers.BooleanField()
    auth_org_id = serializers.CharField(required=False, allow_null=True)
    site = EnterpriseCustomerSiteSerializer()
    branding_configuration = EnterpriseCustomerBrandingConfiguration()
    identity_provider = serializers.CharField(required=False, allow_null=True)
    identity_providers = serializers.ListField(child=EnterpriseCustomerIdentityProvider(), required=False, default=list)
    enable_data_sharing_consent = serializers.BooleanField()
    enforce_data_sharing_consent = serializers.CharField()
    disable_expiry_messaging_for_learner_credit = serializers.BooleanField()
    enable_audit_enrollment = serializers.BooleanField()
    replace_sensitive_sso_username = serializers.BooleanField()
    enable_portal_code_management_screen = serializers.BooleanField()
    sync_learner_profile_data = serializers.BooleanField()
    enable_audit_data_reporting = serializers.BooleanField()
    enable_learner_portal = serializers.BooleanField()
    enable_learner_portal_offers = serializers.BooleanField()
    enable_portal_learner_credit_management_screen = serializers.BooleanField()
    enable_executive_education_2U_fulfillment = serializers.BooleanField()
    enable_portal_reporting_config_screen = serializers.BooleanField()
    enable_portal_saml_configuration_screen = serializers.BooleanField()
    contact_email = serializers.EmailField(required=False, allow_null=True)
    enable_portal_subscription_management_screen = serializers.BooleanField()
    hide_course_original_price = serializers.BooleanField()
    enable_analytics_screen = serializers.BooleanField()
    enable_integrated_customer_learner_portal_search = serializers.BooleanField()
    enable_generation_of_api_credentials = serializers.BooleanField()
    enable_portal_lms_configurations_screen = serializers.BooleanField()
    sender_alias = serializers.CharField(required=False, allow_null=True)
    enterprise_customer_catalogs = serializers.ListField(child=serializers.UUIDField(), required=False, default=list)
    reply_to = serializers.EmailField(required=False, allow_null=True)
    enterprise_notification_banner = EnterpriseCustomerNotificationBanner(required=False, allow_null=True)
    hide_labor_market_data = serializers.BooleanField()
    modified = serializers.DateTimeField()
    enable_universal_link = serializers.BooleanField()
    enable_browse_and_request = serializers.BooleanField()
    admin_users = EnterpriseCustomerAdminUser(many=True, required=False, default=list)
    enable_learner_portal_sidebar_message = serializers.BooleanField()
    learner_portal_sidebar_content = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    enable_pathways = serializers.BooleanField()
    enable_programs = serializers.BooleanField()
    enable_demo_data_for_analytics_and_lpr = serializers.BooleanField()
    enable_academies = serializers.BooleanField()
    enable_one_academy = serializers.BooleanField()
    active_integrations = EnterpriseCustomerActiveIntegration(many=True, required=False, default=list)
    show_videos_in_learner_portal_search_results = serializers.BooleanField()
    default_language = serializers.CharField(required=False, allow_null=True)
    country = serializers.CharField()
    enable_slug_login = serializers.BooleanField()
    disable_search = serializers.BooleanField()
    show_integration_warning = serializers.BooleanField()


class EnterpriseCustomerUserSerializer(BaseBffSerializer):
    """
    Serializer for all linked enterprise customer users
    """
    id = serializers.IntegerField()
    user_id = serializers.IntegerField()
    enterprise_customer = EnterpriseCustomerSerializer()
    active = serializers.BooleanField()


class BaseResponseSerializer(BaseBffSerializer):
    """
    Serializer for base response.
    """

    enterprise_customer = EnterpriseCustomerSerializer(required=False, allow_null=True)
    all_linked_enterprise_customer_users = EnterpriseCustomerUserSerializer(many=True, allow_empty=True, default=list)
    should_update_active_enterprise_customer_user = serializers.BooleanField(default=False)
    errors = ErrorSerializer(many=True, required=False, default=list)
    warnings = WarningSerializer(many=True, required=False, default=list)
    enterprise_features = serializers.DictField(required=False, default=dict)


class CustomerAgreementSerializer(BaseBffSerializer):
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
    has_custom_license_expiration_messaging_v2 = serializers.BooleanField(
        required=False, allow_null=True, default=False,
    )
    button_label_in_modal_v2 = serializers.CharField(required=False, allow_null=True)
    expired_subscription_modal_messaging_v2 = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    modal_header_text_v2 = serializers.CharField(required=False, allow_null=True)
    url_for_button_in_modal_v2 = serializers.CharField(required=False, allow_null=True)


class SubscriptionPlanSerializer(BaseBffSerializer):
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
    should_auto_apply_licenses = serializers.BooleanField(allow_null=True)


class SubscriptionLicenseSerializer(BaseBffSerializer):
    """
    Serializer for subscription license.
    """

    uuid = serializers.UUIDField()
    status = serializers.CharField()
    user_email = serializers.EmailField(allow_null=True)
    activation_date = serializers.DateTimeField(allow_null=True)
    last_remind_date = serializers.DateTimeField(allow_null=True)
    revoked_date = serializers.DateTimeField(allow_null=True)
    activation_key = serializers.CharField(allow_null=True)
    subscription_plan = SubscriptionPlanSerializer()


class SubscriptionLicenseStatusSerializer(BaseBffSerializer):
    """
    Serializer for subscription license status.
    """

    activated = SubscriptionLicenseSerializer(many=True, required=False, default=list)
    assigned = SubscriptionLicenseSerializer(many=True, required=False, default=list)
    revoked = SubscriptionLicenseSerializer(many=True, required=False, default=list)


class SubscriptionsSerializer(BaseBffSerializer):
    """
    Serializer for subscriptions subsidies.
    """

    customer_agreement = CustomerAgreementSerializer(required=False, allow_null=True)
    subscription_licenses = SubscriptionLicenseSerializer(many=True, required=False, default=list)
    subscription_licenses_by_status = SubscriptionLicenseStatusSerializer(required=False)
    subscription_license = SubscriptionLicenseSerializer(required=False, allow_null=True)
    subscription_plan = SubscriptionPlanSerializer(required=False, allow_null=True)
    show_expiration_notifications = serializers.BooleanField(required=False)


class EnterpriseCustomerUserSubsidiesSerializer(BaseBffSerializer):
    """
    Serializer for enterprise customer user subsidies.
    """

    subscriptions = SubscriptionsSerializer(required=False, default=dict)


class BaseLearnerPortalResponseSerializer(BaseResponseSerializer):
    """
    Serializer for base learner portal response.
    """

    enterprise_customer_user_subsidies = EnterpriseCustomerUserSubsidiesSerializer()


class EnrollmentDueDateSerializer(BaseBffSerializer):
    """
    Serializer for enrollment due date.
    """

    name = serializers.CharField()
    date = serializers.CharField()
    url = serializers.URLField()


class EnterpriseCourseEnrollmentSerializer(BaseBffSerializer):
    """
    Serializer for enterprise course enrollment.
    """

    can_unenroll = serializers.BooleanField()
    course_run_id = serializers.CharField()
    course_run_status = serializers.CharField()
    course_key = serializers.CharField()
    course_type = serializers.CharField()
    created = serializers.DateTimeField()
    end_date = serializers.DateTimeField(allow_null=True)
    enroll_by = serializers.DateTimeField(allow_null=True)
    has_emails_enabled = serializers.BooleanField()
    is_enrollment_active = serializers.BooleanField()
    is_revoked = serializers.BooleanField()
    link_to_course = serializers.URLField()
    link_to_certificate = serializers.URLField(allow_null=True)
    micromasters_title = serializers.CharField(allow_null=True)
    mode = serializers.CharField()
    notifications = serializers.ListField(
        child=EnrollmentDueDateSerializer(),
        allow_empty=True,
    )
    org_name = serializers.CharField()
    pacing = serializers.CharField()
    product_source = serializers.CharField()
    resume_course_run_url = serializers.URLField(allow_null=True)
    start_date = serializers.DateTimeField(allow_null=True)
    title = serializers.CharField()


class BFFRequestSerializer(BaseBffSerializer):
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


class LearnerDashboardRequestSerializer(BFFRequestSerializer):
    """
    Serializer for the learner dashboard request.
    """


class LearnerEnrollmentsByStatusSerializer(BaseBffSerializer):
    """
    Serializer for subscription license status.
    """

    in_progress = EnterpriseCourseEnrollmentSerializer(many=True, required=False, default=list)
    upcoming = EnterpriseCourseEnrollmentSerializer(many=True, required=False, default=list)
    completed = EnterpriseCourseEnrollmentSerializer(many=True, required=False, default=list)
    saved_for_later = EnterpriseCourseEnrollmentSerializer(many=True, required=False, default=list)


class LearnerDashboardResponseSerializer(BaseLearnerPortalResponseSerializer):
    """
    Serializer for the learner dashboard response.
    """

    enterprise_course_enrollments = EnterpriseCourseEnrollmentSerializer(many=True)
    all_enrollments_by_status = LearnerEnrollmentsByStatusSerializer()
