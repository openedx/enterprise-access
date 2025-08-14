"""
Serializers for the checkout bff.
"""
from rest_framework import serializers

from enterprise_access.apps.customer_billing.constants import CheckoutIntentState
from enterprise_access.apps.customer_billing.models import CheckoutIntent


# pylint: disable=abstract-method
class EnterpriseCustomerSerializer(serializers.Serializer):
    """
    Serializer for enterprise customer data in checkout context.
    """
    customer_uuid = serializers.CharField()
    customer_name = serializers.CharField()
    customer_slug = serializers.CharField()
    stripe_customer_id = serializers.CharField(required=False, allow_blank=True)
    is_self_service = serializers.BooleanField(default=False)
    admin_portal_url = serializers.CharField()


class PriceSerializer(serializers.Serializer):
    """
    Serializer for Stripe price objects in checkout context.
    """
    id = serializers.CharField(help_text="Stripe Price ID")
    product = serializers.CharField(help_text="Stripe Product ID")
    lookup_key = serializers.CharField(help_text="Lookup key for this price")
    recurring = serializers.DictField(
        help_text="Recurring billing configuration"
    )
    currency = serializers.CharField(help_text="Currency code (e.g. 'usd')")
    unit_amount = serializers.IntegerField(help_text="Price amount in cents")
    unit_amount_decimal = serializers.DecimalField(
        max_digits=8,
        decimal_places=2,
        help_text="Price amount as decimal",
    )


class PricingDataSerializer(serializers.Serializer):
    """
    Serializer for pricing data in checkout context.
    """
    default_by_lookup_key = serializers.CharField(
        help_text="Lookup key for the default price option"
    )
    prices = PriceSerializer(many=True, help_text="Available price options")


class QuantityConstraintSerializer(serializers.Serializer):
    """
    Serializer for quantity constraints.
    """
    min = serializers.IntegerField(help_text="Minimum allowed quantity")
    max = serializers.IntegerField(help_text="Maximum allowed quantity")


class SlugConstraintSerializer(serializers.Serializer):
    """
    Serializer for enterprise slug constraints.
    """
    min_length = serializers.IntegerField(help_text="Minimum slug length")
    max_length = serializers.IntegerField(help_text="Maximum slug length")
    pattern = serializers.CharField(help_text="Regex pattern for valid slugs")


class FieldConstraintsSerializer(serializers.Serializer):
    """
    Serializer for field constraints in checkout context.

    TODO: the field constraints should be expanded to more closely match the mins/maxes within this code block:
    https://github.com/edx/frontend-app-enterprise-checkout/blob/main/src/constants.ts#L13-L39
    """
    quantity = QuantityConstraintSerializer(help_text="Constraints for license quantity")
    enterprise_slug = SlugConstraintSerializer(help_text="Constraints for enterprise slug")


class CheckoutIntentModelSerializer(serializers.ModelSerializer):
    """
    Model serializer to help convert CheckoutIntent objects to dicts
    in the course of response building and other internal data transformations.
    """
    admin_portal_url = serializers.CharField(read_only=True, required=False, allow_blank=True)

    class Meta:
        model = CheckoutIntent
        fields = '__all__'


class CheckoutIntentMinimalResponseSerializer(serializers.Serializer):
    """
    Minimal serializer to represent CheckoutIntent records in BFF response payloads.
    """
    id = serializers.IntegerField(
        help_text='CheckoutIntent id',
    )
    state = serializers.ChoiceField(
        help_text='The current state of this record',
        choices=CheckoutIntentState,
    )
    enterprise_name = serializers.CharField(
        help_text='The enterprise name associated with this record', required=False,
    )
    enterprise_slug = serializers.CharField(
        help_text='The enterprise slug associated with this record', required=False,
    )
    stripe_checkout_session_id = serializers.CharField(
        help_text='The stripe checkout session id for this intent',
        required=False,
        allow_null=True,
    )
    last_checkout_error = serializers.CharField(
        help_text='The last checkout error related to this intent',
        required=False,
        allow_blank=True,
    )
    last_provisioning_error = serializers.CharField(
        help_text='The last provisioning error related to this intent',
        required=False,
        allow_blank=True,
    )
    workflow_id = serializers.CharField(
        help_text='The workflow id related to this intent',
        required=False,
        allow_null=True,
    )
    expires_at = serializers.DateTimeField(
        help_text='The expiration time of this intent',
    )
    admin_portal_url = serializers.CharField(
        help_text='The admin portal URL related to this intent',
        required=False,
        allow_null=True,
    )


class CheckoutContextResponseSerializer(serializers.Serializer):
    """
    Serializer for the checkout context response.
    """
    existing_customers_for_authenticated_user = EnterpriseCustomerSerializer(
        many=True,
        help_text="Enterprise customers associated with the authenticated user (empty for unauthenticated users)"
    )
    pricing = PricingDataSerializer(help_text="Available pricing options")
    field_constraints = FieldConstraintsSerializer(help_text="Constraints for form fields")
    checkout_intent = CheckoutIntentMinimalResponseSerializer(
        required=False,
        allow_null=True,
        help_text="The existing ``CheckoutIntent`` for the requesting user, if any",
    )


# BFF Validation Serializers #


class ValidationDecisionSerializer(serializers.Serializer):
    """
    Serializer for individual validation decisions.
    """
    error_code = serializers.CharField(help_text="Error code for the validation failure")
    developer_message = serializers.CharField(help_text="Technical message describing the validation failure")


class CheckoutValidationRequestSerializer(serializers.Serializer):
    """
    Request serializer for the checkout validation endpoint.
    """
    full_name = serializers.CharField(required=False, allow_blank=True, help_text="User's full name")
    admin_email = serializers.EmailField(required=False, allow_blank=True, help_text="User's work email")
    company_name = serializers.CharField(required=False, allow_blank=True, help_text="Company name")
    enterprise_slug = serializers.SlugField(required=False, allow_blank=True, help_text="Desired enterprise slug")
    quantity = serializers.IntegerField(required=False, allow_null=True, help_text="Number of licenses")
    stripe_price_id = serializers.CharField(required=False, allow_blank=True, help_text="Stripe price ID")


class UserAuthInfoSerializer(serializers.Serializer):
    """
    Serializer for user authentication status info.
    """
    user_exists_for_email = serializers.BooleanField(
        allow_null=True,
        help_text="Whether a user exists for the provided email"
    )


class CheckoutValidationResponseSerializer(serializers.Serializer):
    """
    Response serializer for the checkout validation endpoint.
    """
    validation_decisions = serializers.DictField(
        child=ValidationDecisionSerializer(allow_null=True),
        help_text="Validation results for each field"
    )
    user_authn = UserAuthInfoSerializer(help_text="User authentication status information")


# BFF Checkout Success Serializers #


class BillingAddressSerializer(serializers.Serializer):
    """Serializer for billing address information."""
    city = serializers.CharField(allow_null=True, allow_blank=True)
    country = serializers.CharField(allow_null=True, allow_blank=True)
    line1 = serializers.CharField(allow_null=True, allow_blank=True)
    line2 = serializers.CharField(allow_null=True, allow_blank=True)
    postal_code = serializers.CharField(allow_null=True, allow_blank=True)
    state = serializers.CharField(allow_null=True, allow_blank=True)


class FirstBillableInvoiceSerializer(serializers.Serializer):
    """Serializer for first billable invoice information."""
    start_time = serializers.DateTimeField(allow_null=True)
    end_time = serializers.DateTimeField(allow_null=True)
    last4 = serializers.IntegerField(allow_null=True)
    quantity = serializers.IntegerField(allow_null=True)
    unit_amount_decimal = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    customer_phone = serializers.CharField(allow_null=True, allow_blank=True)
    customer_name = serializers.CharField(allow_null=True, allow_blank=True)
    billing_address = BillingAddressSerializer(allow_null=True)


class CheckoutIntentExpandedSerializer(CheckoutIntentMinimalResponseSerializer):
    """
    Serializes checkout intent data, expanded to included related
    stripe object data.
    """
    first_billable_invoice = FirstBillableInvoiceSerializer(allow_null=True)


class CheckoutSuccessResponseSerializer(CheckoutContextResponseSerializer):
    """Complete serializer for checkout success intent."""
    checkout_intent = CheckoutIntentExpandedSerializer(
        required=False,
        allow_null=True,
        help_text=(
            "The existing ``CheckoutIntent`` for the requesting user, if any. "
            "Includes expanded information from related Stripe records."
        ),
    )
