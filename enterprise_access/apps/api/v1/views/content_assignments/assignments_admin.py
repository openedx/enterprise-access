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
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response

from enterprise_access.apps.api import filters, serializers, utils
from enterprise_access.apps.api.v1.views.utils import PaginationWithPageCount
from enterprise_access.apps.content_assignments import api as assignments_api
from enterprise_access.apps.content_assignments.constants import AssignmentActions, LearnerContentAssignmentStateChoices
from enterprise_access.apps.content_assignments.models import LearnerContentAssignment, LearnerContentAssignmentAction
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
    serializer_class = serializers.LearnerContentAssignmentAdminResponseSerializer
    authentication_classes = (JwtAuthentication, authentication.SessionAuthentication)
    filter_backends = (filters.NoFilterOnDetailBackend, OrderingFilter)
    filterset_class = filters.LearnerContentAssignmentAdminFilter
    pagination_class = PaginationWithPageCount
    lookup_field = 'uuid'

    # Settings that control list ordering, powered by OrderingFilter.
    # Fields in `ordering_fields` are what we allow to be passed to the "?ordering=" query param.
    ordering_fields = ['recent_action_time', 'learner_state_sort_order']
    # `ordering` defines the default order.
    ordering = ['-recent_action_time']

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
        queryset = LearnerContentAssignment.objects.all()
        if self.action == 'list':
            # Limit results based on the requested assignment configuration.
            queryset = queryset.filter(
                assignment_configuration__uuid=self.requested_assignment_configuration_uuid
            )
        else:
            # For all other actions, RBAC controls enterprise-customer-based access, so returning all objects here is
            # safe (and more performant).
            pass

        # Annotate extra dynamic fields used by this viewset for DRF-supported ordering and filtering:
        # * learner_state
        # * learner_state_sort_order
        # * recent_action
        # * recent_action_time
        queryset = LearnerContentAssignment.annotate_dynamic_fields_onto_queryset(queryset)

        return queryset

    @extend_schema(
        tags=[CONTENT_ASSIGNMENT_ADMIN_CRUD_API_TAG],
        summary='Retrieve a content assignment by UUID.',
        responses={
            status.HTTP_200_OK: serializers.LearnerContentAssignmentAdminResponseSerializer,
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
            status.HTTP_200_OK: serializers.LearnerContentAssignmentAdminResponseSerializer,
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

        # If the response contains one element in the `cancelled` list, that is the one we sent, indicating succcess.
        cancellation_succeeded = len(cancellation_info['cancelled']) == 1

        if cancellation_succeeded:
            # Serialize the assignment object obtained via get_queryset() instead of the one from the assignments_api.
            # Only the former has the additional dynamic fields annotated, and those are required for serialization.
            assignment_to_cancel.refresh_from_db()
            response_serializer = serializers.LearnerContentAssignmentAdminResponseSerializer(assignment_to_cancel)
            return Response(response_serializer.data, status=status.HTTP_200_OK)
        else:
            return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY)
