"""
Utilities for REST API View and Viewset classes.
"""
from django.conf import settings
from edx_rest_framework_extensions.paginators import DefaultPagination


class PaginationWithPageCount(DefaultPagination):
    """
    A PageNumber paginator that adds the total number of pages
    and the current page to the paginated response.
    """

    page_size_query_param = 'page_size'
    # The configured `PAGE_SIZE` in Django settings must be used as the default page size as opposed
    # to relying on the default `page_size` specified via `DefaultPagination` in order to maintain
    # backwards compatibility with existing API clients.
    page_size = settings.REST_FRAMEWORK.get('PAGE_SIZE')
    max_page_size = 500


class OptionalPaginationWithPageCount(PaginationWithPageCount):
    """
    Optionally allows callers to disable pagination by setting the (configurable) `no_page` query param.
    """
    no_page_query_param = 'no_page'

    def formatted_no_page_query_param(self, request):
        """
        Parses the query parameters for `self.no_page_query_param` and transforms
        its value to a lowercase string, if present.
        """
        no_page_query_param = request.query_params.get(self.no_page_query_param)
        formatted_no_page_query_param = no_page_query_param.lower() if no_page_query_param else no_page_query_param
        return formatted_no_page_query_param

    def has_non_paginated_response(self, request):
        """
        Determines whether the given `self.no_page_query_param` query param has a value that indicates
        that pagination should be disabled for the request.
        """
        no_page_query_param = self.formatted_no_page_query_param(request)
        return no_page_query_param in ('true', '1')

    def paginate_queryset(self, queryset, request, view=None):
        """
        Paginates a queryset, if required. Allows disabling pagination by setting
        the `no_page` query param, supporting use cases where API callers may not be
        able to rely on pagination (e.g., if more than 1 page of results could be expected
        for a use case where the client can't traverse all pages and passing a "sufficiently high"
        `page_size` query parameter is too naive of a solution).
        """
        # Determine whether to paginate the queryset based on the value of the
        # `no_page` query param. If True, do not paginate the response.
        if self.has_non_paginated_response(request):
            return None

        # Perform standard pagination.
        return super().paginate_queryset(queryset, request, view)
