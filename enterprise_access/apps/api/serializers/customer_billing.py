"""
customer billing serializers
"""
from django_countries.serializers import CountryFieldMixin
from rest_framework import serializers

from enterprise_access.apps.customer_billing.constants import ALLOWED_CHECKOUT_INTENT_STATE_TRANSITIONS
from enterprise_access.apps.customer_billing.models import CheckoutIntent


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
    Only allows state field updates.
    """

    class Meta:
        model = CheckoutIntent
        fields = '__all__'
        read_only_fields = [
            field.name for field in CheckoutIntent._meta.get_fields()
            if field.name not in ('state', 'country')
        ]

    def validate_state(self, value):
        """
        Validate that the state transition is allowed.
        """
        instance = self.instance
        if instance:
            current_state = instance.state
            if value not in ALLOWED_CHECKOUT_INTENT_STATE_TRANSITIONS.get(current_state, []):
                raise serializers.ValidationError(
                    f'Invalid state transition from {current_state} to {value}'
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
            ]
        ]

    # Put some reasonable validation bounds at this layer, and let
    # the customer_billing.api business logic handle more detailed validation
    quantity = serializers.IntegerField(min_value=1, max_value=1000)

    def create(self, validated_data):
        """
        Creates a new CheckoutIntent.
        """
        return CheckoutIntent.create_intent(
            user=self.context['request'].user,
            slug=validated_data['enterprise_slug'],
            name=validated_data['enterprise_name'],
            quantity=validated_data['quantity'],
            country=validated_data.get('country'),
        )
