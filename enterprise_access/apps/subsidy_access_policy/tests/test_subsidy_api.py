"""
Tests for the subsidy_api module.
"""
import uuid
from unittest import mock

from django.test import TestCase

from ..subsidy_api import get_and_cache_transactions_for_learner


class TransactionsForLearnerTests(TestCase):
    """
    Tests the ``get_and_cache_transactions_for_learner`` function.
    """
    @mock.patch('enterprise_access.apps.subsidy_access_policy.subsidy_api.get_versioned_subsidy_client')
    def test_request_caching_works(self, mock_client_getter):
        """
        Test that we utilize the request cache.
        """
        response_payload = {
            'next': None,
            'previous': None,
            'count': 1,
            'results': [{'thing': 3}],
        }
        mock_client = mock_client_getter.return_value
        mock_client.list_subsidy_transactions.return_value = response_payload

        subsidy_uuid = uuid.uuid4()
        lms_user_id = 42

        result = get_and_cache_transactions_for_learner(subsidy_uuid, lms_user_id)

        expected_result = {
            'transactions': [{'thing': 3}],
            'aggregates': {},
        }
        self.assertEqual(result, expected_result)

        # call it again, should be using the cache this time
        next_result = get_and_cache_transactions_for_learner(subsidy_uuid, lms_user_id)

        self.assertEqual(next_result, expected_result)

        # we should only have used the client in the first call
        mock_client.list_subsidy_transactions.assert_called_once_with(
            subsidy_uuid=subsidy_uuid,
            lms_user_id=lms_user_id,
            include_aggregates=False,
        )
        # no pagination happened here
        self.assertFalse(mock_client.client.get.called)

    @mock.patch('enterprise_access.apps.subsidy_access_policy.subsidy_api.get_versioned_subsidy_client')
    def test_multiple_pages_are_traversed(self, mock_client_getter):
        """
        Test that we read multiple pages of data, if present
        in the client's response for ``list_subsidy_transactions()``.
        """
        first_response_payload = {
            'next': 'http://the.next.page',
            'previous': None,
            'count': 12,
            'results': [{'thing': 1}, {'thing': 2}],
        }
        second_response_payload = {
            'next': None,
            'previous': None,
            'count': 3,
            'results': [{'thing': 3}],
        }
        mock_client = mock_client_getter.return_value
        mock_client.list_subsidy_transactions.return_value = first_response_payload
        mock_client.client.get.return_value = second_response_payload

        subsidy_uuid = uuid.uuid4()
        lms_user_id = 42

        result = get_and_cache_transactions_for_learner(subsidy_uuid, lms_user_id)

        expected_result = {
            'transactions': [{'thing': 1}, {'thing': 2}, {'thing': 3}],
            'aggregates': {},
        }
        self.assertEqual(result, expected_result)
        mock_client.list_subsidy_transactions.assert_called_once_with(
            subsidy_uuid=subsidy_uuid,
            lms_user_id=lms_user_id,
            include_aggregates=False,
        )
        mock_client.client.get.assert_called_once_with(first_response_payload['next'])
