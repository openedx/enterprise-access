"""
Base classes for BFF views.
"""
import logging
from collections import OrderedDict
from datetime import datetime

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.viewsets import ViewSet

from enterprise_access.apps.bffs.context import BaseHandlerContext, HandlerContext
from enterprise_access.apps.bffs.serializers import BaseResponseSerializer

logger = logging.getLogger(__name__)


COMMON_BFF_QUERY_PARAMETERS = [
    OpenApiParameter(
        name='enterprise_customer_uuid',
        description='The UUID of the enterprise customer.',
        type=OpenApiTypes.UUID,
        required=False,
        location=OpenApiParameter.QUERY,
    ),
    OpenApiParameter(
        name='enterprise_customer_slug',
        description='The slug of the enterprise customer.',
        required=False,
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
    ),
]


class BaseBFFViewSetMixin:
    """
    Mixin class containing common BFF viewset functionality.
    """

    def _create_context(self, request, context_class):
        """
        Creates the appropriate context for the request.

        Args:
            request: The incoming HTTP request
            context_class: The context class to instantiate

        Returns:
            tuple: (context_instance, error_response_data, error_status_code)
            If successful, error_response_data and error_status_code will be None
        """
        try:
            context = context_class(request=request)
            return context, None, None
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Could not instantiate the handler context for the request.")
            error = {
                'user_message': 'An error occurred while processing the request.',
                'developer_message': f'Could not create the handler context. Error: {exc}',
            }
            response_data = BaseResponseSerializer({'errors': [error]}).data
            return None, response_data, status.HTTP_500_INTERNAL_SERVER_ERROR

    def _instantiate_handler(self, handler_class, context):
        """
        Instantiates the route handler.

        Args:
            handler_class: The handler class to instantiate
            context: The context instance

        Returns:
            handler instance or None if failed
        """
        try:
            return handler_class(context)
        except Exception as exc:  # pylint: disable=broad-except
            # Get user ID safely - may not exist for unauthenticated contexts
            user_id = getattr(context, 'lms_user_id', 'unauthenticated')
            enterprise_uuid = getattr(context, 'enterprise_customer_uuid', 'unknown')

            logger.exception(
                "Could not instantiate route handler (%s) for request user %s and enterprise customer %s.",
                handler_class.__name__,
                user_id,
                enterprise_uuid,
            )
            context.add_error(
                user_message='An error occurred while processing the request.',
                developer_message=f'Could not instantiate route handler ({handler_class.__name__}). Error: {exc}',
            )
            return None

    def _process_handler(self, handler, handler_class, context):
        """
        Processes the route handler.

        Args:
            handler: The handler instance
            handler_class: The handler class (for logging)
            context: The context instance
        """
        if not handler:
            return

        try:
            handler.load_and_process()
        except Exception as exc:  # pylint: disable=broad-except
            # Get user ID safely - may not exist for unauthenticated contexts
            user_id = getattr(context, 'lms_user_id', 'unauthenticated')
            enterprise_uuid = getattr(context, 'enterprise_customer_uuid', 'unknown')

            logger.exception(
                "Could not load/process route handler (%s) for request user %s and enterprise customer %s.",
                handler_class.__name__,
                user_id,
                enterprise_uuid,
            )
            context.add_error(
                user_message='An error occurred while processing the request.',
                developer_message=(
                    f'Could not load/process data for route handler ({handler_class.__name__}). Error: {exc}',
                ),
            )

    def _build_response(self, context, response_builder_class):
        """
        Builds the response data and status code.

        Args:
            context: The context instance
            response_builder_class: The response builder class

        Returns:
            tuple: (response_data, status_code)
        """
        response_builder = response_builder_class(context)
        response_builder.build()
        response_data, status_code = response_builder.serialize()

        ordered_representation = OrderedDict(response_data)

        # Remove errors/warnings & enterprise_features from the response, and add them back at the end
        errors = ordered_representation.pop('errors', [])
        warnings = ordered_representation.pop('warnings', [])
        enterprise_features = ordered_representation.pop('enterprise_features', {})
        ordered_representation['errors'] = errors
        ordered_representation['warnings'] = warnings
        ordered_representation['enterprise_features'] = enterprise_features

        return dict(ordered_representation), status_code

    def load_route_data_and_build_response(self, request, handler_class, response_builder_class, context_class):
        """
        Handles the route and builds the response with the specified context class.

        Args:
            request: The incoming HTTP request
            handler_class: The handler class to use
            response_builder_class: The response builder class to use
            context_class: The context class to use

        Returns:
            tuple: (response_data, status_code)
        """
        # Create the context based on the request
        context, error_response, error_status = self._create_context(request, context_class)
        if context is None:
            return error_response, error_status

        # Create and process the route handler
        handler = self._instantiate_handler(handler_class, context)
        self._process_handler(handler, handler_class, context)

        # Build and return the response
        return self._build_response(context, response_builder_class)


class BaseBFFViewSet(BaseBFFViewSetMixin, ViewSet):
    """
    Base class for authenticated BFF viewsets.
    """

    authentication_classes = [JwtAuthentication]
    permission_classes = [IsAuthenticated]

    def load_route_data_and_build_response(self, request, handler_class, response_builder_class):
        """
        Handles the route and builds the response using HandlerContext for authenticated requests.
        """
        return super().load_route_data_and_build_response(
            request, handler_class, response_builder_class, HandlerContext
        )


class BFFAnonRateThrottle(AnonRateThrottle):
    """
    Custom anonymous rate throttle for BFF endpoints.
    """
    scope = 'bff_unauthenticated'


class BaseUnauthenticatedBFFViewSet(BaseBFFViewSetMixin, ViewSet):
    """
    Base class for unauthenticated BFF viewsets.
    Uses BaseHandlerContext which doesn't store customer or user data.
    """

    authentication_classes = []
    permission_classes = []
    throttle_classes = [BFFAnonRateThrottle]

    def load_route_data_and_build_response(self, request, handler_class, response_builder_class, context_class=None):
        """
        Handles the route and builds the response using BaseHandlerContext for unauthenticated requests.
        """
        return super().load_route_data_and_build_response(
            request, handler_class, response_builder_class, context_class or BaseHandlerContext
        )


class PingViewSet(BaseUnauthenticatedBFFViewSet):
    """
    Simple ping viewset for health checks and unauthenticated service availability testing.
    """

    @extend_schema(
        operation_id='ping_health_check',
        summary='Health Check Ping',
        description='Simple ping endpoint to check if the BFF service is running and responsive.',
        responses={
            200: {
                'type': 'object',
                'properties': {
                    'message': {'type': 'string'},
                    'timestamp': {'type': 'string', 'format': 'date-time'},
                    'status': {'type': 'string'},
                    'service': {'type': 'string'},
                },
                'example': {
                    'message': 'pong',
                    'timestamp': '2025-06-11T15:31:27Z',
                    'status': 'healthy',
                    'service': 'enterprise-access-bff'
                }
            }
        },
        tags=['Health Check']
    )
    @action(detail=False, methods=['get'], url_path='ping')
    def ping(self, request):
        """
        Simple ping endpoint that returns a pong response.

        This endpoint can be used for:
        - Health checks
        - Service availability monitoring
        - Load balancer health probes
        - Basic connectivity testing
        """
        response_data = {
            'message': 'pong',
            'timestamp': timezone.now().isoformat() + 'Z',
            'status': 'healthy',
            'service': 'enterprise-access-bff',
        }

        return Response(response_data, status=status.HTTP_200_OK)
