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
