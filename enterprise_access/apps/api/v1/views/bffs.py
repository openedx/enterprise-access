"""
Enterprise BFFs for MFEs.
"""

from rest_framework.response import Response
from rest_framework.viewsets import ViewSet
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiParameter, OpenApiExample
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication

from enterprise_access.apps.bffs.context import HandlerContext
from enterprise_access.apps.bffs.handlers import DashboardHandler
from enterprise_access.apps.bffs.response_builder import LearnerDashboardResponseBuilder
from enterprise_access.apps.bffs.serializers import LearnerDashboardResponseSerializer


class BaseBFFViewSet(ViewSet):
    """
    Base class for BFF viewsets.
    """

    authentication_classes = [JwtAuthentication]
    permission_classes = [IsAuthenticated]

    def load_route_data_and_build_response(self, request, handler_class, response_builder_class):
        """
        Handles the route and builds the response.
        """
        try:
            # Create the context based on the request
            context = HandlerContext(request=request)

            # Create the response builder
            response_builder = response_builder_class(context)

            # Create the route handler
            handler = handler_class(context)

            # Load and process data using the handler
            handler.load_and_process()
        except Exception as exc:
            context.add_error(
                user_message="An error occurred while processing the request.",
                developer_message=f"Error: {exc}",
            )

        # Build the response data and status code
        return response_builder.build()


class LearnerPortalBFFViewSet(BaseBFFViewSet):
    """
    API view for learner portal BFF routes.
    """

    @extend_schema(
        tags=['Learner Portal BFF'],
        summary='Dashboard route',
        responses={
            200: OpenApiResponse(
                response=LearnerDashboardResponseSerializer,
                description='Sample response for the learner dashboard route.',
            ),
        },
    )
    @action(detail=False, methods=['post'])
    def dashboard(self, request, *args, **kwargs):
        """
        Retrieves, transforms, and processes data for the learner dashboard route.

        Args:
            request (Request): The request object.

        Returns:
            Response: The response data formatted by the response builder.
        """
        response_data, status_code = self.load_route_data_and_build_response(
            request=request,
            handler_class=DashboardHandler,
            response_builder_class=LearnerDashboardResponseBuilder,
        )
        return Response(response_data, status=status_code)
