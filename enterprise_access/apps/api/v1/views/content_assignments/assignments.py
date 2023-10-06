"""
Learner-facing REST API views for LearnerContentAssignments in the content_assignments app.
"""
import logging

from drf_spectacular.utils import extend_schema
from edx_rbac.decorators import permission_required
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import authentication, mixins, permissions, status, viewsets

from enterprise_access.apps.api import filters, serializers, utils
from enterprise_access.apps.api.v1.views.utils import OptionalPaginationWithPageCount
from enterprise_access.apps.content_assignments.models import LearnerContentAssignment
from enterprise_access.apps.core.constants import CONTENT_ASSIGNMENT_LEARNER_READ_PERMISSION

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
):
    """
    Viewset supporting all learner-facing CRUD operations on ``LearnerContentAssignment`` records.
    """
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = serializers.LearnerContentAssignmentResponseSerializer
    authentication_classes = (JwtAuthentication, authentication.SessionAuthentication)
    filter_backends = (filters.NoFilterOnDetailBackend,)
    pagination_class = OptionalPaginationWithPageCount
    lookup_field = 'uuid'

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
