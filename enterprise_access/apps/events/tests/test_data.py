"""
Tests for data module in events django application.
"""
from unittest import mock
from uuid import uuid4

from django.test import TestCase
from faker import Faker

from enterprise_access.apps.events.data import (
    AccessPolicyEvent,
    AccessPolicyEventSerializer,
    SubsidyRedemptionEvent,
    SubsidyRedemptionSerializer
)
from enterprise_access.apps.subsidy_access_policy.models import AccessMethods

FAKER = Faker()


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

    def test_subsidy_redemption_event(self):
        """
        Validate the behavior of SubsidyRedemptionEvent class.
        """
        subsidy_redemption_event_data = {
            'enterprise_uuid': uuid4(),
            'content_key': 'test-course',
            'lms_user_id': FAKER.pyint(),
        }

        subsidy_redemption_event = SubsidyRedemptionEvent.from_dict(subsidy_redemption_event_data, mock.MagicMock())

        assert SubsidyRedemptionEvent.to_dict(subsidy_redemption_event, mock.MagicMock()) == \
            subsidy_redemption_event_data

    @mock.patch('enterprise_access.apps.events.data.AvroSerializer', return_value=mock.MagicMock())
    def test_subsidy_event_serializer(self, mock_avro_serializer):
        """
        Validate the behavior of SubsidyRedemptionSerializer.
        """
        serializer = SubsidyRedemptionSerializer.get_serializer()
        assert mock_avro_serializer.call_count == 1

        # Verify subsequent calls return the same serializer.
        assert SubsidyRedemptionSerializer.get_serializer() == serializer
        assert mock_avro_serializer.call_count == 1
