"""
Tests for the subsidy_api module.
"""
import uuid
from unittest import mock

from django.test import TestCase

from ..subsidy_api import get_and_cache_transactions_for_learner, get_redemptions_by_content_and_policy_for_learner
from .factories import PerLearnerSpendCapLearnerCreditAccessPolicyFactory


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
        mock_second_response = mock.Mock()
        mock_second_response.json.return_value = second_response_payload
        mock_client.client.get.return_value = mock_second_response

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

    @mock.patch('enterprise_access.apps.subsidy_access_policy.subsidy_api.get_and_cache_transactions_for_learner')
    def test_redemptions_by_content_and_policy(self, mock_transaction_cache):
        cake_subsidy_uuid = uuid.uuid4()
        pie_subsidy_uuid = uuid.uuid4()

        cherry_policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(subsidy_uuid=pie_subsidy_uuid)
        apple_policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(subsidy_uuid=pie_subsidy_uuid)

        german_chocolate_policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(subsidy_uuid=cake_subsidy_uuid)

        # The transaction uuids and content keys don't really matter much here, they don't even need
        # to be proper uuids, just unique amongst this list of test data.
        mock_pie_transactions = [
            {
                'uuid': 'alpha',
                'content_key': 'content-1',
                'subsidy_access_policy_uuid': str(cherry_policy.uuid),
            },
            {
                'uuid': 'beta',
                'content_key': 'content-2',
                'subsidy_access_policy_uuid': str(apple_policy.uuid),
            },
        ]
        mock_cake_transactions = [
            {
                'uuid': 'delta',
                'content_key': 'content-3',
                'subsidy_access_policy_uuid': str(german_chocolate_policy.uuid),
            },
            # Add some unmatched policy uuid in here,
            # which we'll later verify is omitted from the mapping.
            {
                'uuid': 'epsilon',
                'content_key': 'content-4',
                'subsidy_access_policy_uuid': str(uuid.uuid4()),
            },
        ]

        mock_transaction_cache.side_effect = [
            {'transactions': mock_pie_transactions, 'aggregates': {}},
            {'transactions': mock_cake_transactions, 'aggregates': {}},
        ]

        result = get_redemptions_by_content_and_policy_for_learner(
            [cherry_policy, apple_policy, german_chocolate_policy],
            123,
        )

        self.assertEqual(
            {
                'content-1': {str(cherry_policy.uuid): [mock_pie_transactions[0]]},
                'content-2': {str(apple_policy.uuid): [mock_pie_transactions[1]]},
                'content-3': {str(german_chocolate_policy.uuid): [mock_cake_transactions[0]]},
            },
            result,
        )
