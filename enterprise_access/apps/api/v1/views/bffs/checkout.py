"""
ViewSet for the Checkout BFF endpoints.
"""
import logging

from drf_spectacular.utils import OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import OR, AllowAny, IsAuthenticated
from rest_framework.response import Response

from enterprise_access.apps.api.v1.views.bffs.common import BaseUnauthenticatedBFFViewSet
from enterprise_access.apps.bffs.checkout.context import CheckoutContext
from enterprise_access.apps.bffs.checkout.handlers import CheckoutContextHandler
from enterprise_access.apps.bffs.checkout.response_builder import CheckoutContextResponseBuilder
from enterprise_access.apps.bffs.checkout.serializers import CheckoutContextResponseSerializer

logger = logging.getLogger(__name__)


class CheckoutBFFViewSet(BaseUnauthenticatedBFFViewSet):
    """
    ViewSet for checkout-related BFF endpoints.

    These endpoints serve both authenticated and unauthenticated users.
    """

    authentication_classes = [JwtAuthentication]

    def get_permissions(self):
        """
        Compose authenticated and unauthenticated permissions
        to allow access to both user types, so that we can access ``request.user``
        and get either a hydrated user object from the JWT, or an AnonymousUser
        if not authenticated.
        """
        return [OR(IsAuthenticated(), AllowAny())]

    @extend_schema(
        operation_id="checkout_context",
        summary="Get checkout context",
        description=(
            "Provides context information for the checkout flow, including pricing options "
            "and, for authenticated users, associated enterprise customers."
        ),
        responses={
            200: OpenApiResponse(
                description="Success response with checkout context data.",
                response=CheckoutContextResponseSerializer,
            ),
        },
        tags=["Checkout BFF"],
    )
    @action(detail=False, methods=["post"], url_path="context")
    def context(self, request):
        """
        Provides context information for the checkout flow.

        This includes pricing options for self-service subscription plans and,
        for authenticated users, information about associated enterprise customers.
        """
        # Import handlers here to avoid circular imports

        response_data, status_code = self.load_route_data_and_build_response(
            request,
            CheckoutContextHandler,
            CheckoutContextResponseBuilder,
            CheckoutContext,
        )

        return Response(response_data, status=status_code)
