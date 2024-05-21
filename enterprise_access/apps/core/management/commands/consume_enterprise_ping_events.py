"""
Produce a single event for enterprise-specific testing or health checks.

Implements required ``APP.management.commands.*.Command`` structure.
"""
import json
import logging
from argparse import RawTextHelpFormatter
from pprint import pformat

import attr
from django.conf import settings
from django.core.management.base import BaseCommand
from django.dispatch import receiver
from openedx_events.event_bus import make_single_consumer
from openedx_events.tooling import OpenEdxPublicSignal

logger = logging.getLogger(__name__)


# First define the topic that our consumer will subscribe to.
ENTERPRISE_CORE_TOPIC = getattr(settings, 'EVENT_BUS_ENTERPRISE_CORE_TOPIC', 'enterprise-core')


# Define the shape/schema of the data that our consumer will process.
# It should be identical to the schema used to *produce* the event.
@attr.s(frozen=True)
class PingData:
    """
    Attributes of a ping record.
    """
    ping_uuid = attr.ib(type=str)
    ping_message = attr.ib(type=str)


ENTERPRISE_PING_DATA_SCHEMA = {
    "ping": PingData,
}

# Define a Signal with the type (unique name) of the event to process,
# and tell it about the expected schema of event data. The producer of our ping events
# should emit an identical signal (same event_type and data schema).
ENTERPRISE_PING_SIGNAL = OpenEdxPublicSignal(
    event_type="org.openedx.enterprise.core.ping.v1",
    data=ENTERPRISE_PING_DATA_SCHEMA
)


# Create a receiver function to do the "processing" of the signal data.
@receiver(ENTERPRISE_PING_SIGNAL)
def handle_enterprise_ping_signal(sender, **kwargs):
    logger.info('RECEIVED PING DATA: %s', pformat(kwargs['ping']))


class Command(BaseCommand):
    """
    Mgmt command to consume enterprise ping events.
    """

    help = """
    Consume messages from the enterprise core topic and emit their data with
    a corresponding signal.

    Examples:

        ./manage.py consume_enterprise_ping_events -g enterprise-access-service

        # send extra args, for example pass check_backlog flag to redis consumer
        ./manage.py consume_enterprise_ping_events -g user-activity-service -g enterprise-access-service \\
            --extra '{"check_backlog": true}'

        # send extra args, for example replay events from specific redis msg id.
        ./manage.py consume_enterprise_ping_events -g enterprise-access-service \\
            --extra '{"last_read_msg_id": "1679676448892-0"}'
    """

    def add_arguments(self, parser):
        """
        Add arguments for parsing topic, group, and extra args.
        """
        parser.add_argument(
            '-g', '--group-id',
            nargs='?',
            required=False,
            type=str,
            default='enterprise-access-service',
            help='Consumer group id'
        )
        parser.add_argument(
            '--extra',
            nargs='?',
            type=str,
            required=False,
            help='JSON object to pass additional arguments to the consumer.'
        )

    def create_parser(self, *args, **kwargs):
        parser = super(Command, self).create_parser(*args, **kwargs)
        parser.formatter_class = RawTextHelpFormatter
        return parser

    def handle(self, *args, **options):
        """
        Create consumer based on django settings and consume events.
        """
        try:
            # load additional arguments specific for the underlying implementation of event_bus.
            extra = json.loads(options.get('extra') or '{}')
            event_consumer = make_single_consumer(
                topic=ENTERPRISE_CORE_TOPIC,
                group_id=options['group_id'],
                **extra,
            )
            event_consumer.consume_indefinitely()
        except Exception:  # pylint: disable=broad-except
            logger.exception("Error consuming events")
