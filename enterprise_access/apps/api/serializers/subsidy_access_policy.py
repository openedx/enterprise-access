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


def policy_pre_write_validation(policy_instance_or_class, field_values_by_name):
    """
    Validates via a policy instance or class that the given fields and values
    don't violate any constraints defined by the policy class' FIELD_CONSTRAINTS.
    If a constraint occurs, raises a `ValidationError`.
    """
    violations = []
    constraints = policy_instance_or_class.FIELD_CONSTRAINTS

    for field_name, new_value in field_values_by_name.items():
        if field_name in constraints:
            constraint_function, error_message = constraints[field_name]
            if not constraint_function(new_value):
                violations.append(error_message)

    if violations:
        raise serializers.ValidationError(
            f'{policy_instance_or_class} has the following field violations: {violations}'
        )


class SubsidyAccessPolicyResponseSerializer(serializers.ModelSerializer):
    """
    A read-only Serializer for responding to requests for ``SubsidyAccessPolicy`` records.
    """
    class Meta:
        model = SubsidyAccessPolicy
        fields = [
            'uuid',
            'policy_type',
            'display_name',
            'description',
            'active',
            'enterprise_customer_uuid',
            'catalog_uuid',
            'subsidy_uuid',
            'access_method',
            'per_learner_enrollment_limit',
            'per_learner_spend_limit',
            'spend_limit',
            'subsidy_active_datetime',
            'subsidy_expiration_datetime',
            'is_subsidy_active',
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
            'display_name',
            'description',
            'active',
            'enterprise_customer_uuid',
            'catalog_uuid',
            'subsidy_uuid',
            'access_method',
            'per_learner_enrollment_limit',
            'per_learner_spend_limit',
            'spend_limit',
            'subsidy_active_datetime',
            'subsidy_expiration_datetime',
            'is_subsidy_active',
        ]
        read_only_fields = ['uuid']
        extra_kwargs = {
            'uuid': {'read_only': True},
            'display_name': {
                'min_length': None,
                'max_length': 512,
                'trim_whitespace': True,
            },
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
            'subsidy_active_datetime': {
                'allow_null': True,
                'required': False,
            },
            'subsidy_expiration_datetime': {
                'allow_null': True,
                'required': False,
            },
            'is_subsidy_active': {
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
        policy_class = SubsidyAccessPolicy.get_policy_class_by_type(attrs['policy_type'])
        policy_pre_write_validation(policy_class, attrs)
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


class SubsidyAccessPolicyUpdateRequestSerializer(serializers.ModelSerializer):
    """
    Request Serializer for PUT or PATCH requests to update a subsidy access policy.

    For views: SubsidyAccessPolicyViewSet.update and SubsidyAccessPolicyViewSet.partial_update.
    """
    class Meta:
        model = SubsidyAccessPolicy
        fields = (
            'display_name',
            'description',
            'active',
            'catalog_uuid',
            'subsidy_uuid',
            'access_method',
            'spend_limit',
            'per_learner_spend_limit',
            'per_learner_enrollment_limit',
            'subsidy_active_datetime',
            'subsidy_expiration_datetime',
            'is_subsidy_active',
        )
        extra_kwargs = {
            'display_name': {
                'min_length': None,
                'max_length': 512,
                'trim_whitespace': True,
            },
            'description': {
                'required': False,
                'allow_blank': False,
                'min_length': None,
                'max_length': 200,
                'trim_whitespace': True,
            },
            'active': {
                'allow_null': False,
                'required': False,
            },
            'catalog_uuid': {
                'allow_null': False,
                'required': False,
            },
            'subsidy_uuid': {
                'allow_null': False,
                'required': False,
            },
            'access_method': {
                'allow_null': False,
                'required': False,
            },
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
            'subsidy_active_datetime': {
                'allow_null': True,
                'required': False,
            },
            'subsidy_expiration_datetime': {
                'allow_null': True,
                'required': False,
            },
            'is_subsidy_active': {
                'allow_null': True,
                'required': False,
            },
        }

    def validate(self, attrs):
        """
        Raises a ValidationError if any field not explicitly declared
        as a field in this serializer definition is provided as input.
        """
        unknown = sorted(set(self.initial_data) - set(self.fields))
        if unknown:
            raise serializers.ValidationError("Field(s) are not updatable: {}".format(", ".join(unknown)))
        return attrs

    def update(self, instance, validated_data):
        """
        Overwrites the update() method to check that no fields
        that are valid in a type of SubsidyAccessPolicy that is *different*
        from the type of ``instance`` are present in ``validated_data``.

        We have to do this validation here so that we have access
        to a ``SubsidyAccessPolicy`` instance.  It's not required
        for the caller of the policy update view to provide a policy type,
        so we can't infer the desired type from the request payload.
        """
        policy_pre_write_validation(instance, validated_data)
        return super().update(instance, validated_data)

    def to_representation(self, instance):
        """
        Once a SubsidyAccessPolicy has been updated, we want to serialize
        more fields from the instance than are required in this, the input serializer.
        """
        read_serializer = SubsidyAccessPolicyResponseSerializer(instance)
        return read_serializer.data


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
    subsidy_expiration_date = serializers.SerializerMethodField()

    def get_remaining_balance_per_user(self, obj):
        lms_user_id = self.context.get('lms_user_id')
        return obj.remaining_balance_per_user(lms_user_id=lms_user_id)

    def get_remaining_balance(self, obj):
        return obj.remaining_balance()

    def get_subsidy_expiration_date(self, obj):
        return obj.subsidy_expiration_datetime


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
