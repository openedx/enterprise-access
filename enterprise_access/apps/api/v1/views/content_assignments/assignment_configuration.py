"""
REST API views for the content_assignments app.
"""
import logging

from django.core.exceptions import ValidationError
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from edx_rbac.decorators import permission_required
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import authentication, mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from enterprise_access.apps.api import filters, serializers, utils
from enterprise_access.apps.api.utils import get_assignment_config_customer_uuid
from enterprise_access.apps.api.v1.views.utils import PaginationWithPageCount
from enterprise_access.apps.content_assignments.models import AssignmentConfiguration
from enterprise_access.apps.core.constants import (
    CONTENT_ASSIGNMENT_CONFIGURATION_ACKNOWLEDGE_PERMISSION,
    CONTENT_ASSIGNMENT_CONFIGURATION_READ_PERMISSION,
    CONTENT_ASSIGNMENT_CONFIGURATION_WRITE_PERMISSION
)

logger = logging.getLogger(__name__)

CONTENT_ASSIGNMENTS_CONFIGURATION_CRUD_API_TAG = 'Content Assignment Configuration CRUD'


def assignment_config_permission_create_fn(request):
    """
    Helper to use with @permission_required on create endpoint.
    """
    return request.data.get('enterprise_customer_uuid')


def assignment_config_permission_detail_fn(request, *args, uuid=None, **kwargs):
    """
    Helper to use with @permission_required on detail-type endpoints (retrieve, update, partial_update, destroy).

    Args:
        uuid (str): UUID representing an AssignmentConfiguration object.
    """
    return get_assignment_config_customer_uuid(uuid)


class AssignmentConfigurationViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Viewset supporting all CRUD operations on ``AssignmentConfiguration`` records.
    """
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = serializers.AssignmentConfigurationResponseSerializer
    authentication_classes = (JwtAuthentication, authentication.SessionAuthentication)
    filter_backends = (filters.NoFilterOnDetailBackend,)
    filterset_class = filters.AssignmentConfigurationFilter
    pagination_class = PaginationWithPageCount
    lookup_field = 'uuid'

    @property
    def requested_enterprise_customer_uuid(self):
        """
        Look in the query parameters for an enterprise customer UUID.
        """
        return utils.get_enterprise_uuid_from_query_params(self.request)

    def get_queryset(self):
        """
        A base queryset to list or retrieve ``AssignmentConfiguration`` records.
        """
        if self.action == 'list':
            return AssignmentConfiguration.objects.filter(
                enterprise_customer_uuid=self.requested_enterprise_customer_uuid
            )

        # For all other actions, RBAC controls enterprise-customer-based access, so returning all objects here is safe.
        return AssignmentConfiguration.objects.all()

    def get_serializer_class(self):
        """
        Overrides the default behavior to return different serializers depending on the request action.
        """
        if self.action == 'create':
            return serializers.AssignmentConfigurationCreateRequestSerializer
        if self.action in ('update', 'partial_update'):
            return serializers.AssignmentConfigurationUpdateRequestSerializer
        if self.action in ('destroy'):
            return serializers.AssignmentConfigurationDeleteRequestSerializer
        # list and retrieve use the default serializer.
        return self.serializer_class

    @extend_schema(
        tags=[CONTENT_ASSIGNMENTS_CONFIGURATION_CRUD_API_TAG],
        summary='Retrieve content assignment configuration by UUID.',
        responses={
            status.HTTP_200_OK: serializers.AssignmentConfigurationResponseSerializer,
            status.HTTP_403_FORBIDDEN: None,
        },
    )
    @permission_required(CONTENT_ASSIGNMENT_CONFIGURATION_READ_PERMISSION, fn=assignment_config_permission_detail_fn)
    def retrieve(self, request, *args, uuid=None, **kwargs):
        """
        Retrieves a single ``AssignmentConfiguration`` record by uuid.
        """
        return super().retrieve(request, *args, uuid=uuid, **kwargs)

    @extend_schema(
        tags=[CONTENT_ASSIGNMENTS_CONFIGURATION_CRUD_API_TAG],
        summary='List content assignment configurations.',
        # Inject additional parameters which cannot be inferred form the Serializer.  This is easier to do than to
        # construct a new Serializer from scratch just to mimic a request schema that supports pagination.
        parameters=[
            OpenApiParameter(
                name='enterprise_customer_uuid',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=True,
                description='List only assignment configurations belonging to the given customer.',
            ),
        ],
    )
    @permission_required(
        CONTENT_ASSIGNMENT_CONFIGURATION_READ_PERMISSION,
        fn=lambda request: request.query_params.get('enterprise_customer_uuid')
    )
    def list(self, request, *args, **kwargs):
        """
        Lists ``AssignmentConfiguration`` records, filtered by the given query parameters.

        TODO: implement a ``subsidy_access_policy`` filter.
        """
        return super().list(request, *args, **kwargs)

    @extend_schema(
        tags=[CONTENT_ASSIGNMENTS_CONFIGURATION_CRUD_API_TAG],
        summary='Get or create a new content assignment configuration for the given subsidy access policy.',
        request=serializers.AssignmentConfigurationCreateRequestSerializer,
        responses={
            status.HTTP_200_OK: serializers.AssignmentConfigurationResponseSerializer,
            status.HTTP_201_CREATED: serializers.AssignmentConfigurationResponseSerializer,
            status.HTTP_403_FORBIDDEN: None,
        },
    )
    @permission_required(
        CONTENT_ASSIGNMENT_CONFIGURATION_WRITE_PERMISSION,
        fn=assignment_config_permission_create_fn,
    )
    def create(self, request, *args, **kwargs):
        """
        Creates a single ``AssignmentConfiguration`` record.
        """
        return super().create(request, *args, **kwargs)

    @extend_schema(
        tags=[CONTENT_ASSIGNMENTS_CONFIGURATION_CRUD_API_TAG],
        summary='Partially update (with a PUT) a content assignment configuration by UUID.',
        request=serializers.AssignmentConfigurationUpdateRequestSerializer,
        responses={
            status.HTTP_200_OK: serializers.AssignmentConfigurationResponseSerializer,
            status.HTTP_403_FORBIDDEN: None,
        },
    )
    @permission_required(CONTENT_ASSIGNMENT_CONFIGURATION_WRITE_PERMISSION, fn=assignment_config_permission_detail_fn)
    def update(self, request, *args, uuid=None, **kwargs):
        """
        Updates a single ``AssignmentConfiguration`` record by uuid.  All fields for the update are optional (which is
        different from a standard PUT request).
        """
        kwargs['partial'] = True
        return super().update(request, *args, uuid=uuid, **kwargs)

    @extend_schema(
        tags=[CONTENT_ASSIGNMENTS_CONFIGURATION_CRUD_API_TAG],
        summary='Partially update (with a PATCH) a content assignment configuration by UUID.',
        request=serializers.AssignmentConfigurationUpdateRequestSerializer,
        responses={
            status.HTTP_200_OK: serializers.AssignmentConfigurationResponseSerializer,
            status.HTTP_403_FORBIDDEN: None,
        },
    )
    @permission_required(CONTENT_ASSIGNMENT_CONFIGURATION_WRITE_PERMISSION, fn=assignment_config_permission_detail_fn)
    def partial_update(self, request, *args, uuid=None, **kwargs):
        """
        Updates a single ``AssignmentConfiguration`` record by uuid.  All fields for the update are optional.
        """
        return super().partial_update(request, *args, uuid=uuid, **kwargs)

    @extend_schema(
        tags=[CONTENT_ASSIGNMENTS_CONFIGURATION_CRUD_API_TAG],
        summary='Soft-delete content assignment configuration by UUID.',
        request=serializers.AssignmentConfigurationDeleteRequestSerializer,
        responses={
            status.HTTP_200_OK: serializers.AssignmentConfigurationResponseSerializer,
            status.HTTP_403_FORBIDDEN: None,
        },
    )
    @permission_required(CONTENT_ASSIGNMENT_CONFIGURATION_WRITE_PERMISSION, fn=assignment_config_permission_detail_fn)
    def destroy(self, request, *args, uuid=None, **kwargs):
        """
        Soft-delete a single ``AssignmentConfiguration`` record by uuid, and unlink from the associated policy.

        Note: This endpoint supports an optional "reason" request body parameter, representing the description (free
        form text) for why the AssignmentConfiguration is being deactivated.
        """
        # Note: destroy() must be implemented in the view instead of the serializer because DRF serializers don't
        # implement destroy/delete.

        # Collect the "reason" query parameter from request body.
        request_serializer = serializers.AssignmentConfigurationDeleteRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        delete_reason = request_serializer.data.get('reason', None)

        try:
            assignment_config_to_soft_delete = self.get_queryset().get(uuid=uuid)
        except AssignmentConfiguration.DoesNotExist:
            return Response(None, status=status.HTTP_404_NOT_FOUND)

        # Custom delete() method should set the active flag to False.
        assignment_config_to_soft_delete.delete(reason=delete_reason)

        response_serializer = serializers.AssignmentConfigurationResponseSerializer(assignment_config_to_soft_delete)
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=['Assignment Acknowledgement'],
        summary='Acknowledge assignments for the given AssignmentConfiguration.',
        request=serializers.AssignmentConfigurationAcknowledgeAssignmentsRequestSerializer,
        responses={
            status.HTTP_200_OK: serializers.AssignmentConfigurationAcknowledgeAssignmentsResponseSerializer,
            status.HTTP_201_CREATED: serializers.AssignmentConfigurationAcknowledgeAssignmentsResponseSerializer,
            status.HTTP_403_FORBIDDEN: None,
        },
    )
    @permission_required(CONTENT_ASSIGNMENT_CONFIGURATION_ACKNOWLEDGE_PERMISSION, fn=assignment_config_permission_detail_fn)
    @action(detail=True, methods=['post'], url_path='acknowledge-assignments')
    def acknowledge_assignments(self, request, **kwargs):  # pylint: disable=unused-argument
        """
        POST API endpoint that allows the authenticated user to acknowledge cancelled and/or expired assignments
        for the specified AssignmentConfiguration. By acknowledging the assignments in an expired
        and/or cancelled state, the user is indicating they have seen the assignments and have
        opted to dismiss them from the UI.

        This endpoint will return an object containing:
        - assignments successfully acknowledged
        - assignments already acknowledged
        - assignments not acknowledged
        """
        # Collect the "assignment_uuids" query parameter from request body.
        request_serializer = serializers.AssignmentConfigurationAcknowledgeAssignmentsRequestSerializer(
            data=request.data,
        )
        request_serializer.is_valid(raise_exception=True)
        assignment_uuids_to_acknowledge = request_serializer.data.get('assignment_uuids', None)

        assignment_configuration = self.get_object()
        try:
            acknowledged_assignments, already_acknowledged_assignments, unacknowledged_assignments = \
                assignment_configuration.acknowledge_assignments(
                    assignment_uuids=assignment_uuids_to_acknowledge,
                    lms_user_id=request.user.lms_user_id,
                )
        except ValidationError as err:
            return Response(
                {"error": err.message},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Inline helper function to extract the UUIDs from a list of assignments.
        def _extract_uuid_from_assignment(assignments):
            return [assignment.uuid for assignment in assignments]

        response_serializer = serializers.AssignmentConfigurationAcknowledgeAssignmentsResponseSerializer({
            "acknowledged_assignments": _extract_uuid_from_assignment(acknowledged_assignments),
            "already_acknowledged_assignments": _extract_uuid_from_assignment(already_acknowledged_assignments),
            "unacknowledged_assignments": _extract_uuid_from_assignment(unacknowledged_assignments),
        })
        response_status_code = status.HTTP_201_CREATED if acknowledged_assignments else status.HTTP_200_OK
        return Response(
            response_serializer.data,
            status=response_status_code,
        )
