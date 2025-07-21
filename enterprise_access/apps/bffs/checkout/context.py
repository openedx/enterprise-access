"""
Context classes for the Checkout BFF endpoints.
"""
from enterprise_access.apps.bffs.context import BaseHandlerContext


class CheckoutContext(BaseHandlerContext):
    """
    Context class for checkout-related BFF endpoints.

    Stores data needed for checkout operations including pricing info,
    enterprise customer data, and field constraints.
    """

    def __init__(self, request):
        """
        Initialize the checkout context with a request.

        Args:
            request: The HTTP request
        """
        super().__init__(request)
        self.existing_customers_for_authenticated_user = []
        self.pricing = {}
        self.field_constraints = {}
