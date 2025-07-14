"""
Serializers for the Checkout BFF endpoints.
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
    stripe_customer_id = serializers.CharField()
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
    unit_amount_decimal = serializers.CharField(help_text="Price amount as decimal string")


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
