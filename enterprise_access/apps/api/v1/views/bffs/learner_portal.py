"""
Enterprise BFFs for MFEs.
"""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from edx_rbac.decorators import permission_required
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from enterprise_access.apps.api.utils import get_or_fetch_enterprise_uuid_for_bff_request
from enterprise_access.apps.api.v1.views.bffs.common import COMMON_BFF_QUERY_PARAMETERS, BaseBFFViewSet
from enterprise_access.apps.bffs.handlers import AcademyHandler, DashboardHandler, SearchHandler, SkillsQuizHandler
from enterprise_access.apps.bffs.response_builder import (
    LearnerAcademyResponseBuilder,
    LearnerDashboardResponseBuilder,
    LearnerSearchResponseBuilder,
    LearnerSkillsQuizResponseBuilder
)
from enterprise_access.apps.bffs.serializers import (
    LearnerAcademyRequestSerializer,
    LearnerAcademyResponseSerializer,
    LearnerDashboardRequestSerializer,
    LearnerDashboardResponseSerializer,
    LearnerSearchRequestSerializer,
    LearnerSearchResponseSerializer,
    LearnerSkillsQuizRequestSerializer,
    LearnerSkillsQuizResponseSerializer
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
    @permission_required(BFF_READ_PERMISSION, fn=get_or_fetch_enterprise_uuid_for_bff_request)
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

    @extend_schema(
        tags=['Learner Portal BFF'],
        summary='Search route',
        request=LearnerSearchRequestSerializer,
        parameters=COMMON_BFF_QUERY_PARAMETERS,
        responses={
            status.HTTP_200_OK: OpenApiResponse(
                response=LearnerSearchResponseSerializer,
                description='Sample response for the learner search route.',
            ),
        },
        description='Retrieves, transforms, and processes data for the learner search route.',
    )
    @action(detail=False, methods=['post'])
    @permission_required(BFF_READ_PERMISSION, fn=get_or_fetch_enterprise_uuid_for_bff_request)
    def search(self, request, *args, **kwargs):
        """
        Retrieves, transforms, and processes data for the learner search route.
        Args:
            request (Request): The request object.
        Returns:
            Response: The response data formatted by the response builder.
        """
        response_data, status_code = self.load_route_data_and_build_response(
            request=request,
            handler_class=SearchHandler,
            response_builder_class=LearnerSearchResponseBuilder,
        )
        return Response(response_data, status=status_code)

    @extend_schema(
        tags=['Learner Portal BFF'],
        summary='Academy route',
        request=LearnerAcademyRequestSerializer,
        parameters=COMMON_BFF_QUERY_PARAMETERS,
        responses={
            status.HTTP_200_OK: OpenApiResponse(
                response=LearnerAcademyResponseSerializer,
                description='Sample response for the learner academy route.',
            ),
        },
        description='Retrieves, transforms, and processes data for the learner academy route.',
    )
    @action(detail=False, methods=['post'])
    @permission_required(BFF_READ_PERMISSION, fn=get_or_fetch_enterprise_uuid_for_bff_request)
    def academy(self, request, *args, **kwargs):
        """
        Retrieves, transforms, and processes data for the learner academy detail route.
        Args:
            request (Request): The request object.
        Returns:
            Response: The response data formatted by the response builder.
        """
        response_data, status_code = self.load_route_data_and_build_response(
            request=request,
            handler_class=AcademyHandler,
            response_builder_class=LearnerAcademyResponseBuilder,
        )
        return Response(response_data, status=status_code)

    @extend_schema(
        tags=['Learner Portal BFF'],
        summary='Skills Quiz route',
        request=LearnerSkillsQuizRequestSerializer,
        parameters=COMMON_BFF_QUERY_PARAMETERS,
        responses={
            status.HTTP_200_OK: OpenApiResponse(
                response=LearnerSkillsQuizResponseSerializer,
                description='Sample response for the learner skills quiz route.',
            ),
        },
        description='Retrieves, transforms, and processes data for the learner skills quiz route.',
    )
    @action(detail=False, methods=['post'], url_path='skills-quiz')
    @permission_required(BFF_READ_PERMISSION, fn=get_or_fetch_enterprise_uuid_for_bff_request)
    def skills_quiz(self, request, *args, **kwargs):
        """
        Retrieves, transforms, and processes data for the learner skills quiz route.
        Args:
            request (Request): The request object.
        Returns:
            Response: The response data formatted by the response builder.
        """
        response_data, status_code = self.load_route_data_and_build_response(
            request=request,
            handler_class=SkillsQuizHandler,
            response_builder_class=LearnerSkillsQuizResponseBuilder,
        )
        return Response(response_data, status=status_code)
