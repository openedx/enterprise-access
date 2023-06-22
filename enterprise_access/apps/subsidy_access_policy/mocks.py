""" Mock clients for subsidy_access_policy models. """

from unittest import mock


class catalog_client():
    """
    API client for the enterprise-catalog service.
    """
    @classmethod
    def catalog_contains_content(cls, catalog_uuid, content_key):
        """Catalog service api"""
        return mock.MagicMock(catalog_uuid, content_key)

    @classmethod
    def get_course_price(cls, content_key):
        """Catalog service api"""
        return mock.MagicMock(content_key)
