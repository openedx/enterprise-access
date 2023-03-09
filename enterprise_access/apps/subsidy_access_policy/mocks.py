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

class subsidy_client():
    """
    API client for the subsidy service.
    """
    @classmethod
    def can_redeem(cls, subsidy_uuid, learner_id, content_key):
        """
        Subsidy service api
        enterprise-subsidy service call:
        GET /api/v1/subsidies/[subsidy-uuid]/can_redeem/

        TODO: Does not yet have an implementation in enterprise-subsidy.
        """
        return mock.MagicMock(subsidy_uuid, learner_id, content_key)

    @classmethod
    def redeem(cls, subsidy_uuid, learner_id, content_key):
        """
        Subsidy service api

        POST /api/v1/transactions/
        This has an implementation.
        """
        return mock.MagicMock(subsidy_uuid, learner_id, content_key)

    @classmethod
    def request_redemption(cls, subsidy_uuid, learner_id, content_key):
        """
        Subsidy service api
        This is about browse and request, we don't need this for MVP.
        """
        return mock.MagicMock(subsidy_uuid, learner_id, content_key)

    @classmethod
    def has_redeemed(cls, subsidy_uuid, learner_id, content_key):
        """
        Subsidy service api

        Theoretically GET /api/v1/transactions/?subsidy_uuid=...&content_key=...&learner_id=...

        TODO: there's a list endpoint, but no filtering support yet.
        """
        return mock.MagicMock(subsidy_uuid, learner_id, content_key)

    @classmethod
    def has_requested(cls, subsidy_uuid, learner_id, content_key):
        """
        Don't need this for MVP.
        Subsidy service api
        """
        return mock.MagicMock(subsidy_uuid, learner_id, content_key)

    @classmethod
    def get_license_for_learner(cls, subsidy_uuid, learner_id):
        """
        Don't need this for MVP.
        Subsidy service api
        """
        return mock.MagicMock(subsidy_uuid, learner_id)

    @classmethod
    def get_license_for_group(cls, subsidy_uuid, group_uuid):
        """
        Don't need this for MVP.
        Subsidy service api
        """
        return mock.MagicMock(subsidy_uuid, group_uuid)

    @classmethod
    def transactions_for_learner(cls, subsidy_uuid, learner_id):
        """
        Subsidy service api

        Theoretically GET /api/v1/transactions/?subsidy_uuid=...&content_key=...&learner_id=...

        TODO: there's a list endpoint, but no filtering support yet (in this case for learner_id)
        """
        return mock.MagicMock(subsidy_uuid, learner_id)

    @classmethod
    def amount_spent_for_learner(cls, subsidy_uuid, learner_id):
        """
        Subsidy service api
        This might be something that just calls transactions_for_learner() and sums the quantities.
        """
        return mock.MagicMock(subsidy_uuid, learner_id)

    @classmethod
    def amount_spent_for_group_and_catalog(cls, subsidy_uuid, group_uuid, catalog_uuid):
        """
        Don't need for MVP because there's no group yet, and get_current_balance() below
        will satisfy knowing the entire balance for a customer's subsidy.
        Subsidy service api
        """
        return mock.MagicMock(subsidy_uuid, group_uuid, catalog_uuid)

    @classmethod
    def get_current_balance(cls, subsidy_uuids):
        """
        Returns current balance for each subsidy

        Will call /api/v1/subsidies/{subsidy_uuid}

        TODO: need to serialize the current balance into the response payload.
        """
        return mock.MagicMock(subsidy_uuids)
