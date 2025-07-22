"""
Filter backends for Enterprise Access API.
"""

from django.db.models import Q, OuterRef, Subquery
from django_filters import rest_framework as filters
from edx_rbac import utils
from rest_framework import filters as drf_filters
from rest_framework.filters import OrderingFilter
from urllib.parse import unquote

from enterprise_access.apps.core import constants
from enterprise_access.apps.subsidy_request.models import LearnerCreditRequest, LearnerCreditRequestActions
from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates
from .mixins import NestedFilterMixin, create_nested_filter_aliases


__all__ = [
    'SubsidyRequestFilterBackend',
    'SubsidyRequestCustomerConfigurationFilterBackend',
    'LearnerCreditRequestFilter',
    'LearnerCreditRequestOrderingFilter',
]


class SubsidyRequestFilterBackend(drf_filters.BaseFilterBackend):
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


class SubsidyRequestCustomerConfigurationFilterBackend(drf_filters.BaseFilterBackend):
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




class LearnerCreditRequestFilter(NestedFilterMixin, filters.FilterSet):
    """
    Enhanced FilterSet for LearnerCreditRequest with modular nested filtering.

    Supports both root model filtering and nested action filtering using
    the Enhanced Hybrid Prefix approach with clean parameter names.
    """

    # Nested field configuration
    nested_field_config = {
        'action': {
            'related_name': 'actions',
            'latest_strategy': 'created',
            'fields': ['status', 'recent_action', 'error_reason', 'created']
        }
    }

    # Security: Override allowed nested fields
    ALLOWED_NESTED_FIELDS = ['actions']

    # Root model filters (preserved and enhanced)
    # Note: state filtering removed to avoid conflicts with SubsidyRequestFilterBackend
    uuid = filters.UUIDFilter(
        help_text='Filter by request UUID'
    )
    user__email = filters.CharFilter(
        lookup_expr='icontains',
        help_text='Filter by user email (case-insensitive)'
    )
    course_id = filters.CharFilter(
        help_text='Filter by course ID'
    )
    course_title = filters.CharFilter(
        lookup_expr='icontains',
        help_text='Filter by course title (case-insensitive)'
    )
    enterprise_customer_uuid = filters.UUIDFilter(
        help_text='Filter by enterprise customer UUID'
    )

    # Date range filters for request creation
    created = filters.DateTimeFilter(
        help_text='Filter by exact creation date'
    )
    created__gte = filters.DateTimeFilter(
        field_name='created',
        lookup_expr='gte',
        help_text='Filter by creation date >= value'
    )
    created__lte = filters.DateTimeFilter(
        field_name='created',
        lookup_expr='lte',
        help_text='Filter by creation date <= value'
    )

    class Meta:
        model = LearnerCreditRequest
        fields = {
            'uuid': ['exact'],
            'user__email': ['exact', 'icontains'],
            'course_id': ['exact'],
            'course_title': ['exact', 'icontains'],
            'enterprise_customer_uuid': ['exact'],
            'created': ['exact', 'gte', 'lte'],
        }



# Add backward compatibility aliases
create_nested_filter_aliases(LearnerCreditRequestFilter, {
    'latest_action_status': 'action_status',
    'latest_action_recent_action': 'action_recent_action',
    'latest_action_error_reason': 'action_error_reason',
    'latest_action_created': 'action_created',
    'latest_action_created__gte': 'action_created__gte',
    'latest_action_created__lte': 'action_created__lte',
})


class LearnerCreditRequestOrderingFilter(OrderingFilter):
    """
    Custom ordering filter that supports nested fields for learner credit requests.
    """

    def get_ordering(self, request, queryset, view):
        """
        Override to handle nested field ordering.
        """
        ordering = super().get_ordering(request, queryset, view)
        if not ordering:
            return ordering

        # Transform nested field ordering parameters
        processed_ordering = []
        for field in ordering:
            # Check for descending order first
            descending = field.startswith('-')
            clean_field = field[1:] if descending else field

            if clean_field.startswith('latest_action__'):
                # Handle nested ordering for latest action fields
                nested_field = clean_field.replace('latest_action__', '')
                if descending:
                    processed_ordering.append(f'-actions__{nested_field}')
                else:
                    processed_ordering.append(f'actions__{nested_field}')
            else:
                processed_ordering.append(field)

        return processed_ordering

    def filter_queryset(self, request, queryset, view):
        """
        Apply ordering to the queryset with support for nested fields.
        """
        ordering = self.get_ordering(request, queryset, view)

        if ordering:
            # For nested action fields, we need to handle them specially
            action_ordering_fields = [f for f in ordering if 'actions__' in f]
            regular_ordering_fields = [f for f in ordering if 'actions__' not in f]

            if action_ordering_fields:
                # Add prefetch for actions to optimize queries
                from django.db.models import Prefetch

                queryset = queryset.prefetch_related(
                    Prefetch(
                        'actions',
                        queryset=LearnerCreditRequestActions.objects.order_by('-created'),
                        to_attr='ordered_actions'
                    )
                )

                # For action ordering, we'll use a custom approach since Django
                # doesn't support ordering by related fields directly in this context
                # We'll order by the latest action's fields
                for field in action_ordering_fields:
                    clean_field = field.replace('actions__', '').replace('-', '')
                    descending = field.startswith('-')

                    # Get the latest action for each request
                    latest_action_subquery = LearnerCreditRequestActions.objects.filter(
                        learner_credit_request=OuterRef('pk')
                    ).order_by('-created').values(clean_field)[:1]

                    # Annotate with the latest action field value
                    annotation_name = f'latest_action_{clean_field}'
                    queryset = queryset.annotate(**{annotation_name: Subquery(latest_action_subquery)})

                    # Update the ordering field name
                    if descending:
                        regular_ordering_fields.append(f'-{annotation_name}')
                    else:
                        regular_ordering_fields.append(annotation_name)

            # Apply regular ordering
            if regular_ordering_fields:
                queryset = queryset.order_by(*regular_ordering_fields)

        return queryset
