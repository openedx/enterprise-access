"""
Enterprise BFFs for MFEs.
"""

from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import get_authorization_header, SessionAuthentication
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.jwt.cookies import jwt_cookie_name

from enterprise_access.apps.bffs.context import HandlerContext
from enterprise_access.apps.bffs.handlers import LearnerPortalHandlerFactory
from enterprise_access.apps.bffs.response_builder import LearnerPortalResponseBuilderFactory


class LearnerPortalBFFAPIView(APIView):
    """
    API view for learner portal BFF routes.
    """

    authentication_classes = [JwtAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, page_route, *args, **kwargs):
        """
        Handles GET requests for learner-specific routes.

        Args:
            request (Request): The request object.
            route (str): The specific learner portal route (e.g., 'dashboard').

        Returns:
            Response: The response data formatted by the response builder.
        """

        # Create the context based on the request
        context = HandlerContext(page_route=page_route, request=request)

        # Use the LearnerPortalResponseBuilderFactory to get the appropriate response builder
        response_builder = LearnerPortalResponseBuilderFactory.get_response_builder(context)

        try:
            # Use the LearnerHandlerFactory to get the appropriate handler
            handler = LearnerPortalHandlerFactory.get_handler(context)

            # Load and process data using the handler
            handler.load_and_process()
        except Exception as exc:
            context.add_error(
                user_message="An error occurred while processing the request.",
                developer_message=f"Error: {exc}",
                severity="error",
            )

        # Build the response data and status code
        response_data, status_code = response_builder.build()

        return Response(response_data, status=status_code)
