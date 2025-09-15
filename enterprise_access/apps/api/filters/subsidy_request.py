"""
Filter backends for Enterprise Access API.
"""

from django.db.models import Q
from django_filters import CharFilter
from django_filters import rest_framework as drf_filters
from edx_rbac import utils
from rest_framework import filters

from enterprise_access.apps.core import constants
from enterprise_access.apps.subsidy_request.constants import LearnerCreditRequestActionChoices
from enterprise_access.apps.subsidy_request.models import LearnerCreditRequest

from .base import CharInFilter

LATEST_ACTION_STATUS_HELP_TEXT = (
    'Choose from the following valid action statuses: ' +
    ', '.join([choice for choice, _ in LearnerCreditRequestActionChoices])
)

LEARNER_REQUEST_STATE_HELP_TEXT = (
    'Choose from the following valid learner request states: '
    'requested, pending, approved, declined, accepted, cancelled, expired, reversed, reminded, waiting, failed'
)


class SubsidyRequestFilterBackend(filters.BaseFilterBackend):
    """
    Filter backend that returns subsidy requests that were either created for the user or created under
    enterprises that the user is an admin of.
    """

    def _filter_by_states(self, request, queryset):
        """
        Filter queryset by comma-delimited list of states.
        """

        states = request.query_params.get('state', None)
        if states:
            states = states.strip(',').split(',')
            return queryset.filter(state__in=states)

        return queryset

    def _filter_by_accessible_requests(self, request, queryset):
        """
        Filter queryset for non staff/super users.
        """

        if request.user.is_staff or request.user.is_superuser:
            return queryset

        decoded_jwt = utils.get_decoded_jwt(request)
        lms_user_id_from_jwt = decoded_jwt.get('user_id')
        accessible_enterprises_as_admin = utils.contexts_accessible_from_request(
            request, [constants.REQUESTS_ADMIN_ROLE]
        )

        # openedx operators will have access to all enterprises
        if constants.ALL_ACCESS_CONTEXT in accessible_enterprises_as_admin:
            return queryset

        return queryset.filter(
            Q(user__lms_user_id=lms_user_id_from_jwt) |
            Q(enterprise_customer_uuid__in=accessible_enterprises_as_admin)
        )

    def filter_queryset(self, request, queryset, view):
        queryset = self._filter_by_accessible_requests(request, queryset)
        queryset = self._filter_by_states(request, queryset)

        return queryset


class SubsidyRequestCustomerConfigurationFilterBackend(filters.BaseFilterBackend):
    """
    Filter backend that returns customer configurations of enterprises that the user has access to.
    """

    def filter_queryset(self, request, queryset, view):
        """
        Filter queryset for non staff/super users.
        """

        if request.user.is_staff or request.user.is_superuser:
            return queryset

        accessible_enterprises_as_admin = utils.contexts_accessible_from_request(
            request, [constants.REQUESTS_ADMIN_ROLE]
        )

        # openedx operators will have access to all enterprises
        if constants.ALL_ACCESS_CONTEXT in accessible_enterprises_as_admin:
            return queryset

        accessible_enterprises_as_learner = utils.contexts_accessible_from_request(
            request, [constants.REQUESTS_LEARNER_ROLE]
        )

        # do a similar check for edge-case learners that might have access to all enterprises
        if constants.ALL_ACCESS_CONTEXT in accessible_enterprises_as_learner:
            return queryset

        return queryset.filter(
            Q(enterprise_customer_uuid__in=accessible_enterprises_as_admin.union(accessible_enterprises_as_learner))
        )


class LearnerCreditRequestFilterSet(drf_filters.FilterSet):
    """
    Custom FilterSet for LearnerCreditRequest to allow filtering by policy_uuid and latest_action_status
    """

    policy_uuid = drf_filters.UUIDFilter(field_name='learner_credit_request_config__learner_credit_config__uuid')

    # Add filtering support for annotated field
    latest_action_status = CharFilter(
        field_name='latest_action_status',
        lookup_expr='exact',
        help_text=LATEST_ACTION_STATUS_HELP_TEXT,
    )
    latest_action_status__in = CharInFilter(
        field_name='latest_action_status',
        lookup_expr='in',
        help_text=LATEST_ACTION_STATUS_HELP_TEXT,
    )

    # Add filtering support for learner_request_state annotated field
    learner_request_state = CharFilter(
        field_name='learner_request_state',
        lookup_expr='exact',
        help_text=LEARNER_REQUEST_STATE_HELP_TEXT,
    )

    learner_request_state__in = CharInFilter(
        field_name='learner_request_state',
        lookup_expr='in',
        help_text=LEARNER_REQUEST_STATE_HELP_TEXT,
    )

    class Meta:
        model = LearnerCreditRequest
        fields = ['uuid', 'user__email', 'course_id', 'enterprise_customer_uuid', 'policy_uuid']
