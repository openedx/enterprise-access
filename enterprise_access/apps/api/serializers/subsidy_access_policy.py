"""
Serializers for the `SubsidyAccessPolicy` model.
"""
import logging
from urllib.parse import urlparse, urlunparse

from crum import get_current_request
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

logger = logging.getLogger(__name__)


class SubsidyAccessPolicyResponseSerializer(serializers.ModelSerializer):
    """
    A read-only Serializer for responding to requests for ``SubsidyAccessPolicy`` records.
    """
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
        read_only_fields = fields


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


class ValidateContentKeyMixin:
    """
    Mixin to provide validation function for the `content_key` serializer field.

    Requires the inheriting serializer to declare a `content_key` field.  Supports one or many content keys (CharField
    or ListField-of-CharField).
    """
    def validate_content_key(self, value):
        """
        Validate `content_key` field.
        """
        content_keys = [value] if isinstance(value, str) else value
        for content_key in content_keys:
            try:
                CourseKey.from_string(content_key)
            except InvalidKeyError as exc:
                raise serializers.ValidationError(f"Invalid content_key: {content_key}") from exc
        return value


# pylint: disable=abstract-method
class SubsidyAccessPolicyRedeemRequestSerializer(ValidateContentKeyMixin, serializers.Serializer):
    """
    Request Serializer to validate policy redeem endpoint POST data.

    For view: SubsidyAccessPolicyRedeemViewset.redeem
    """
    lms_user_id = serializers.IntegerField(required=True)
    content_key = serializers.CharField(required=True)
    metadata = serializers.JSONField(required=False, allow_null=True)


# pylint: disable=abstract-method
class SubsidyAccessPolicyListRequestSerializer(ValidateContentKeyMixin, serializers.Serializer):
    """
    Request Serializer to validate policy list endpoint query params.

    For view: SubsidyAccessPolicyRedeemViewset.list
    """
    lms_user_id = serializers.IntegerField(required=True)
    content_key = serializers.CharField(required=True)
    enterprise_customer_uuid = serializers.UUIDField(required=True)


# pylint: disable=abstract-method
class SubsidyAccessPolicyRedemptionRequestSerializer(ValidateContentKeyMixin, serializers.Serializer):
    """
    Request Serializer to validate policy redemption endpoint query params.

    For view: SubsidyAccessPolicyRedeemViewset.redemption
    """
    lms_user_id = serializers.IntegerField(required=True)
    content_key = serializers.CharField(required=True)
    enterprise_customer_uuid = serializers.UUIDField(required=True)


# pylint: disable=abstract-method
class SubsidyAccessPolicyCreditsAvailableRequestSerializer(serializers.Serializer):
    """
    Request serializer to validate policy credits_available endpoint query params.

    For view: SubsidyAccessPolicyRedeemViewset.credits_available
    """
    enterprise_customer_uuid = serializers.UUIDField(required=True)
    lms_user_id = serializers.IntegerField(required=True)


# pylint: disable=abstract-method
class SubsidyAccessPolicyCanRedeemRequestSerializer(ValidateContentKeyMixin, serializers.Serializer):
    """
    Request serializer to validate can_redeem endpoint query params.

    For view: SubsidyAccessPolicyRedeemViewset.can_redeem
    """
    # enterprise_customer_uuid = serializers.UUIDField(
    #     required=False,
    #     help_text='The enterprise customer UUID under which policies will be queried for redeemability.',
    # )
    content_key = serializers.ListField(
        child=serializers.CharField(required=True),
        allow_empty=False,
        help_text='Content keys about which redeemability will be queried.',
    )


class SubsidyAccessPolicyRedeemableResponseSerializer(serializers.ModelSerializer):
    """
    Response serializer to represent redeemable policies.

    For views:
    * SubsidyAccessPolicyRedeemViewset.list
    * SubsidyAccessPolicyRedeemViewset.can_redeem
    """

    policy_redemption_url = serializers.SerializerMethodField()

    class Meta:
        model = SubsidyAccessPolicy
        exclude = ('created', 'modified')

    def get_policy_redemption_url(self, obj):
        """
        Generate a fully qualified URI that can be POSTed to redeem a policy.

        Deficiencies:
        * In a prod-like environment, this may return an "http" URL because the protocol is inferred from django
          settings, however we tend to deploy TLS above the application layer.  We can't just force it to "https" using
          string manipulation because then it would break dev environments which don't use https.  As-is, it should
          still work in prod because the other non-app infrastructure should automatically 3xx redirect to "https".
        """
        current_request = get_current_request()
        current_scheme = current_request.scheme

        location = reverse('api:v1:policy-redeem', kwargs={'policy_uuid': obj.uuid})
        parsed_url = urlparse(current_request.build_absolute_uri(location))
        return urlunparse(
            parsed_url._replace(scheme=current_scheme)
        )


class SubsidyAccessPolicyCreditsAvailableResponseSerializer(SubsidyAccessPolicyRedeemableResponseSerializer):
    """
    Response serializer to represent redeemable policies with additional information about remaining balance.

    For view: SubsidyAccessPolicyRedeemViewset.credits_available
    """
    remaining_balance_per_user = serializers.SerializerMethodField()
    remaining_balance = serializers.SerializerMethodField()

    def get_remaining_balance_per_user(self, obj):
        lms_user_id = self.context.get('lms_user_id')
        return obj.remaining_balance_per_user(lms_user_id=lms_user_id)

    def get_remaining_balance(self, obj):
        return obj.remaining_balance()
