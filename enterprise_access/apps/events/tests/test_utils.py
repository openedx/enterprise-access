"""
Unit tests for the utility functions.
"""
from unittest import mock
from unittest.mock import ANY
from uuid import uuid4

from confluent_kafka.error import ValueSerializationError
from django.conf import settings
from django.test import TestCase
from django.test.utils import override_settings
from faker import Faker

from enterprise_access.apps.events.signals import ACCESS_POLICY_CREATED, SUBSIDY_REDEEMED
from enterprise_access.apps.events.utils import (
    send_access_policy_event_to_event_bus,
    send_subsidy_redemption_event_to_event_bus,
    verify_event
)
from enterprise_access.apps.subsidy_access_policy.models import AccessMethods

FAKER = Faker()


class UtilsTests(TestCase):
    """
    Unit tests for the utility functions.
    """
    @override_settings(KAFKA_ENABLED=True)
    @mock.patch('enterprise_access.apps.events.utils.logger', return_value=mock.MagicMock())
    @mock.patch('enterprise_access.apps.events.utils.SerializingProducer', return_value=mock.MagicMock())
    def test_send_access_policy_event_to_event_bus(self, mock_serializing_producer, mock_logger):
        """
        Validate the behavior of send_access_policy_event_to_event_bus utility method.
        """
        access_policy_event_data = {
            'uuid': uuid4(),
            'active': True,
            'group_uuid': uuid4(),
            'subsidy_uuid': uuid4(),
            'access_method': AccessMethods.DIRECT,
        }

        send_access_policy_event_to_event_bus(ACCESS_POLICY_CREATED.event_type, access_policy_event_data)

        mock_serializing_producer().produce.assert_any_call(
            settings.ACCESS_POLICY_TOPIC_NAME,
            key=str(ACCESS_POLICY_CREATED.event_type),
            value=ANY,
            on_delivery=verify_event
        )

        assert mock_serializing_producer().poll.call_count == 1

        mock_serializing_producer().poll.side_effect = ValueSerializationError

        send_access_policy_event_to_event_bus(ACCESS_POLICY_CREATED.event_type, access_policy_event_data)

        mock_serializing_producer().produce.assert_any_call(
            settings.ACCESS_POLICY_TOPIC_NAME,
            key=str(ACCESS_POLICY_CREATED.event_type),
            value=ANY,
            on_delivery=verify_event
        )
        assert mock_logger.exception.call_count == 1

    @override_settings(KAFKA_ENABLED=True)
    @mock.patch('enterprise_access.apps.events.utils.logger', return_value=mock.MagicMock())
    @mock.patch('enterprise_access.apps.events.utils.SerializingProducer', return_value=mock.MagicMock())
    def test_send_subsidy_redemption_event_to_event_bus(self, mock_serializing_producer, mock_logger):
        """
        Validate the behavior of send_access_policy_event_to_event_bus utility method.
        """
        subsidy_redemption_event_data = {
            'enterprise_uuid': uuid4(),
            'content_key': 'test-course',
            'lms_user_id': FAKER.pyint(),
        }

        send_subsidy_redemption_event_to_event_bus(SUBSIDY_REDEEMED.event_type, subsidy_redemption_event_data)

        mock_serializing_producer().produce.assert_any_call(
            settings.SUBSIDY_REDEMPTION_TOPIC_NAME,
            key=str(SUBSIDY_REDEEMED.event_type),
            value=ANY,
            on_delivery=verify_event
        )

        assert mock_serializing_producer().poll.call_count == 1

        mock_serializing_producer().poll.side_effect = ValueSerializationError

        send_subsidy_redemption_event_to_event_bus(SUBSIDY_REDEEMED.event_type, subsidy_redemption_event_data)

        mock_serializing_producer().produce.assert_any_call(
            settings.SUBSIDY_REDEMPTION_TOPIC_NAME,
            key=str(SUBSIDY_REDEEMED.event_type),
            value=ANY,
            on_delivery=verify_event
        )
        assert mock_logger.exception.call_count == 1
