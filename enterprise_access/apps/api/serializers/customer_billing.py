"""
customer billing serializers
"""

from rest_framework import serializers


# pylint: disable=abstract-method
class CustomerBillingCreatePlanRequestSerializer(serializers.Serializer):
    """
    Request serializer for body of POST requests to /api/v1/customer-billing/create-plan
    """
    email = serializers.EmailField(required=True)
    slug = serializers.SlugField(required=True)
    num_licenses = serializers.IntegerField(required=True, min_value=1)
    stripe_price_id = serializers.CharField(required=True)
