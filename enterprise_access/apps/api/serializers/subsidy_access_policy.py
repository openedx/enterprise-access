"""
Serializers for the `SubsidyAccessPolicy` model.
"""
import logging
from urllib.parse import urljoin

from django.apps import apps
from django.conf import settings
from django.urls import reverse
from drf_spectacular.utils import extend_schema_field
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from requests.exceptions import HTTPError
from rest_framework import serializers

from enterprise_access.apps.api.serializers.subsidy_requests import LearnerCreditRequestSerializer
from enterprise_access.apps.content_assignments.content_metadata_api import get_content_metadata_for_assignments
from enterprise_access.apps.subsidy_access_policy.constants import (
    CENTS_PER_DOLLAR,
    SORT_BY_ENROLLMENT_COUNT,
    PolicyTypes
)
from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy
from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates
from enterprise_access.apps.subsidy_request.models import LearnerCreditRequest

from .content_assignments.assignment import (
    LearnerContentAssignmentResponseSerializer,
    LearnerContentAssignmentWithLearnerAcknowledgedResponseSerializer
)
from .content_assignments.assignment_configuration import AssignmentConfigurationResponseSerializer

logger = logging.getLogger(__name__)


def policy_pre_write_validation(policy_instance_or_class, field_values_by_name):
    """
    Validates via a policy instance or class that the given fields and values
    don't violate any constraints defined by the policy class' FIELD_CONSTRAINTS.
    If a constraint occurs, raises a `ValidationError`.
    """
    violations = []

    if isinstance(policy_instance_or_class, SubsidyAccessPolicy):
        policy_instance_or_class.clean()
    else:
        instance = policy_instance_or_class(**field_values_by_name)
        instance.clean()

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


# pylint: disable=abstract-method
class SubsidyAccessPolicyAggregatesSerializer(serializers.Serializer):
    """
    Response serializer representing aggregates about the policy and related objects.
    """
    amount_redeemed_usd_cents = serializers.SerializerMethodField(
        help_text="Total Amount redeemed for policy, in positive USD cents.",
    )
    amount_redeemed_usd = serializers.SerializerMethodField(
        help_text="Total Amount redeemed for policy, in USD.",
    )
    amount_allocated_usd_cents = serializers.SerializerMethodField(
        help_text=(
            f"Total amount allocated for policies of type {PolicyTypes.ASSIGNED_LEARNER_CREDIT} (0 otherwise), in "
            "positive USD cents."
        ),
    )
    amount_allocated_usd = serializers.SerializerMethodField(
        help_text=(
            f"Total amount allocated for policies of type {PolicyTypes.ASSIGNED_LEARNER_CREDIT} (0 otherwise), in USD.",
        ),
    )
    spend_available_usd_cents = serializers.SerializerMethodField(
        help_text="Total Amount of available spend for policy, in positive USD cents.",
    )
    spend_available_usd = serializers.SerializerMethodField(
        help_text="Total Amount of available spend for policy, in USD.",
    )

    @extend_schema_field(serializers.IntegerField)
    def get_amount_redeemed_usd_cents(self, policy):
        """
        Make amount a positive number.
        Protect against Subsidy API Errors.
        """
        try:
            return policy.total_redeemed * -1
        except HTTPError as exc:
            logger.exception(f"HTTPError from subsidy service: {exc}")
            return None

    @extend_schema_field(serializers.IntegerField)
    def get_amount_allocated_usd_cents(self, policy):
        """
        Make amount a positive number.
        """
        return policy.total_allocated * -1

    @extend_schema_field(serializers.IntegerField)
    def get_spend_available_usd_cents(self, policy):
        """
        Protect against Subsidy API Errors.
        """
        try:
            return policy.spend_available
        except HTTPError as exc:
            logger.exception(f"HTTPError from subsidy service: {exc}")
            return None

    @extend_schema_field(serializers.FloatField)
    def get_amount_redeemed_usd(self, policy):
        """
        Make amount a positive number.
        Convert cents to dollars.
        Protect against Subsidy API Errors.
        """
        try:
            return float(policy.total_redeemed * -1) / CENTS_PER_DOLLAR
        except HTTPError as exc:
            logger.exception(f"HTTPError from subsidy service: {exc}")
            return None

    @extend_schema_field(serializers.FloatField)
    def get_amount_allocated_usd(self, policy):
        """
        Make amount a positive number.
        Convert cents to dollars.
        """
        return float(policy.total_allocated * -1) / CENTS_PER_DOLLAR

    @extend_schema_field(serializers.FloatField)
    def get_spend_available_usd(self, policy):
        """
        Convert cents to dollars.
        Protect against Subsidy API Errors.
        """
        try:
            return float(policy.spend_available) / CENTS_PER_DOLLAR
        except HTTPError as exc:
            logger.exception(f"HTTPError from subsidy service: {exc}")
            return None


class SubsidyAccessPolicyResponseSerializer(serializers.ModelSerializer):
    """
    A read-only Serializer for responding to requests for ``SubsidyAccessPolicy`` records.
    """
    aggregates = SubsidyAccessPolicyAggregatesSerializer(
        help_text='Aggregates about the policy and related objects.',
        # This causes the entire unserialized model to be passed into the nested serializer.
        source='*',
    )
    assignment_configuration = AssignmentConfigurationResponseSerializer(
        help_text='AssignmentConfiguration object for this policy.',
    )
    group_associations = serializers.SerializerMethodField()

    class Meta:
        model = SubsidyAccessPolicy
        fields = [
            'uuid',
            'policy_type',
            'display_name',
            'description',
            'active',
            'retired',
            'retired_at',
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
            'aggregates',
            'assignment_configuration',
            'group_associations',
            'late_redemption_allowed_until',
            'is_late_redemption_allowed',
            'created',
        ]
        read_only_fields = fields

    def get_group_associations(self, obj):
        return list(obj.groups.values_list("enterprise_group_uuid", flat=True))


class SubsidyAccessPolicyCRUDSerializer(serializers.ModelSerializer):
    """
    Serializer to validate policy data for CRUD operations.
    """

    # Since the upstream model field has editable=False, we must redefine the field here because editable fields are
    # automatically skipped by validation, but we do actually want it to be validated.
    policy_type = serializers.ChoiceField(choices=PolicyTypes.CHOICES)
    group_associations = serializers.SerializerMethodField()

    class Meta:
        model = SubsidyAccessPolicy
        fields = [
            'uuid',
            'policy_type',
            'display_name',
            'description',
            'active',
            'retired',
            'retired_at',
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
            'group_associations',
            'late_redemption_allowed_until',
            'is_late_redemption_allowed',
        ]
        read_only_fields = ['uuid', 'is_late_redemption_allowed']
        extra_kwargs = {
            'uuid': {'read_only': True},
            'display_name': {
                'min_length': None,
                'max_length': 512,
                'trim_whitespace': True,
            },
            'description': {
                'allow_blank': True,
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
            'late_redemption_allowed_until': {
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

    def get_group_associations(self, obj):
        return list(obj.groups.values_list("enterprise_group_uuid", flat=True))

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
    enterprise_customer_uuid = serializers.UUIDField(
        required=True,
        help_text='The customer for which available policies are filtered.',
    )
    lms_user_id = serializers.IntegerField(
        required=True,
        help_text='The user identifier for which available policies are filtered.',
    )


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
    lms_user_id = serializers.IntegerField(
        required=False,
        help_text='The user identifier for which redeemability will be queried (only applicable to staff users).',
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
            'retired',
            'retired_at',
            'catalog_uuid',
            'subsidy_uuid',
            'access_method',
            'spend_limit',
            'per_learner_spend_limit',
            'per_learner_enrollment_limit',
            'subsidy_active_datetime',
            'subsidy_expiration_datetime',
            'is_subsidy_active',
            'late_redemption_allowed_until',
        )
        extra_kwargs = {
            'display_name': {
                'min_length': None,
                'max_length': 512,
                'trim_whitespace': True,
            },
            'description': {
                'required': False,
                'allow_blank': True,
                'min_length': None,
                'max_length': 200,
                'trim_whitespace': True,
            },
            'active': {
                'allow_null': False,
                'required': False,
            },
            'retired': {
                'allow_null': True,
                'required': False,
            },
            'retired_at': {
                'allow_null': True,
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
            'late_redemption_allowed_until': {
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
    is_late_redemption_allowed = serializers.BooleanField(
        help_text="True if late redemption is currently allowed (i.e. late_redemption_allowed_until is in the future)."
    )

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
    remaining_balance_per_user = serializers.SerializerMethodField(
        help_text='Remaining balance for the requesting user, in USD cents.',
    )
    remaining_balance = serializers.SerializerMethodField(
        help_text='Remaining balance on the entire subsidy, in USD cents.',
    )
    subsidy_expiration_date = serializers.DateTimeField(
        help_text='The date at which the related Subsidy record expires.',
        source='subsidy_expiration_datetime',
    )
    learner_content_assignments = serializers.SerializerMethodField('get_assignments_serializer')

    learner_requests = serializers.SerializerMethodField('get_learner_requests')

    group_associations = serializers.SerializerMethodField()

    @extend_schema_field(LearnerContentAssignmentWithLearnerAcknowledgedResponseSerializer(many=True))
    def get_assignments_serializer(self, obj):
        """
        Return serialized assignments if the policy access method is of the 'assigned' type
        """
        if not obj.is_assignable:
            return []

        assignments = obj.assignment_configuration.assignments.prefetch_related('actions').filter(
            lms_user_id=self.context.get('lms_user_id')
        )
        unacknowledged_assignments_uuids = [
            assignment.uuid
            for assignment in assignments
            if not assignment.learner_acknowledged
        ]
        unacknowledged_assignments = assignments.filter(uuid__in=unacknowledged_assignments_uuids)
        content_metadata_lookup = get_content_metadata_for_assignments(obj.catalog_uuid, unacknowledged_assignments)
        context = {'content_metadata': content_metadata_lookup}
        serializer = LearnerContentAssignmentWithLearnerAcknowledgedResponseSerializer(
            unacknowledged_assignments,
            many=True,
            context=context,
        )
        return serializer.data

    @extend_schema_field(LearnerCreditRequestSerializer(many=True))
    def get_learner_requests(self, obj):
        """
        Return serialized learner credit requests associated with the user and policy's enterprise customer,
        filtered by the specific learner credit request configuration associated with this policy.

        Returns:
            list: Serialized learner credit requests if policy has BNR enabled and user is authenticated.
                Empty list otherwise.
        """
        # Early returns if BNR not enabled or no user ID
        if not obj.bnr_enabled:
            return []

        lms_user_id = self.context.get('lms_user_id')
        if not lms_user_id:
            return []

        learner_requests = LearnerCreditRequest.objects.filter(
            user__lms_user_id=lms_user_id,
            enterprise_customer_uuid=obj.enterprise_customer_uuid,
            state__in=[SubsidyRequestStates.APPROVED, SubsidyRequestStates.REQUESTED],
            learner_credit_request_config=obj.learner_credit_request_config
        )

        if not learner_requests:
            return []

        return LearnerCreditRequestSerializer(learner_requests, many=True).data

    @extend_schema_field(serializers.IntegerField)
    def get_remaining_balance_per_user(self, obj):
        """
        The remaining balance per user for this policy, in USD cents, if applicable.
        """
        if hasattr(obj, 'remaining_balance_per_user'):
            lms_user_id = self.context.get('lms_user_id')
            return obj.remaining_balance_per_user(lms_user_id=lms_user_id)
        return None

    @extend_schema_field(serializers.IntegerField)
    def get_remaining_balance(self, obj):
        """Returns the remaining balance for the policy"""
        return obj.subsidy_balance()

    def get_group_associations(self, obj):
        return list(obj.groups.values_list("enterprise_group_uuid", flat=True))


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
    content_key = ContentKeyField(help_text="Requested content_key to which the rest of this element pertains.")
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
    display_reason = serializers.CharField(
        allow_null=True,
        help_text="A single, user-facing description of the most salient reason for non-redeemability.",
    )


# pylint: disable=abstract-method
class SubsidyAccessPolicyAllocateRequestSerializer(serializers.Serializer):
    """
    Request Serializer to validate policy ``allocate`` endpoint POST data.

    For view: SubsidyAccessPolicyRedeemViewset.allocate
    """
    learner_emails = serializers.ListField(
        child=serializers.EmailField(required=True),
        allow_empty=False,
        help_text='Learner emails to whom LearnerContentAssignments should be allocated.',
    )
    content_key = serializers.CharField(
        required=True,
        help_text='Course content_key to which these learners are assigned.',
    )
    content_price_cents = serializers.IntegerField(
        required=True,
        help_text=(
            'The price, in USD cents, of this content at the time of allocation. Must be >= 0.'
        ),
        min_value=0,
    )


class SubsidyAccessPolicyAllocationResponseSerializer(serializers.Serializer):
    """
    A read-only serializer for responding to request to allocate ``LearnerCotentAssignment`` records.
    """
    updated = LearnerContentAssignmentResponseSerializer(
        many=True,
        help_text='Assignment records whose state was transitioned to "allocated" as a result of this action.',
    )
    created = LearnerContentAssignmentResponseSerializer(
        many=True,
        help_text='New Assignment records that were created as a result of this action.',
    )
    no_change = LearnerContentAssignmentResponseSerializer(
        many=True,
        help_text=(
            'Already-allocated Assignment records related to the requested policy, '
            'learner email(s), and content for this action.'
        ),
    )


class GroupMembersDetailsSerializer(serializers.Serializer):
    """
    Sub-serializer for the response objects associated with the ``get_group_member_data_with_aggregates``
    endpoint. Contains data captured by the ``member_details`` field.
    """
    user_email = serializers.CharField(
        help_text='The email associated with the group member user record.',
    )

    user_name = serializers.CharField(
        required=False,
        help_text='The full (first and last) name of the group member user records. Is blank if the member is pending',
    )


class GroupMemberWithAggregatesResponseSerializer(serializers.Serializer):
    """
    A read-only serializer for responding to requests to the ``get_group_member_data_with_aggregates`` endpoint.

    For view: SubsidyAccessPolicyGroupViewset.get_group_member_data_with_aggregates
    """
    enterprise_customer_user_id = serializers.IntegerField(
        help_text=('LMS enterprise user ID associated with the membership.'),
    )
    lms_user_id = serializers.IntegerField(
        help_text=('LMS auth user ID associated with the membership.'),
    )
    pending_enterprise_customer_user_id = serializers.IntegerField(
        help_text=('LMS pending enterprise user ID associated with the membership.'),
    )
    enterprise_group_membership_uuid = serializers.UUIDField(
        help_text=('The UUID associated with the group membership record.')
    )
    member_details = GroupMembersDetailsSerializer(
        help_text='User record associated with the membership details, including name and email.'
    )
    recent_action = serializers.CharField(
        help_text='String representation of the most recent action associated with the creation of the membership.',
    )
    status = serializers.CharField(
        help_text='String representation of the current membership status.',
    )
    activated_at = serializers.DateTimeField(
        help_text='Datetime at which the membership and user record when from pending to realized.',
    )
    enrollment_count = serializers.IntegerField(
        help_text=(
            'Number of enrollments, made by a group member, that are associated with the subsidy access policy'
            'connected to the group.'
        ),
        min_value=0,
    )


class GroupMemberWithAggregatesRequestSerializer(serializers.Serializer):
    """
    Request Serializer to validate policy group ``get_group_member_data_with_aggregates`` endpoint GET data.

    For view: SubsidyAccessPolicyGroupViewset.get_group_member_data_with_aggregates
    """
    group_uuid = serializers.UUIDField(
        required=False,
        help_text=(
            'The group from which to select members.'
            'Leave blank to fetch group members for every group associated with the policy',
        )
    )
    user_query = serializers.CharField(required=False, max_length=320)
    sort_by = serializers.ChoiceField(
        choices=[
            ('member_details', 'member_details'),
            ('status', 'status'),
            ('recent_action', 'recent_action'),
            (SORT_BY_ENROLLMENT_COUNT, SORT_BY_ENROLLMENT_COUNT),
        ],
        required=False,
        help_text=(
            "Selectable values by which to sort the returned membership records. [Warning]: 'enrollment_count' "
            "will automatically cause incoming requests to wait for all pages of data to be fetched from "
            "edx-enterprise."
        )
    )
    show_removed = serializers.BooleanField(
        required=False,
        help_text="Set to True to fetch and include removed membership records."
    )
    is_reversed = serializers.BooleanField(
        required=False,
        help_text="Set to True to reverse the order in which records are returned."
    )
    format_csv = serializers.BooleanField(
        required=False,
        help_text="Set to True to return data in a csv format."
    )
    page = serializers.IntegerField(
        required=False,
        help_text=('Determines which page of enterprise group members to fetch. Leave blank for all data'),
        min_value=1,
    )
    traverse_pagination = serializers.BooleanField(
        required=False,
        default=False,
        help_text=('Set to True to traverse over and return all group members records across all pages of data.')
    )
    learners = serializers.ListField(
        child=serializers.EmailField(required=True),
        required=False,
    )

    def validate(self, attrs):
        """
        Raises a ValidationError both `traverse_pagination` and `page` are both supplied as they enable conflicting
        behaviors.
        """
        if bool(attrs.get('page')) == bool(attrs.get('traverse_pagination')):
            raise serializers.ValidationError(
                "Can only support one param of the following at a time: `page` or `traverse_pagination`"
            )
        return attrs
