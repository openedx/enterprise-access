"""
Tests for data module in events django application.
"""
from uuid import uuid4

import mock
from django.test import TestCase

from enterprise_access.apps.events.data import AccessPolicyEvent, AccessPolicyEventSerializer
from enterprise_access.apps.subsidy_access_policy.models import AccessMethods


class DataTests(TestCase):
    """
    Unit tests for data module in events django application..
    """

    def test_access_policy_event(self):
        """
        Validate the behavior of AccessPolicyEvent class.
        """
        access_policy_event_data = {
            'uuid': uuid4(),
            'active': True,
            'group_uuid': uuid4(),
            'subsidy_uuid': uuid4(),
            'access_method': AccessMethods.DIRECT,
        }

        access_policy_event = AccessPolicyEvent.from_dict(access_policy_event_data, mock.MagicMock())

        assert AccessPolicyEvent.to_dict(access_policy_event, mock.MagicMock()) == access_policy_event_data

    @mock.patch('enterprise_access.apps.events.data.AvroSerializer', return_value=mock.MagicMock())
    def test_access_policy_event_serializer(self, mock_avro_serializer):
        """
        Validate the behavior of AccessPolicyEventSerializer.
        """
        serializer = AccessPolicyEventSerializer.get_serializer()
        assert mock_avro_serializer.call_count == 1

        # Verify subsequent calls return the same serializer.
        assert AccessPolicyEventSerializer.get_serializer() == serializer
        assert mock_avro_serializer.call_count == 1
