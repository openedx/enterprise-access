"""
Serializers for Enterprise Access API v1.
"""
import logging

from django.apps import apps
from django.urls import reverse
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import serializers

from enterprise_access.apps.subsidy_access_policy.constants import (
    POLICY_TYPE_CREDIT_LIMIT_FIELDS,
    POLICY_TYPE_FIELD_MAPPER,
    POLICY_TYPES_WITH_CREDIT_LIMIT,
    AccessMethods,
    PolicyTypes
)
from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy
from enterprise_access.apps.subsidy_request.models import (
    CouponCodeRequest,
    LicenseRequest,
    SubsidyRequest,
    SubsidyRequestCustomerConfiguration
)

logger = logging.getLogger(__name__)


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


class SubsidyAccessPolicyRedeemSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer to validate policy redeem request POST data.
    """
    learner_id = serializers.IntegerField(required=True)
    content_key = serializers.CharField(required=True)
    metadata = serializers.JSONField(required=False)

    def validate_content_key(self, value):
        """
        Validate `content_key`.
        """
        try:
            CourseKey.from_string(value)
        except InvalidKeyError as exc:
            raise serializers.ValidationError(f"Invalid course key: {value}") from exc

        return value


class SubsidyAccessPolicyCRUDSerializer(serializers.ModelSerializer):
    """
    Serializer to validate policy data for CRUD operations.
    """
    uuid = serializers.UUIDField(read_only=True)
    policy_type = serializers.ChoiceField(choices=PolicyTypes.CHOICES)
    description = serializers.CharField(max_length=None, min_length=None, allow_blank=False, trim_whitespace=True)
    active = serializers.BooleanField()
    enterprise_customer_uuid = serializers.UUIDField(allow_null=False, required=True)
    catalog_uuid = serializers.UUIDField(allow_null=False)
    subsidy_uuid = serializers.UUIDField(allow_null=False)
    access_method = serializers.ChoiceField(choices=AccessMethods.CHOICES)

    per_learner_enrollment_limit = serializers.IntegerField()
    per_learner_spend_limit = serializers.IntegerField()
    spend_limit = serializers.IntegerField()

    class Meta:
        model = SubsidyAccessPolicy
        fields = [
            'uuid',
            'policy_type',
            'description',
            'active',
            'enterprise_customer_uuid',
            'catalog_uuid',
            'subsidy_uuid',
            'access_method',
            'per_learner_enrollment_limit',
            'per_learner_spend_limit',
            'spend_limit',
        ]
        read_only_fields = ['uuid']

    def create(self, validated_data):
        policy_type = validated_data.get('policy_type')
        policy_model = apps.get_model(app_label='subsidy_access_policy', model_name=policy_type)
        policy = policy_model.objects.create(**validated_data)
        return policy

    def validate(self, attrs):
        super().validate(attrs)
        policy_type = attrs.get('policy_type', None)
        if policy_type:
            # just some extra caution around discarding the other two credit limits if they have a non-zero value
            if policy_type in POLICY_TYPES_WITH_CREDIT_LIMIT:
                for field in POLICY_TYPE_CREDIT_LIMIT_FIELDS:
                    if field != POLICY_TYPE_FIELD_MAPPER.get(policy_type):
                        attrs.pop(field)
        return attrs


class SubsidyAccessPolicyRedeemListSerializer(SubsidyAccessPolicyRedeemSerializer):  # pylint: disable=abstract-method
    """
    Serializer to validate policy request GET query params.
    """
    enterprise_customer_uuid = serializers.UUIDField(required=True)


class SubsidyAccessPolicyRedeemableSerializer(serializers.ModelSerializer):
    """
    Serializer to transform response for policy redeem GET endpoint.
    """

    policy_redemption_url = serializers.SerializerMethodField()

    class Meta:
        model = SubsidyAccessPolicy
        exclude = ('created', 'modified')

    def get_policy_redemption_url(self, obj):
        return reverse('api:v1:policy-redeem', kwargs={'policy_uuid': obj.uuid})


class SubsidyAccessPolicyCreditAvailableSerializer(SubsidyAccessPolicyRedeemableSerializer):
    """
    Serializer to transform response for policy redeem GET endpoint.
    """
    remaining_balance_per_user = serializers.SerializerMethodField()
    remaining_balance = serializers.SerializerMethodField()

    def get_remaining_balance_per_user(self, obj):
        learner_id = self.context.get('learner_id')
        return obj.remaining_balance_per_user(learner_id=learner_id)

    def get_remaining_balance(self, obj):
        return obj.remaining_balance()


class SubsidyAccessPolicyCreditAvailableListSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer to validate policy request GET query params.
    """
    enterprise_customer_uuid = serializers.UUIDField(required=True)
    lms_user_id = serializers.CharField(required=True)


class SubsidyAccessPolicyCanRedeemRequestSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer to validate SubsidyAccessPolicyRedeemViewset.can_redeem GET request parameters.
    """
    enterprise_customer_uuid = serializers.UUIDField(required=True)
    content_key = serializers.ListField(child=serializers.CharField(required=True), allow_empty=False)

    def validate_content_key(self, value):
        """
        Validate `content_key`.
        """
        for content_key in value:
            try:
                CourseKey.from_string(content_key)
            except InvalidKeyError as exc:
                raise serializers.ValidationError(f"Invalid course key: {content_key}") from exc

        return value
