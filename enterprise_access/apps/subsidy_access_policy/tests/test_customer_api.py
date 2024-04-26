"""
Tests for the customer_api.py functions caching behavior
"""

from unittest import mock
from uuid import uuid4

from django.test import TestCase

from enterprise_access.apps.subsidy_access_policy.customer_api import get_and_cache_enterprise_learner_record


class TestCustomerApi(TestCase):
    """
    Tests for the customer_api.py functions caching behavior
    """
    @mock.patch('enterprise_access.apps.api_client.lms_client.LmsApiClient.get_enterprise_user', autospec=True)
    def test_get_and_cache_enterprise_learner_record(self, mock_client):
        enterprise_customer_uuid = uuid4()
        learner_id = 12345

        mock_result = {
            'enterprise_customer': enterprise_customer_uuid,
            'learner_id': learner_id,
        }

        mock_client.return_value = mock_result

        enterprise_learner_record = get_and_cache_enterprise_learner_record(enterprise_customer_uuid, learner_id)
        self.assertEqual(mock_client.call_count, 1)
        self.assertEqual(enterprise_learner_record, mock_result)

        enterprise_learner_record = get_and_cache_enterprise_learner_record(enterprise_customer_uuid, learner_id)
        self.assertEqual(mock_client.call_count, 1)
        self.assertEqual(enterprise_learner_record, mock_result)

        new_learner_id = 54321

        get_and_cache_enterprise_learner_record(enterprise_customer_uuid, new_learner_id)
        self.assertEqual(mock_client.call_count, 2)
