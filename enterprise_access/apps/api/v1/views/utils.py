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

    def get_paginated_response_schema(self, schema):
        """
        Annotate the paginated response schema with the extra fields provided by edx_rest_framework_extensions'
        DefaultPagination class (e.g., `page_count`), ensuring these extra fields show up in the DRF Spectacular
        generated docs.
        """
        return {
            'type': 'object',
            'properties': {
                'count': {
                    'type': 'integer',
                    'description': 'The total number of items across all pages',
                    'example': 123,
                },
                'page_count': {
                    'type': 'integer',
                    'description': 'The total number of pages',
                    'example': 3,
                },
                'page_size': {
                    'type': 'integer',
                    'description': 'The number of items per page',
                    'example': 50,
                },
                'current_page': {
                    'type': 'integer',
                    'description': 'The current page number',
                    'example': 1,
                },
                'next': {
                    'type': 'string',
                    'description': 'Link to the next page of results',
                    'nullable': True,
                    'format': 'uri',
                },
                'previous': {
                    'type': 'string',
                    'description': 'Link to the previous page of results',
                    'nullable': True,
                    'format': 'uri',
                },
                'results': schema,
            },
        }
