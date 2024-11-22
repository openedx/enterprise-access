"""
Enterprise BFFs for MFEs.
"""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from edx_rbac.decorators import permission_required
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from enterprise_access.apps.api.utils import get_bff_enterprise_customer_uuid
from enterprise_access.apps.api.v1.views.bffs.common import COMMON_BFF_QUERY_PARAMETERS, BaseBFFViewSet
from enterprise_access.apps.bffs.handlers import DashboardHandler
from enterprise_access.apps.bffs.response_builder import LearnerDashboardResponseBuilder
from enterprise_access.apps.bffs.serializers import (
    LearnerDashboardRequestSerializer,
    LearnerDashboardResponseSerializer
)
from enterprise_access.apps.core.constants import BFF_READ_PERMISSION


class LearnerPortalBFFViewSet(BaseBFFViewSet):
    """
    API view for learner portal BFF routes.
    """

    @extend_schema(
        tags=['Learner Portal BFF'],
        summary='Dashboard route',
        request=LearnerDashboardRequestSerializer,
        parameters=COMMON_BFF_QUERY_PARAMETERS,
        responses={
            status.HTTP_200_OK: OpenApiResponse(
                response=LearnerDashboardResponseSerializer,
                description='Sample response for the learner dashboard route.',
            ),
        },
        description='Retrieves, transforms, and processes data for the learner dashboard route.',
    )
    @action(detail=False, methods=['post'])
    @permission_required(BFF_READ_PERMISSION, fn=get_bff_enterprise_customer_uuid)
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
