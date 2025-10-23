"""
customer billing serializers
"""
from django_countries.serializers import CountryFieldMixin
from rest_framework import serializers
from rest_framework.exceptions import APIException

from enterprise_access.apps.customer_billing.constants import ALLOWED_CHECKOUT_INTENT_STATE_TRANSITIONS
from enterprise_access.apps.customer_billing.embargo import get_embargoed_countries
from enterprise_access.apps.customer_billing.models import (
    CheckoutIntent,
    FailedCheckoutIntentConflict,
    SlugReservationConflict,
    StripeEventSummary
)


class RecordConflictError(APIException):
    """
    Raise this to trigger HTTP 422, as an alternative to ValidationError (HTTP 400).

    422 more accurately describes a conflicting database record, whereas 400 means there's an issue
    with the query structure or values.
    """
    status_code = 422
    default_detail = 'Encountered a conflicting record.'
    default_code = 'record_conflict_error'


# pylint: disable=abstract-method
class CustomerBillingCreateCheckoutSessionRequestSerializer(serializers.Serializer):
    """
    Request serializer for body of POST requests to /api/v1/customer-billing/create-checkout-session
    """
    admin_email = serializers.EmailField(
        required=True,
        help_text='The email corresponding to a registered user to assign as admin.',
    )
    enterprise_slug = serializers.SlugField(
        required=True,
        help_text='The unique slug proposed for the Enterprise Customer.',
    )
    company_name = serializers.CharField(
        required=True,
        help_text='The unique name proposed for the Enterprise Customer.',
    )
    quantity = serializers.IntegerField(
        required=True,
        min_value=1,
        help_text=(
            'Unit depends on the Stripe Price object. '
            'This could be count of subscription licenses, but could also be USD of Learner Credit.'
        )
    )
    stripe_price_id = serializers.CharField(
        required=True,
        help_text='The ID of the Stripe Price object representing the plan selection.',
    )


# pylint: disable=abstract-method
class CustomerBillingCreateCheckoutSessionSuccessResponseSerializer(serializers.Serializer):
    """
    Response serializer for response body from POST /api/v1/customer-billing/create-checkout-session

    Specifically for HTTP 201 CREATED responses.
    """
    checkout_session_client_secret = serializers.CharField(
        required=True,
        help_text=(
            'Secret identifier for the newly created Stripe checkout session. Pass this to the '
            'frontend stripe component.'
        ),
    )


class FieldValidationSerializer(serializers.Serializer):
    """
    Common pattern for serialized field validation errors.
    """
    error_code = serializers.CharField(
        required=True,
        help_text='Error code for validation failure.',
    )
    developer_message = serializers.CharField(
        required=True,
        help_text='System message (not intended for user display) for validation failure.',
    )


class UnprocessableEntityErrorSerializer(serializers.Serializer):
    """
    Common pattern for serialized field validation errors.
    """
    error_code = serializers.CharField(
        required=True,
        help_text='Error code for validation failure.',
    )
    developer_message = serializers.CharField(
        required=True,
        help_text='System message (not intended for user display) for validation failure.',
    )


# pylint: disable=abstract-method
class CustomerBillingCreateCheckoutSessionValidationFailedResponseSerializer(serializers.Serializer):
    """
    Response serializer for response body from POST /api/v1/customer-billing/create-checkout-session

    Specifically for HTTP 422 UNPROCESSABLE ENTITY responses.
    """
    admin_email = FieldValidationSerializer(
        required=False,
        help_text='Validation results for admin_email if validation failed. Absent otherwise.',
    )
    enterprise_slug = FieldValidationSerializer(
        required=False,
        help_text='Validation results for enterprise_slug if validation failed. Absent otherwise.',
    )
    quantity = FieldValidationSerializer(
        required=False,
        help_text='Validation results for quantity if validation failed. Absent otherwise.',
    )
    stripe_price_id = FieldValidationSerializer(
        required=False,
        help_text='Validation results for stripe_price_id if validation failed. Absent otherwise.',
    )
    errors = UnprocessableEntityErrorSerializer(
        required=False,
        many=True,
        help_text='Errors if ChekoutIntent creation failed for non-field-specific reasons. Absent otherwise.',
    )


class CheckoutIntentReadOnlySerializer(CountryFieldMixin, serializers.ModelSerializer):
    """
    Serializer for reading and updating CheckoutIntent model instances.
    """

    class Meta:
        model = CheckoutIntent
        fields = '__all__'
        read_only_fields = [field.name for field in CheckoutIntent._meta.get_fields()]


class CheckoutIntentUpdateRequestSerializer(CountryFieldMixin, serializers.ModelSerializer):
    """
    Write serializer for CheckoutIntent - used for PATCH operations.
    """

    class Meta:
        model = CheckoutIntent
        fields = '__all__'
        read_only_fields = [
            field.name for field in CheckoutIntent._meta.get_fields()
            if field.name not in ('state', 'country', 'terms_metadata')
        ]

    def validate_state(self, value):
        """
        Validate that the state transition is allowed.
        """
        instance = self.instance
        if instance:
            current_state = instance.state
            if (current_state != value) and \
               (value not in ALLOWED_CHECKOUT_INTENT_STATE_TRANSITIONS.get(current_state, [])):
                raise serializers.ValidationError(
                    f'Invalid state transition from {current_state} to {value}'
                )

        return value

    def validate_country(self, value):
        """
        Reject embargoed countries.
        """

        if value and value in get_embargoed_countries():
            raise serializers.ValidationError(
                f'Country {value} is not supported.'
            )
        return value

    def validate_terms_metadata(self, value):
        """
        Validate that terms_metadata is a dictionary/object, not a list or string.
        """
        if value is not None and not isinstance(value, dict):
            raise serializers.ValidationError(
                'terms_metadata must be a dictionary/object, not a list or string.'
            )
        return value


class CheckoutIntentCreateRequestSerializer(CountryFieldMixin, serializers.ModelSerializer):
    """
    A serializer intended for creating new CheckoutIntents.
    """
    class Meta:
        model = CheckoutIntent
        fields = '__all__'
        read_only_fields = [
            field.name for field in CheckoutIntent._meta.get_fields()
            if field.name not in [
                'enterprise_slug',
                'enterprise_name',
                'quantity',
                'country',
                'terms_metadata',
            ]
        ]

    # Put some reasonable validation bounds at this layer, and let
    # the customer_billing.api business logic handle more detailed validation
    quantity = serializers.IntegerField(min_value=1, max_value=1000)

    def validate_terms_metadata(self, value):
        """
        Validate that terms_metadata is a dictionary/object, not a list or string.
        """
        if value is not None and not isinstance(value, dict):
            raise serializers.ValidationError(
                'terms_metadata must be a dictionary/object, not a list or string.'
            )
        return value

    def validate(self, attrs):
        """
        Perform any cross-field validation.
        """
        if attrs.get('enterprise_slug') and not attrs.get('enterprise_name'):
            raise serializers.ValidationError(
                {'enterprise_name': 'enterprise_name is required when enterprise_slug is provided.'}
            )
        if attrs.get('enterprise_name') and not attrs.get('enterprise_slug'):
            raise serializers.ValidationError(
                {'enterprise_slug': 'enterprise_slug is required when enterprise_name is provided.'}
            )
        return attrs

    def create(self, validated_data):
        """
        Creates a new CheckoutIntent.
        """
        try:
            return CheckoutIntent.create_intent(
                user=self.context['request'].user,
                quantity=validated_data['quantity'],
                slug=validated_data.get('enterprise_slug'),
                name=validated_data.get('enterprise_name'),
                country=validated_data.get('country'),
                terms_metadata=validated_data.get('terms_metadata'),
            )

        # Catch exceptions that should return 422:
        except SlugReservationConflict as exc:
            raise RecordConflictError('enterprise_slug or enterprise_name has already been reserved.') from exc
        except FailedCheckoutIntentConflict as exc:
            raise RecordConflictError('Requesting user already has a failed CheckoutIntent.') from exc

        # All other exceptions should return 5xx.


class StripeEventSummaryReadOnlySerializer(serializers.ModelSerializer):
    """
    Serializer for reading StripeEventSummary model instances.
    """
    class Meta:
        model = StripeEventSummary
        fields = '__all__'
        read_only_fields = [field.name for field in StripeEventSummary._meta.get_fields()]
