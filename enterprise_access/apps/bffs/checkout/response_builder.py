"""
Response builders for the Checkout BFF endpoints.
"""
from django.conf import settings
from rest_framework import status

from enterprise_access.apps.bffs.checkout.serializers import (
    CheckoutContextResponseSerializer,
    CheckoutValidationResponseSerializer
)
from enterprise_access.apps.bffs.response_builder import BaseResponseBuilder


class CheckoutContextResponseBuilder(BaseResponseBuilder):
    """
    Response builder for the checkout context endpoint.
    """
    serializer_class = CheckoutContextResponseSerializer

    def build(self):
        """
        Build the response data from the context. This specifically does *not*
        call super().build().
        """
        # Create the response structure
        response_data = {
            'existing_customers_for_authenticated_user':
                getattr(self.context, 'existing_customers_for_authenticated_user', []),
            'pricing': getattr(self.context, 'pricing', {
                'default_by_lookup_key': settings.DEFAULT_SSP_PRICE_LOOKUP_KEY,
                'prices': []
            }),
            'field_constraints': getattr(self.context, 'field_constraints', {
                'quantity': {'min': 5, 'max': 30},
                'enterprise_slug': {
                    'min_length': 3,
                    'max_length': 30,
                    'pattern': '^[a-z0-9-]+$'
                }
            })
        }

        # Update the data with the serialized data
        self.response_data.update(response_data)


class CheckoutValidationResponseBuilder(BaseResponseBuilder):
    """
    Response builder for checkout validation endpoint.
    """
    serializer_class = CheckoutValidationResponseSerializer
    ALL_VALIDATION_FIELDS = [
        'full_name',
        'admin_email',
        'company_name',
        'enterprise_slug',
        'quantity',
        'stripe_price_id',
    ]

    def build(self):
        """
        Build the response data.
        """
        # Get validation decisions from context
        validation_decisions = getattr(self.context, 'validation_decisions', {})

        # Add empty entries for fields that weren't validated
        for field in self.ALL_VALIDATION_FIELDS:
            if field not in validation_decisions:
                validation_decisions[field] = None

        # Build the response
        user_authn = getattr(self.context, 'user_authn', None) or {'user_exists_for_email': None}
        self.response_data = {
            'validation_decisions': validation_decisions,
            'user_authn': user_authn,
        }
