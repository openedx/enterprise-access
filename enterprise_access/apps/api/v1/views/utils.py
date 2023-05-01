"""
Utilities for REST API View and Viewset classes.
"""
from rest_framework.pagination import PageNumberPagination


class PaginationWithPageCount(PageNumberPagination):
    """
    A PageNumber paginator that adds the total number of pages to the paginated response.
    """

    page_size_query_param = 'page_size'
    max_page_size = 500

    def get_paginated_response(self, data):
        """ Adds a ``num_pages`` field into the paginated response. """
        response = super().get_paginated_response(data)
        response.data['num_pages'] = self.page.paginator.num_pages
        return response
