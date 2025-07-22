"""
Serializers for the checkout bff.
"""
from rest_framework import serializers


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
