"""
Serializers for the `SubsidyAccessPolicy` model.
"""
import logging
from urllib.parse import urljoin

from django.apps import apps
from django.conf import settings
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

    per_learner_enrollment_limit = serializers.IntegerField(allow_null=True, required=False)
    per_learner_spend_limit = serializers.IntegerField(allow_null=True, required=False)
    spend_limit = serializers.IntegerField(allow_null=True, required=False)

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


class ContentKeyField(serializers.CharField):
    """
    Serializer Field for Content Keys, created to add basic opaque-keys-based validation.
    """
    def to_internal_value(self, data):
        content_key = data
        try:
            CourseKey.from_string(content_key)
        except InvalidKeyError as exc:
            raise serializers.ValidationError(f"Invalid content_key: {content_key}") from exc
        return super().to_internal_value(content_key)


# pylint: disable=abstract-method
class SubsidyAccessPolicyRedeemRequestSerializer(serializers.Serializer):
    """
    Request Serializer to validate policy redeem endpoint POST data.

    For view: SubsidyAccessPolicyRedeemViewset.redeem
    """
    lms_user_id = serializers.IntegerField(required=True)
    content_key = ContentKeyField(required=True)
    metadata = serializers.JSONField(required=False, allow_null=True)


# pylint: disable=abstract-method
class SubsidyAccessPolicyListRequestSerializer(serializers.Serializer):
    """
    Request Serializer to validate policy list endpoint query params.

    For view: SubsidyAccessPolicyRedeemViewset.list
    """
    lms_user_id = serializers.IntegerField(required=True)
    content_key = ContentKeyField(required=True)
    enterprise_customer_uuid = serializers.UUIDField(required=True)


# pylint: disable=abstract-method
class SubsidyAccessPolicyRedemptionRequestSerializer(serializers.Serializer):
    """
    Request Serializer to validate policy redemption endpoint query params.

    For view: SubsidyAccessPolicyRedeemViewset.redemption
    """
    lms_user_id = serializers.IntegerField(required=True)
    content_key = ContentKeyField(required=True)
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
class SubsidyAccessPolicyCanRedeemRequestSerializer(serializers.Serializer):
    """
    Request serializer to validate can_redeem endpoint query params.

    For view: SubsidyAccessPolicyRedeemViewset.can_redeem
    """
    content_key = serializers.ListField(
        child=ContentKeyField(required=True),
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
        """
        location = reverse('api:v1:policy-redeem', kwargs={'policy_uuid': obj.uuid})
        return urljoin(settings.ENTERPRISE_ACCESS_URL, location)


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


class SubsidyAccessPolicyCanRedeemReasonResponseSerializer(serializers.Serializer):
    """
    Response serializer used to document the structure of a "reason" a content key is not redeemable, used by the
    can_redeem endpoint.
    """
    reason = serializers.CharField(
        help_text="reason code (in camel_case) for why the following subsidy access policies are not redeemable."
    )
    user_message = serializers.CharField(
        help_text="Description of why the following subsidy access policies are not redeemable."
    )
    policy_uuids = serializers.ListField(
        child=serializers.UUIDField()
    )
    metadata = serializers.DictField(
        help_text="context information about the failure reason."
    )


class ListPriceResponseSerializer(serializers.Serializer):
    """
    Response serializer representing a couple different representations of list (content) prices.
    """
    usd = serializers.FloatField(help_text="List price for content, in USD.")
    usd_cents = serializers.IntegerField(help_text="List price for content, in USD cents.")


class SubsidyAccessPolicyCanRedeemElementResponseSerializer(serializers.Serializer):
    """
    Response serializer representing a single element of the response list for the can_redeem endpoint.
    """
    content_key = ContentKeyField(help_text="requested content_key to which the rest of this element pertains.")
    list_price = ListPriceResponseSerializer(help_text="List price for content.")
    redemptions = serializers.ListField(
        # TODO: figure out a way to import TransactionSerializer from enterprise-subsidy.  Until then, the output docs
        # will not describe the redemption fields.
        child=serializers.DictField(),
        help_text="List of redemptions of this content_key by the requested lms_user_id.",
    )
    has_committed_redemption = serializers.BooleanField(
        help_text="True if there are any committed redemptions of this content_key by the requested lms_user_id."
    )
    redeemable_subsidy_access_policy = SubsidyAccessPolicyRedeemableResponseSerializer(
        help_text=(
            "One subsidy access policy selected from potentially multiple redeemable policies for the requested "
            "content_key and lms_user_id."
        )
    )
    can_redeem = serializers.BooleanField(
        help_text="True if there is a redeemable subsidy access policy for the requested content_key and lms_user_id."
    )
    reasons = serializers.ListField(
        child=SubsidyAccessPolicyCanRedeemReasonResponseSerializer(),
        help_text=(
            "List of reasons why each of the enterprise's subsidy access policies are not redeemable, grouped by reason"
        )
    )
