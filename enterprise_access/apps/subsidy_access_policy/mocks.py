""" Mock clients for subsidy_access_policy models. """

import mock


class group_client():
    """
    API client for the groups service.
    """
    @classmethod
    def group_contains_learner(cls, group_uuid, learner_id):
        """Group service api"""
        return mock.MagicMock(group_uuid, learner_id)

    @classmethod
    def get_groups_for_learner(cls, learner_id):
        """Group service api"""
        return mock.MagicMock(learner_id)


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
