"""
Util classes and methods for producing enterprise-access events to the event bus. Likely
temporary.
"""

import logging

from confluent_kafka import KafkaError, KafkaException, SerializingProducer
from confluent_kafka.admin import AdminClient, NewTopic
from confluent_kafka.error import ValueSerializationError
from confluent_kafka.serialization import StringSerializer
from django.conf import settings

from enterprise_access.apps.events.data import (
    AccessPolicyEvent,
    AccessPolicyEventSerializer,
    CouponCodeRequestEvent,
    CouponCodeRequestEventSerializer,
    SubsidyRedemptionEvent,
    SubsidyRedemptionSerializer
)

logger = logging.getLogger(__name__)


class ProducerFactory:
    """
    Factory class to create event producers.
    The factory pattern is used to ensure only one producer per event type, which is the confluent recommendation.
    """
    _type_to_producer = {}

    @classmethod
    def get_or_create_event_producer(cls, event_type, event_key_serializer, event_value_serializer):
        """
        Factory method to return the correct producer for the event type, or
        create a new producer if none exists
        :param event_type: name of event (same as segment events)
        :param event_key_serializer:  AvroSerializer instance for serializing event key
        :param event_value_serializer: AvroSerializer instance for serializing event value
        :return: SerializingProducer
        """
        existing_producer = cls._type_to_producer.get(event_type)
        if existing_producer is not None:
            return existing_producer

        producer_settings = {
            'bootstrap.servers': settings.KAFKA_BOOTSTRAP_SERVER,
            'key.serializer': event_key_serializer,
            'value.serializer': event_value_serializer,
        }

        if settings.KAFKA_API_KEY and settings.KAFKA_API_SECRET:
            producer_settings.update({
                'sasl.mechanism': 'PLAIN',
                'security.protocol': 'SASL_SSL',
                'sasl.username': settings.KAFKA_API_KEY,
                'sasl.password': settings.KAFKA_API_SECRET,
            })

        new_producer = SerializingProducer(producer_settings)
        cls._type_to_producer[event_type] = new_producer
        return new_producer


def create_topics(topic_names):
    """
    Create topics in the event bus
    :param topic_names: topics to create
    """
    KAFKA_ACCESS_CONF_BASE = { 'bootstrap.servers': settings.KAFKA_BOOTSTRAP_SERVER }

    if settings.KAFKA_API_KEY and settings.KAFKA_API_SECRET:
        KAFKA_ACCESS_CONF_BASE.update({
            'sasl.mechanism': 'PLAIN',
            'security.protocol': 'SASL_SSL',
            'sasl.username': settings.KAFKA_API_KEY,
            'sasl.password': settings.KAFKA_API_SECRET,
        })

    admin_client = AdminClient(KAFKA_ACCESS_CONF_BASE)

    topics = [
        NewTopic(
            topic_name,
            num_partitions=settings.KAFKA_PARTITIONS_PER_TOPIC,
            replication_factor=settings.KAFKA_REPLICATION_FACTOR_PER_TOPIC
        ) for topic_name in topic_names
    ]

    # Call create_topics to asynchronously create topic.
    # Wait for each operation to finish.
    topic_futures = admin_client.create_topics(topics)

    # TODO: (ARCHBOM-2004) programmatically update permissions so the calling app can write to the created topic
    # ideally we could check beforehand if the topic already exists instead of using exceptions as control flow
    # but that is not in the AdminClient API
    for topic, f in topic_futures.items():
        try:
            f.result()  # The result itself is None
            logger.info(f"Topic {topic} created")
        except KafkaException as ke:
            if ke.args[0].code() == KafkaError.TOPIC_ALREADY_EXISTS:
                logger.info(f"Topic {topic} already exists")
            else:
                raise


def send_coupon_code_request_event_to_event_bus(event_name, event_properties):
    """
    Sends a coupon code request event to the event bus.
    """
    try:
        event_producer = ProducerFactory.get_or_create_event_producer(
            settings.COUPON_CODE_REQUEST_TOPIC_NAME,
            StringSerializer('utf-8'),
            CouponCodeRequestEventSerializer.get_serializer()
        )
        event_producer.produce(
            settings.COUPON_CODE_REQUEST_TOPIC_NAME,
            key=str(event_name),
            value=CouponCodeRequestEvent(**event_properties),
            on_delivery=verify_event
        )
        event_producer.poll()
    except ValueSerializationError as vse:
        logger.exception(vse)


def send_access_policy_event_to_event_bus(event_name, event_properties):
    """
    Sends access policy event to the event bus.
    """
    if settings.KAFKA_ENABLED:  # pragma: no cover
        try:
            event_producer = ProducerFactory.get_or_create_event_producer(
                settings.ACCESS_POLICY_TOPIC_NAME,
                StringSerializer('utf-8'),
                AccessPolicyEventSerializer.get_serializer()
            )
            event_producer.produce(
                settings.ACCESS_POLICY_TOPIC_NAME,
                key=str(event_name),
                value=AccessPolicyEvent(**event_properties),
                on_delivery=verify_event
            )
            event_producer.poll()
        except ValueSerializationError as vse:
            logger.exception(vse)


def send_subsidy_redemption_event_to_event_bus(event_name, event_properties):
    """
    Sends subsidy redemption and reversal events to the event bus.
    """
    if settings.KAFKA_ENABLED:  # pragma: no cover
        try:
            event_producer = ProducerFactory.get_or_create_event_producer(
                settings.SUBSIDY_REDEMPTION_TOPIC_NAME,
                StringSerializer('utf-8'),
                SubsidyRedemptionSerializer.get_serializer()
            )
            event_producer.produce(
                settings.SUBSIDY_REDEMPTION_TOPIC_NAME,
                key=str(event_name),
                value=SubsidyRedemptionEvent(**event_properties),
                on_delivery=verify_event
            )
            event_producer.poll()
        except ValueSerializationError as vse:
            logger.exception(vse)


def verify_event(err, evt):
    """
    Simple callback method for debugging event production.
    :param err: Error if event production failed
    :param evt: Event that was delivered
    """
    if err is not None:
        logger.warning(f"Event delivery failed: {err}")
    else:
        logger.info(f"Event delivered to {evt.topic()}: key(bytes) - {evt.key()}; "
                    f"partition - {evt.partition()}")
