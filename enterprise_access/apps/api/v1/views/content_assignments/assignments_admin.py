"""
Admin-facing REST API views for LearnerContentAssignments in the content_assignments app.
"""
import logging

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from edx_rbac.decorators import permission_required
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import authentication, mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from enterprise_access.apps.api import filters, serializers, utils
from enterprise_access.apps.api.v1.views.utils import PaginationWithPageCount
from enterprise_access.apps.content_assignments import api as assignments_api
from enterprise_access.apps.content_assignments.models import LearnerContentAssignment
from enterprise_access.apps.core.constants import (
    CONTENT_ASSIGNMENT_ADMIN_READ_PERMISSION,
    CONTENT_ASSIGNMENT_ADMIN_WRITE_PERMISSION
)

logger = logging.getLogger(__name__)

CONTENT_ASSIGNMENT_ADMIN_CRUD_API_TAG = 'Content Assignment Admin CRUD'


def assignment_admin_permission_fn(request, *args, assignment_configuration_uuid=None, **kwargs):
    """
    Helper to use with @permission_required on all endpoints.

    Args:
        assignment_configuration_uuid (str): UUID representing a LearnerContentAssignment object.
    """
    return utils.get_assignment_config_customer_uuid(assignment_configuration_uuid)


class LearnerContentAssignmentAdminViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    Viewset supporting all Admin CRUD operations on ``LearnerContentAssignment`` records.
    """
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = serializers.LearnerContentAssignmentResponseSerializer
    authentication_classes = (JwtAuthentication, authentication.SessionAuthentication)
    filter_backends = (filters.NoFilterOnDetailBackend,)
    filterset_class = filters.LearnerContentAssignmentAdminFilter
    pagination_class = PaginationWithPageCount
    lookup_field = 'uuid'

    @property
    def requested_assignment_configuration_uuid(self):
        """
        Look in the requested URL path for an AssignmentConfiguration UUID.
        """
        return self.kwargs.get('assignment_configuration_uuid')

    def get_queryset(self):
        """
        A base queryset to list or retrieve ``LearnerContentAssignment`` records.
        """
        if self.action == 'list':
            return LearnerContentAssignment.objects.filter(
                assignment_configuration__uuid=self.requested_assignment_configuration_uuid
            )

        # For all other actions, RBAC controls enterprise-customer-based access, so returning all objects here is safe
        # (and more performant).
        return LearnerContentAssignment.objects.all()

    @extend_schema(
        tags=[CONTENT_ASSIGNMENT_ADMIN_CRUD_API_TAG],
        summary='Retrieve a content assignment by UUID.',
        responses={
            status.HTTP_200_OK: serializers.LearnerContentAssignmentResponseSerializer,
            status.HTTP_404_NOT_FOUND: None,  # TODO: test that this actually returns 404 instead of 403 on RBAC error.
        },
    )
    @permission_required(CONTENT_ASSIGNMENT_ADMIN_READ_PERMISSION, fn=assignment_admin_permission_fn)
    def retrieve(self, request, *args, uuid=None, **kwargs):
        """
        Retrieves a single ``LearnerContentAssignment`` record by uuid.
        """
        return super().retrieve(request, *args, uuid=uuid, **kwargs)

    @extend_schema(
        tags=[CONTENT_ASSIGNMENT_ADMIN_CRUD_API_TAG],
        summary='List content assignments.',
    )
    @permission_required(CONTENT_ASSIGNMENT_ADMIN_READ_PERMISSION, fn=assignment_admin_permission_fn)
    def list(self, request, *args, **kwargs):
        """
        Lists ``LearnerContentAssignment`` records, filtered by the given query parameters.
        """
        return super().list(request, *args, **kwargs)

    @extend_schema(
        tags=[CONTENT_ASSIGNMENT_ADMIN_CRUD_API_TAG],
        summary='Cancel an assignment by UUID.',
        responses={
            status.HTTP_200_OK: serializers.LearnerContentAssignmentResponseSerializer,
            status.HTTP_404_NOT_FOUND: None,
            status.HTTP_422_UNPROCESSABLE_ENTITY: None,
        },
    )
    @permission_required(CONTENT_ASSIGNMENT_ADMIN_WRITE_PERMISSION, fn=assignment_admin_permission_fn)
    @action(detail=True, methods=['post'])
    def cancel(self, request, *args, uuid=None, **kwargs):
        """
        Cancel a single ``LearnerContentAssignment`` record by uuid.

        Raises:
            404 if the assignment was not found.
            422 if the assignment is not cancelable.
        """
        try:
            assignment_to_cancel = self.get_queryset().get(uuid=uuid)
        except LearnerContentAssignment.DoesNotExist:
            return Response(None, status=status.HTTP_404_NOT_FOUND)

        # if the assignment is not cancelable, this is a no-op.
        cancellation_info = assignments_api.cancel_assignments([assignment_to_cancel])

        if len(cancellation_info['cancelled']) == 1:
            cancelled_assignment = cancellation_info['cancelled'][0]
            response_serializer = serializers.LearnerContentAssignmentResponseSerializer(cancelled_assignment)
            return Response(response_serializer.data, status=status.HTTP_200_OK)
        else:
            return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY)
