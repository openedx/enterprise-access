"""
Serializers for Enterprise Access API v1.
"""

from rest_framework import serializers

from enterprise_access.apps.subsidy_request.models import (
    CouponCodeRequest,
    LicenseRequest,
    SubsidyRequest,
    SubsidyRequestCustomerConfiguration
)


class SubsidyRequestSerializer(serializers.ModelSerializer):
    """
    Serializer for the abstract `SubsidyRequest` model.
    """

    email = serializers.EmailField(read_only=True, source="user.email")
    lms_user_id = serializers.IntegerField(read_only=True, source="user.lms_user_id")
    reviewer_lms_user_id = serializers.IntegerField(read_only=True, source="reviewer.lms_user_id", allow_null=True)
    course_partners = serializers.JSONField(read_only=True)

    class Meta:
        model = SubsidyRequest
        fields = [
            'uuid',
            'user',
            'lms_user_id',
            'email',
            'course_id',
            'course_title',
            'course_partners',
            'enterprise_customer_uuid',
            'state',
            'reviewed_at',
            'reviewer_lms_user_id',
            'decline_reason',
            'created',
            'modified',
        ]
        read_only_fields = [
            'uuid',
            'state',
            'lms_user_id',
            'email',
            'course_title',
            'course_partners',
            'reviewed_at',
            'reviewer_lms_user_id',
            'created',
            'modified',
        ]
        extra_kwargs = {
            'user': {'write_only': True},
        }
        abstract = True

class LicenseRequestSerializer(SubsidyRequestSerializer):
    """
    Serializer for the `LicenseRequest` model.
    """

    class Meta:
        model = LicenseRequest
        fields = SubsidyRequestSerializer.Meta.fields + [
            'subscription_plan_uuid',
            'license_uuid'
        ]
        read_only_fields = SubsidyRequestSerializer.Meta.read_only_fields + [
            'subscription_plan_uuid',
            'license_uuid'
        ]
        extra_kwargs = SubsidyRequestSerializer.Meta.extra_kwargs


class CouponCodeRequestSerializer(SubsidyRequestSerializer):
    """
    Serializer for the `CouponCodeRequest` model.
    """

    course_id = serializers.CharField(
        allow_blank=False,
        required=True,
    )

    class Meta:
        model = CouponCodeRequest
        fields = SubsidyRequestSerializer.Meta.fields + [
            'coupon_id',
            'coupon_code'
        ]
        read_only_fields = SubsidyRequestSerializer.Meta.read_only_fields + [
            'coupon_id',
            'coupon_code'
        ]
        extra_kwargs = SubsidyRequestSerializer.Meta.extra_kwargs


class SubsidyRequestCustomerConfigurationSerializer(serializers.ModelSerializer):
    """
    Serializer for the `SubsidyRequestCustomerConfiguration` model.
    """
    changed_by_lms_user_id = serializers.IntegerField(read_only=True, source="changed_by.lms_user_id", allow_null=True)

    class Meta:
        model = SubsidyRequestCustomerConfiguration
        fields = [
            'enterprise_customer_uuid',
            'subsidy_requests_enabled',
            'subsidy_type',
            'changed_by_lms_user_id'
        ]

    def update(self, instance, validated_data):
        # Pop enterprise_customer_uuid so that it's read-only for updates.
        validated_data.pop('enterprise_customer_uuid', None)
        return super().update(instance, validated_data)
