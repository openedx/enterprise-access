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

            # Create the response builder
            response_builder = response_builder_class(context)

            # Create the route handler
            handler = handler_class(context)

            # Load and process data using the handler
            handler.load_and_process()
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Could not load route data and build response.")
            context.add_error(
                user_message="An error occurred while processing the request.",
                developer_message=f"Error: {exc}",
            )

        # Build the response data and status code
        response_data, status_code = response_builder.build()

        ordered_representation = OrderedDict(response_data)

        # Remove errors and warnings from the response, and add them back at the end
        errors = ordered_representation.pop('errors', [])
        warnings = ordered_representation.pop('warnings', [])
        ordered_representation['errors'] = errors
        ordered_representation['warnings'] = warnings

        return ordered_representation, status_code
