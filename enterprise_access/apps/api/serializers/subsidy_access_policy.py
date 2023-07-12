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

from enterprise_access.apps.subsidy_access_policy.constants import PolicyTypes
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

    # Since the upstream model field has editable=False, we must redefine the field here because editable fields are
    # automatically skipped by validation, but we do actually want it to be validated.
    policy_type = serializers.ChoiceField(choices=PolicyTypes.CHOICES)

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
        extra_kwargs = {
            'uuid': {'read_only': True},
            'description': {
                'allow_blank': False,
                'min_length': None,
                'max_length': 200,
                'trim_whitespace': True,
            },
            'enterprise_customer_uuid': {
                'allow_null': False,
                'required': True,
            },
            'catalog_uuid': {
                'allow_null': False,
                'required': True,
            },
            'subsidy_uuid': {
                'allow_null': False,
                'required': True,
            },
            'access_method': {'required': True},
            'spend_limit': {
                'allow_null': True,
                'required': False,
            },
            'per_learner_enrollment_limit': {
                'allow_null': True,
                'required': False,
            },
            'per_learner_spend_limit': {
                'allow_null': True,
                'required': False,
            },
        }

    @property
    def calling_view(self):
        """
        Return the view that called this serializer.
        """
        return self.context['view']

    def create(self, validated_data):
        policy_type = validated_data.get('policy_type')
        policy_model = apps.get_model(app_label='subsidy_access_policy', model_name=policy_type)
        filtered_policy = policy_model.objects.filter(
            enterprise_customer_uuid=validated_data['enterprise_customer_uuid'],
            subsidy_uuid=validated_data['subsidy_uuid'],
            catalog_uuid=validated_data['catalog_uuid'],
            access_method=validated_data['access_method'],
            active=True,
        ).first()
        if filtered_policy:
            self.calling_view.set_policy_created(False)
            return filtered_policy
        self.calling_view.set_policy_created(True)
        policy = policy_model.objects.create(**validated_data)
        return policy

    def validate(self, attrs):
        super().validate(attrs)

        # Get the policy subclass.
        # super().validate() already checked that attrs contains a "policy_type" key and valid value.
        policy_type = attrs['policy_type']
        policy_class = SubsidyAccessPolicy.get_policy_class_by_type(policy_type)

        # Must specify exactly the required custom fields as declared by the policy subclass, no more no less.
        custom_policy_field_errors = []
        # Here's the "no less" part:
        if not set(policy_class.REQUIRED_CUSTOM_FIELDS).issubset(attrs.keys()):
            custom_policy_field_errors.append(
                f"Missing fields for {policy_type} policy type: {policy_class.REQUIRED_CUSTOM_FIELDS}."
            )
        # Here's the "no more" part:
        unused_custom_fields = set(policy_class.ALL_CUSTOM_FIELDS) - set(policy_class.REQUIRED_CUSTOM_FIELDS)
        if unused_custom_fields.intersection(attrs.keys()):
            custom_policy_field_errors.append(
                f"Extraneous fields for {policy_type} policy type: {list(unused_custom_fields)}."
            )
        if custom_policy_field_errors:
            raise serializers.ValidationError(custom_policy_field_errors)

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


# pylint: disable=abstract-method
class SubsidyAccessPolicyDeleteRequestSerializer(serializers.Serializer):
    """
    Request Serializer for DELETE parameters to an API call to delete a subsidy access policy.

    For view: SubsidyAccessPolicyViewSet.destroy
    """
    reason = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="Optional description (free form text) for why the subsidy access policy is being deleted.",
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
        location = reverse('api:v1:policy-redemption-redeem', kwargs={'policy_uuid': obj.uuid})
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
    has_successful_redemption = serializers.BooleanField(
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
