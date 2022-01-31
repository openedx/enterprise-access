"""
Filter backends for Enterprise Access API.
"""

from django.db.models import Q
from edx_rbac import utils
from rest_framework import filters

from enterprise_access.apps.core import constants


class SubsidyRequestFilterBackend(filters.BaseFilterBackend):
    """
    Filter backend that returns subsidy requests that were either created for the user or created under
    enterprises that the user is an admin of.
    """

    def filter_queryset(self, request, queryset, view):
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
            Q(lms_user_id=lms_user_id_from_jwt) |
            Q(enterprise_customer_uuid__in=accessible_enterprises_as_admin)
        )

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

        return queryset.filter(
            Q(enterprise_customer_uuid__in=accessible_enterprises_as_admin.union(accessible_enterprises_as_learner))
        )
