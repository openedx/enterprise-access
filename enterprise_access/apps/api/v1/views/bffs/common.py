"""
Base classes for BFF views.
"""

import logging
from collections import OrderedDict

from drf_spectacular.utils import OpenApiParameter, OpenApiTypes
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ViewSet

from enterprise_access.apps.bffs.context import HandlerContext
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
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Could not instantiate the handler context for the request.")
            error = {
                'user_message': 'An error occurred while processing the request.',
                'developer_message': f'Could not create the handler context. Error: {exc}',
            }
            response_data = BaseResponseSerializer({'errors': [error]}).data
            return response_data, status.HTTP_500_INTERNAL_SERVER_ERROR

        try:
            # Create the route handler
            handler = handler_class(context)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception(
                "Could not instantiate route handler (%s) for request user %s and enterprise customer %s.",
                handler_class.__name__,
                context.lms_user_id,
                context.enterprise_customer_uuid,
            )
            context.add_error(
                user_message='An error occurred while processing the request.',
                developer_message=f'Could not instantiate route handler ({handler_class.__name__}). Error: {exc}',
            )

        try:
            # Load and process route data
            handler.load_and_process()
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception(
                "Could not load/process route handler (%s) for request user %s and enterprise customer %s.",
                handler_class.__name__,
                context.lms_user_id,
                context.enterprise_customer_uuid,
            )
            context.add_error(
                user_message='An error occurred while processing the request.',
                developer_message=(
                    f'Could not load/process data for route handler ({handler_class.__name__}). Error: {exc}',
                ),
            )

        # Build the response data and status code
        response_builder = response_builder_class(context)
        response_data, status_code = response_builder.build()

        ordered_representation = OrderedDict(response_data)

        # Remove errors/warnings & enterprise_features from the response, and add them back at the end
        errors = ordered_representation.pop('errors', [])
        warnings = ordered_representation.pop('warnings', [])
        enterprise_features = ordered_representation.pop('enterprise_features', {})
        ordered_representation['errors'] = errors
        ordered_representation['warnings'] = warnings
        ordered_representation['enterprise_features'] = enterprise_features

        return dict(ordered_representation), status_code
