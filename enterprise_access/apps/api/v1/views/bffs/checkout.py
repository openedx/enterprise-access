"""
ViewSet for the Checkout BFF endpoints.
"""
import logging

from drf_spectacular.utils import OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import OR, AllowAny, IsAuthenticated
from rest_framework.response import Response

from enterprise_access.apps.api.v1.views.bffs.common import BaseBFFViewSet, BaseUnauthenticatedBFFViewSet
from enterprise_access.apps.bffs.checkout.context import (
    CheckoutContext,
    CheckoutSuccessContext,
    CheckoutValidationContext
)
from enterprise_access.apps.bffs.checkout.handlers import (
    CheckoutContextHandler,
    CheckoutSuccessHandler,
    CheckoutValidationHandler
)
from enterprise_access.apps.bffs.checkout.response_builder import (
    CheckoutContextResponseBuilder,
    CheckoutSuccessResponseBuilder,
    CheckoutValidationResponseBuilder
)
from enterprise_access.apps.bffs.checkout.serializers import (
    CheckoutContextResponseSerializer,
    CheckoutSuccessResponseSerializer,
    CheckoutValidationRequestSerializer,
    CheckoutValidationResponseSerializer
)

logger = logging.getLogger(__name__)


class CheckoutBFFViewSet(BaseUnauthenticatedBFFViewSet):
    """
    ViewSet for checkout-related BFF endpoints.

    These endpoints serve both authenticated and unauthenticated users.
    """
    AUTHENTICATED_ONLY_ACTIONS = ['success']
    authentication_classes = [JwtAuthentication]

    def get_permissions(self):
        """
        Compose authenticated and unauthenticated permissions
        to allow access to both user types, so that we can access ``request.user``
        and get either a hydrated user object from the JWT, or an AnonymousUser
        if not authenticated.
        """
        if self.action in self.AUTHENTICATED_ONLY_ACTIONS:
            return [IsAuthenticated()]
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

    @extend_schema(
        operation_id="checkout_validate_fields",
        summary="Validate checkout form fields",
        description="Validates multiple checkout form fields and returns detailed validation results",
        request=CheckoutValidationRequestSerializer,
        responses={200: CheckoutValidationResponseSerializer},
        tags=["Checkout BFF"],
    )
    @action(detail=False, methods=['post'], url_path='validation')
    def validate(self, request):
        """
        Validate checkout form fields.

        This endpoint supports both authenticated and unauthenticated requests,
        but enterprise_slug validation requires authentication.
        """
        data, status_code = self.load_route_data_and_build_response(
            request,
            CheckoutValidationHandler,
            CheckoutValidationResponseBuilder,
            CheckoutValidationContext,
        )
        return Response(data, status=status_code)

    @extend_schema(
        operation_id="checkout_success",
        summary="Get checkout success data",
        description="Provides relevant data after checkout success",
        responses={200: CheckoutSuccessResponseSerializer},
        tags=["Checkout BFF"],
    )
    @action(detail=False, methods=['post'], url_path='success')
    def success(self, request):
        """
        Handle the checkout success action.

        Returns:
          Response: The success response
        """
        data, status_code = self.load_route_data_and_build_response(
            request,
            CheckoutSuccessHandler,
            CheckoutSuccessResponseBuilder,
            CheckoutSuccessContext,
        )
        return Response(data, status=status_code)
