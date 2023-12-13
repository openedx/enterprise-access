"""
Learner-facing REST API views for LearnerContentAssignments in the content_assignments app.
"""
import logging

from drf_spectacular.utils import extend_schema
from edx_rbac.decorators import permission_required
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import authentication, mixins, permissions, status, viewsets

from enterprise_access.apps.api import filters, serializers, utils
from enterprise_access.apps.api.v1.views.utils import PaginationWithPageCount
from enterprise_access.apps.content_assignments.models import LearnerContentAssignment
from enterprise_access.apps.core.constants import (CONTENT_ASSIGNMENT_LEARNER_READ_PERMISSION, CONTENT_ASSIGNMENT_LEARNER_WRITE_PERMISSION)

logger = logging.getLogger(__name__)

CONTENT_ASSIGNMENT_CRUD_API_TAG = 'Content Assignment CRUD'


def assignment_permission_fn(request, *args, assignment_configuration_uuid=None, **kwargs):
    """
    Helper to use with @permission_required on all endpoints.

    Args:
        assignment_configuration_uuid (str): UUID representing a LearnerContentAssignment object.
    """
    return utils.get_assignment_config_customer_uuid(assignment_configuration_uuid)


class LearnerContentAssignmentViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
    mixins.UpdateModelMixin,
):
    """
    Viewset supporting all learner-facing CRUD operations on ``LearnerContentAssignment`` records.
    """
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = serializers.LearnerContentAssignmentResponseSerializer
    authentication_classes = (JwtAuthentication, authentication.SessionAuthentication)
    filter_backends = (filters.NoFilterOnDetailBackend,)
    pagination_class = PaginationWithPageCount
    lookup_field = 'uuid'

    def get_serializer_class(self):
        """
        Overrides the default behavior to return different serializers depending on the request action.
        """

        if self.action in ('update', 'partial_update'):
            return serializers.LearnerContentAssignmentUpdateRequestSerializer
        # list and retrieve use the default serializer.
        return self.serializer_class
    
    @property
    def requesting_user_email(self):
        """
        Convenience property to get requesting user's email.
        """
        return self.request.user.email

    @property
    def requested_assignment_config_uuid(self):
        """
        Returns the requested ``assignment_configuration_uuid`` path parameter.
        """
        return self.kwargs.get('assignment_configuration_uuid')

    def get_queryset(self):
        """
        A base queryset to list or retrieve ``LearnerContentAssignment`` records.  In this viewset, only the assignments
        assigned to the requester are returned.

        Unlike in LearnerContentAssignmentAdminViewSet, here we are not going to annotate the extra dynamic fields using
        `annotate_dynamic_fields_onto_queryset()`, so we will NOT serialize `learner_state` and `recent_action` for each
        assignment.
        """
        return LearnerContentAssignment.objects.filter(
            learner_email=self.requesting_user_email,
            assignment_configuration__uuid=self.requested_assignment_config_uuid,
        )

    @extend_schema(
        tags=[CONTENT_ASSIGNMENT_CRUD_API_TAG],
        summary='Retrieve content assignments by UUID.',
        responses={
            status.HTTP_200_OK: serializers.LearnerContentAssignmentResponseSerializer,
            status.HTTP_404_NOT_FOUND: None,
        },
    )
    @permission_required(CONTENT_ASSIGNMENT_LEARNER_READ_PERMISSION, fn=assignment_permission_fn)
    def retrieve(self, request, *args, uuid=None, **kwargs):
        """
        Retrieves a single ``LearnerContentAssignment`` record by uuid, if assigned to the requesting user for this
        given assignment configuration.
        """
        return super().retrieve(request, *args, uuid=uuid, **kwargs)

    @extend_schema(
        tags=[CONTENT_ASSIGNMENT_CRUD_API_TAG],
        summary='List content assignments.',
    )
    @permission_required(CONTENT_ASSIGNMENT_LEARNER_READ_PERMISSION, fn=assignment_permission_fn)
    def list(self, request, *args, **kwargs):
        """
        Lists ``LearnerContentAssignment`` records assigned to the requesting user for the given assignment
        configuration.
        """
        return super().list(request, *args, **kwargs)

    @extend_schema(
        tags=[CONTENT_ASSIGNMENT_CRUD_API_TAG],
        summary='Partially update (with a PATCH) a learner assignment by UUID.',
        request=serializers.LearnerContentAssignmentUpdateRequestSerializer,
        responses={
            status.HTTP_200_OK: None,
            status.HTTP_404_NOT_FOUND: None,
            status.HTTP_422_UNPROCESSABLE_ENTITY: None,
        },
    )
    @permission_required(CONTENT_ASSIGNMENT_LEARNER_WRITE_PERMISSION, fn=assignment_permission_fn)
    def partial_update(self, request, *args, uuid=None, **kwargs):
        """
        Updates a single ``LearnerContentAssignment`` record by uuid.  All fields for the update are optional.
        """
        return super().partial_update(request, *args, uuid=uuid, **kwargs)
