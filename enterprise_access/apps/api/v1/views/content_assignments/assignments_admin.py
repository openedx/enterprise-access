"""
Admin-facing REST API views for LearnerContentAssignments in the content_assignments app.
"""
import logging
from collections import Counter

from drf_spectacular.utils import extend_schema
from edx_rbac.decorators import permission_required
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import authentication, mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response

from enterprise_access.apps.api import filters, serializers, utils
from enterprise_access.apps.api.serializers.content_assignments.assignment import (
    LearnerContentAssignmentActionRequestSerializer,
    LearnerContentAssignmentNudgeHTTP422ErrorSerializer,
    LearnerContentAssignmentNudgeRequestSerializer,
    LearnerContentAssignmentNudgeResponseSerializer
)
from enterprise_access.apps.api.v1.views.utils import PaginationWithPageCount
from enterprise_access.apps.content_assignments import api as assignments_api
from enterprise_access.apps.content_assignments.constants import (
    AssignmentLearnerStates,
    LearnerContentAssignmentStateChoices
)
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


class PaginationWithPageCountAndLearnerStateCounts(PaginationWithPageCount):
    """
    Custom paginator class that adds a `learner_state_counts` field to the response schema such that
    it shows in the DRF Spectacular generated docs.
    """

    def get_paginated_response_schema(self, schema):
        """
        Annotate the paginated response schema with the extra fields provided by edx_rest_framework_extensions'
        DefaultPagination class (e.g., `page_count`).
        """
        response_schema = super().get_paginated_response_schema(schema)
        response_schema['properties']['learner_state_counts'] = {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'learner_state': {
                        'type': 'string',
                        'description': 'The learner state for the assignment',
                        'example': 'waiting',
                    },
                    'count': {
                        'type': 'integer',
                        'description': 'The number of assignments in this state',
                        'example': 123,
                    },
                },
            },

        }
        return response_schema


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
    filter_backends = (filters.NoFilterOnDetailBackend, OrderingFilter, SearchFilter)
    filterset_class = filters.LearnerContentAssignmentAdminFilter
    pagination_class = PaginationWithPageCountAndLearnerStateCounts
    lookup_field = 'uuid'

    # Settings that control list ordering, powered by OrderingFilter.
    # Fields in `ordering_fields` are what we allow to be passed to the "?ordering=" query param.
    ordering_fields = ['recent_action_time', 'learner_state_sort_order', 'content_quantity']
    # `ordering` defines the default order.
    ordering = ['-recent_action_time']

    search_fields = ['content_title', 'learner_email']

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
        if self.action in ('list', 'remind_all', 'cancel_all'):
            # Limit results based on the requested assignment configuration.
            queryset = queryset.filter(
                assignment_configuration__uuid=self.requested_assignment_configuration_uuid
            )
        else:
            # For all other actions, RBAC controls enterprise-customer-based access, so returning all objects here is
            # safe (and more performant).
            pass

        # Annotate extra dynamic fields used by this viewset for DRF-supported ordering and filtering,
        # but only for the list, retrieve, and cancel/remind-all actions:
        # * learner_state
        # * learner_state_sort_order
        # * recent_action
        # * recent_action_time
        if self.action in ('list', 'retrieve', 'remind_all', 'cancel_all'):
            queryset = LearnerContentAssignment.annotate_dynamic_fields_onto_queryset(
                queryset,
            ).prefetch_related(
                'actions',
            )

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
        responses={
            status.HTTP_200_OK: serializers.LearnerContentAssignmentAdminResponseSerializer,
            status.HTTP_404_NOT_FOUND: None,
        },
    )
    @permission_required(CONTENT_ASSIGNMENT_ADMIN_READ_PERMISSION, fn=assignment_admin_permission_fn)
    def list(self, request, *args, **kwargs):
        """
        Lists ``LearnerContentAssignment`` records, filtered by the given query parameters.
        """
        response = super().list(request, *args, **kwargs)

        # Compute the learner_state_counts for the filtered queryset.
        queryset = self.filter_queryset(self.get_queryset())
        learner_state_counter = Counter(
            queryset.exclude(learner_state__isnull=True).values_list('learner_state', flat=True)
        )
        learner_state_counts = [
            {'learner_state': state, 'count': count}
            for state, count in learner_state_counter.most_common()
        ]

        # Add the learner_state_counts to the default response.
        response.data['learner_state_counts'] = learner_state_counts
        return response

    @extend_schema(
        tags=[CONTENT_ASSIGNMENT_ADMIN_CRUD_API_TAG],
        summary='Cancel assignments by UUID.',
        request=LearnerContentAssignmentActionRequestSerializer,
        responses={
            status.HTTP_200_OK: None,
            status.HTTP_404_NOT_FOUND: None,
            status.HTTP_422_UNPROCESSABLE_ENTITY: None,
        },
    )
    @permission_required(CONTENT_ASSIGNMENT_ADMIN_WRITE_PERMISSION, fn=assignment_admin_permission_fn)
    @action(detail=False, methods=['post'])
    def cancel(self, request, *args, **kwargs):
        """
        Cancel a list of ``LearnerContentAssignment`` records by uuid.

        ```
        Raises:
            404 if any of the assignments were not found
            422 if any of the assignments threw an error (not found or not cancelable)
        ```
        """
        serializer = LearnerContentAssignmentActionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assignments = self.get_queryset().filter(
            assignment_configuration__uuid=self.requested_assignment_configuration_uuid,
            uuid__in=serializer.data['assignment_uuids'])
        try:
            response = assignments_api.cancel_assignments(assignments)
            if response.get('non_cancelable') or len(assignments) < len(request.data['assignment_uuids']):
                return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY)
            return Response(status=status.HTTP_200_OK)
        except Exception:  # pylint: disable=broad-except
            return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    @extend_schema(
        tags=[CONTENT_ASSIGNMENT_ADMIN_CRUD_API_TAG],
        summary='Cancel all assignments for the requested assignment configuration.',
        request=None,
        filters=filters.LearnerContentAssignmentAdminFilter,
        responses={
            status.HTTP_202_ACCEPTED: None,
            status.HTTP_404_NOT_FOUND: None,
            status.HTTP_422_UNPROCESSABLE_ENTITY: None,
        },
    )
    @permission_required(CONTENT_ASSIGNMENT_ADMIN_WRITE_PERMISSION, fn=assignment_admin_permission_fn)
    @action(detail=False, methods=['post'], url_path='cancel-all', pagination_class=None)
    def cancel_all(self, request, *args, **kwargs):
        """
        Cancel all ``LearnerContentAssignment`` associated with the given assignment configuration.
        Optionally, cancel only assignments matching the criteria of the provided query param filters.

        ```
        Raises:
            404 if no cancelable assignments were found
            422 if any of the assignments threw an error (not found or not cancelable)
        ```
        """
        base_queryset = self.get_queryset().filter(
            state__in=LearnerContentAssignmentStateChoices.CANCELABLE_STATES,
        )
        assignments = self.filter_queryset(base_queryset)
        if not assignments:
            return Response(status=status.HTTP_404_NOT_FOUND)

        try:
            response = assignments_api.cancel_assignments(assignments)
            if non_cancelable_assignments := response.get('non_cancelable'):
                # This is very unlikely to occur, because we filter down to only the cancelable
                # assignments before calling `cancel_assignments()`, and that function
                # only declares assignments to be non-cancelable if they are not
                # in the set of cancelable states.
                logger.error(
                    'There were non-cancelable assignments in cancel-all: %s',
                    non_cancelable_assignments,
                )
                return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY)
            return Response(status=status.HTTP_202_ACCEPTED)
        except Exception:  # pylint: disable=broad-except
            return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    @extend_schema(
        tags=[CONTENT_ASSIGNMENT_ADMIN_CRUD_API_TAG],
        summary='Remind assignments by UUID.',
        request=LearnerContentAssignmentActionRequestSerializer,
        responses={
            status.HTTP_200_OK: None,
            status.HTTP_404_NOT_FOUND: None,
            status.HTTP_422_UNPROCESSABLE_ENTITY: None,
        },
    )
    @permission_required(CONTENT_ASSIGNMENT_ADMIN_WRITE_PERMISSION, fn=assignment_admin_permission_fn)
    @action(detail=False, methods=['post'])
    def remind(self, request, *args, **kwargs):
        """
        Send reminders to a list of learners with associated ``LearnerContentAssignment``
        record by list of uuids.

        ```
        Raises:
            404 if any of the assignments were not found
            422 if any of the assignments threw an error (not found or not remindable)
        ```
        """
        serializer = LearnerContentAssignmentActionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assignments = self.get_queryset().filter(
            assignment_configuration__uuid=self.requested_assignment_configuration_uuid,
            uuid__in=serializer.data['assignment_uuids'],
        )
        try:
            response = assignments_api.remind_assignments(assignments)
            if response.get('non_remindable_assignments') or len(assignments) < len(request.data['assignment_uuids']):
                return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY)
            return Response(status=status.HTTP_200_OK)
        except Exception:  # pylint: disable=broad-except
            return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    @extend_schema(
        tags=[CONTENT_ASSIGNMENT_ADMIN_CRUD_API_TAG],
        summary='Remind all assignments for the given assignment configuration.',
        request=None,
        filters=filters.LearnerContentAssignmentAdminFilter,
        responses={
            status.HTTP_202_ACCEPTED: None,
            status.HTTP_404_NOT_FOUND: None,
            status.HTTP_422_UNPROCESSABLE_ENTITY: None,
        },
    )
    @permission_required(CONTENT_ASSIGNMENT_ADMIN_WRITE_PERMISSION, fn=assignment_admin_permission_fn)
    @action(detail=False, methods=['post'], url_path='remind-all', pagination_class=None)
    def remind_all(self, request, *args, **kwargs):
        """
        Send reminders for all assignments related to the given assignment configuration.
        Optionally, remind only assignments matching the criteria of the provided query param filters.

        ```
        Raises:
            404 if no cancelable assignments were found
            422 if any of the assignments threw an error (not found or not remindable)
        ```
        """
        base_queryset = self.get_queryset().filter(
            state__in=LearnerContentAssignmentStateChoices.REMINDABLE_STATES,
        )
        assignments = self.filter_queryset(base_queryset)
        if not assignments:
            return Response(status=status.HTTP_404_NOT_FOUND)

        try:
            response = assignments_api.remind_assignments(assignments)
            if non_remindable_assignments := response.get('non_remindable_assignments'):
                # This is very unlikely to occur, because we filter down to only the remindable
                # assignments before calling `remind_assignments()`, and that function
                # only declares assignments to be non-remindable if they are not
                # in the set of remindable states.
                logger.error(
                    'There were non-remindable assignments in remind-all: %s',
                    non_remindable_assignments,
                )
                return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY)
            return Response(status=status.HTTP_202_ACCEPTED)
        except Exception:  # pylint: disable=broad-except
            return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    @extend_schema(
        tags=[CONTENT_ASSIGNMENT_ADMIN_CRUD_API_TAG],
        summary='Nudge assignments by UUID.',
        request=LearnerContentAssignmentNudgeRequestSerializer,
        parameters=None,
        responses={
            status.HTTP_200_OK: LearnerContentAssignmentNudgeResponseSerializer,
            status.HTTP_422_UNPROCESSABLE_ENTITY: LearnerContentAssignmentNudgeHTTP422ErrorSerializer,
        }
    )
    @permission_required(CONTENT_ASSIGNMENT_ADMIN_WRITE_PERMISSION, fn=assignment_admin_permission_fn)
    @action(detail=False, methods=['post'])
    def nudge(self, request, *args, **kwargs):
        """
        Send nudges to a list of learners with associated ``LearnerContentAssignment``
        record by list of uuids.

        ```
        Raises:
            400 If assignment_uuids list length is 0 or the value for days_before_course_start_date is less than 1
            422 If the nudge_assignments call fails for any other reason
        ```
        """
        serializer = LearnerContentAssignmentNudgeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assignment_configuration_uuid = self.requested_assignment_configuration_uuid
        assignments = self.get_queryset().filter(
            assignment_configuration__uuid=assignment_configuration_uuid,
            uuid__in=serializer.data['assignment_uuids'],
        )
        days_before_course_start_date = serializer.data['days_before_course_start_date']
        try:
            if len(assignments) == 0:
                error_message = (
                    "The list of assignments provided are not associated to the assignment_configuration_uuid: {0}"
                    .format(assignment_configuration_uuid)
                )
                return Response(
                    data={"error_message": error_message}, status=status.HTTP_422_UNPROCESSABLE_ENTITY
                )
            result = assignments_api.nudge_assignments(
                assignments,
                assignment_configuration_uuid,
                days_before_course_start_date
            )
            response_serializer = LearnerContentAssignmentNudgeResponseSerializer(data=result)
            response_serializer.is_valid(raise_exception=True)
            return Response(data=response_serializer.data, status=status.HTTP_200_OK)
        except Exception:  # pylint: disable=broad-except
            error_message = (
                "Could not process the nudge email(s) for assignment_configuration_uuid: {0}"
                .format(assignment_configuration_uuid)
            )
            return Response(
                data={"error_message": error_message},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
