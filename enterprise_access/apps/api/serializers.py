"""
Serializers for Enterprise Access API v1.
"""

from rest_framework import serializers

from enterprise_access.apps.subsidy_request.models import CouponCodeRequest, LicenseRequest, SubsidyRequest


class SubsidyRequestSerializer(serializers.ModelSerializer):
    """
    Serializer for the abstract `SubsidyRequest` model.
    """

    class Meta:
        model = SubsidyRequest
        fields = [
            'uuid',
            'lms_user_id',
            'course_id',
            'enterprise_customer_uuid',
            'state',
            'reviewed_at',
            'reviewer_lms_user_id',
            'denial_reason',
            'created',
            'modified',

        ]
        read_only_fields = [
            'uuid',
            'state',
            'reviewed_at',
            'reviewer_lms_user_id',
            'denial_reason',
            'created',
            'modified',
        ]
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
