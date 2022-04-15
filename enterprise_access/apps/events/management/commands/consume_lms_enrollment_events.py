"""
Management command for listening to lms enrollment events and logging them
"""

import logging

from confluent_kafka import DeserializingConsumer, KafkaError
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import StringDeserializer
from django.conf import settings
from django.core.management.base import BaseCommand
from edx_toggles.toggles import SettingToggle

from events.data import CourseEnrollmentEvent


logger = logging.getLogger(__name__)

# .. toggle_name: KAFKA_CONSUMERS_ENABLED
# .. toggle_implementation: SettingToggle
# .. toggle_default: False
# .. toggle_description: Enables the ability to listen and process events from the Kafka event bus
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2022-01-31
# .. toggle_tickets: https://openedx.atlassian.net/browse/ARCHBOM-1992
KAFKA_CONSUMERS_ENABLED = SettingToggle('KAFKA_CONSUMERS_ENABLED', default=True)

CONSUMER_POLL_TIMEOUT = getattr(settings, 'CONSUMER_POLL_TIMEOUT', 1.0)


# TODO (EventBus): Revisit all the todos in this file.


class Command(BaseCommand):
    """
    Listen for events from the event bus and log them. Only run on servers where KAFKA_CONSUMERS_ENABLED is true
    """
    help = """
    This starts a Kafka event consumer that listens to the specified topic and logs all messages it receives. Topic
    is required.
    example:
        manage.py ... consume_lms_enrollment_events -t lms-enrollments-prod -g lms_enrollments_prod
    # TODO (EventBus): Add pointer to relevant future docs around topics and consumer groups, and potentially
    update example topic and group names to follow any future naming conventions.
    """

    def add_arguments(self, parser):

        parser.add_argument(
            '-t', '--topic',
            nargs=1,
            required=True,
            help='Topic to consume'
        )

        parser.add_argument(
            '-g', '--group_id',
            nargs=1,
            required=True,
            help='Consumer group id'
        )

    def create_consumer(self, group_id):
        """
        Create a consumer for CourseEnrollmentEvent
        :param group_id: id of the consumer group this consumer will be part of
        :return: DeserializingConsumer
        """

        KAFKA_SCHEMA_REGISTRY_CONFIG = {
            'url': settings.SCHEMA_REGISTRY_URL,
            'basic.auth.user.info': f"{settings.SCHEMA_REGISTRY_API_KEY}:{settings.SCHEMA_REGISTRY_API_SECRET}",
        }

        schema_registry_client = SchemaRegistryClient(KAFKA_SCHEMA_REGISTRY_CONFIG)

        # TODO (EventBus):
        # 1. Reevaluate if all consumers should listen for the earliest unprocessed offset (auto.offset.reset)
        # 2. Use Avro <-> Attr bridge to deserialize and/or throw an error if we don't know how to
        #    deserialize messages in a particular topic. This will depend heavily on the exact API of the
        #    Avro <-> Attr bridge, which is still under development

        consumer_config = {
            'bootstrap.servers': settings.KAFKA_BOOTSTRAP_SERVER,
            'group.id': group_id,
            'key.deserializer': StringDeserializer('utf-8'),
            'value.deserializer': AvroDeserializer(schema_str=CourseEnrollmentEvent.AVRO_SCHEMA,
                                                   schema_registry_client=schema_registry_client,
                                                   from_dict=CourseEnrollmentEvent.from_dict),
            'auto.offset.reset': 'earliest'
        }

        if settings.KAFKA_API_KEY and settings.KAFKA_API_SECRET:
            consumer_config.update({
                'sasl.mechanism': 'PLAIN',
                'security.protocol': 'SASL_SSL',
                'sasl.username': settings.KAFKA_API_KEY,
                'sasl.password': settings.KAFKA_API_SECRET,
            })

        return DeserializingConsumer(consumer_config)

    def handle_message(self, msg):
        """
        Place holder methods for how to handle an incoming message from the event bus
        """
        # TODO (EventBus):
        # Rewrite this to construct and/or emit the signal eventually specified in the message.
        course_enrollment_dict = CourseEnrollmentEvent.to_dict(msg.value(), {})

        logger.info(
            f"Received message with key {msg.key()} and value {course_enrollment_dict}"
        )

        try:
            course_key = course_enrollment_dict['course_key']
            lms_user_id = course_enrollment_dict['lms_user_id']
            logger.info("Observed enrollment in couse %s for user %s", course_key, lms_user_id)

            # handle redemption/fulfillment here...
        except Exception as ex:  # pylint: disable=broad-except
            logger.exception(ex)

    def process_single_message(self, msg):
        """
        Handle message error or pass along for processing.
        """

        if msg is None:
            return
        if msg.error():
            # TODO (EventBus): iterate on error handling with retry and dead-letter queue topics
            if msg.error().code() == KafkaError._PARTITION_EOF:  # pylint: disable=protected-access
                # End of partition event
                logger.info(f"{msg.topic()} [{msg.partition()}] reached end at offset {msg.offset}")
            else:
                logger.exception(msg.error())
            return
        self.handle_message(msg)

    def handle(self, *args, **options):
        if not KAFKA_CONSUMERS_ENABLED.is_enabled():
            logger.error("Kafka consumers not enabled")
            return
        try:
            topic = options['topic'][0]
            group_id = options['group_id'][0]
            consumer = self.create_consumer(group_id)

            try:
                consumer.subscribe([topic])

                # TODO (EventBus):
                # 1. Is there an elegant way to exit the loop?
                # 2. Determine if there are other errors that shouldn't kill the entire loop
                while True:
                    msg = consumer.poll(timeout=CONSUMER_POLL_TIMEOUT)
                    self.process_single_message(msg)
            finally:
                # Close down consumer to commit final offsets.
                consumer.close()
                logger.info("Committing final offsets")
        except Exception:  # pylint: disable=broad-except
            logger.exception("Error consuming Kafka events")
