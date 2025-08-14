"""
Context classes for the Checkout BFF endpoints.
"""
from django.conf import settings

from enterprise_access.apps.bffs.context import BaseHandlerContext


class CheckoutContext(BaseHandlerContext):
    """
    Context class for checkout-related BFF endpoints.

    Stores data needed for checkout operations including pricing info,
    enterprise customer data, and field constraints.
    """

    @property
    def existing_customers_for_authenticated_user(self):
        return self.data.get('existing_customers_for_authenticated_user', [])

    @existing_customers_for_authenticated_user.setter
    def existing_customers_for_authenticated_user(self, value):
        self.data['existing_customers_for_authenticated_user'] = value

    @property
    def pricing(self):
        return self.data.get('pricing', {
            'default_by_lookup_key': settings.DEFAULT_SSP_PRICE_LOOKUP_KEY,
            'prices': []
        })

    @pricing.setter
    def pricing(self, value):
        self.data['pricing'] = value

    @property
    def field_constraints(self):
        return self.data.get('field_constraints', {
            'quantity': {'min': 5, 'max': 30},
            'enterprise_slug': {
                'min_length': 3,
                'max_length': 30,
                'pattern': '^[a-z0-9-]+$'
            }
        })

    @field_constraints.setter
    def field_constraints(self, value):
        self.data['field_constraints'] = value

    @property
    def checkout_intent(self):
        return self.data.get('checkout_intent', None)

    @checkout_intent.setter
    def checkout_intent(self, value):
        self.data['checkout_intent'] = value


class CheckoutValidationContext(BaseHandlerContext):
    """
    Context class for checkout validation BFF endpoint.
    """

    @property
    def validation_decisions(self):
        return self.data.get('validation_decisions', {})

    @validation_decisions.setter
    def validation_decisions(self, value):
        self.data['validation_decisions'] = value

    @property
    def user_authn(self):
        return self.data.get('user_authn', {})

    @user_authn.setter
    def user_authn(self, value):
        self.data['user_authn'] = value


class CheckoutSuccessContext(CheckoutContext):
    """
    Context class for checkout success BFF endpoint.
    This is the same structure as ``CheckoutContext``, only the contained
    checkout intent record will be expanded with additional data
    via the Stripe API.
    """
