"""
Initialization app for enterprise_access.apps.events.
"""

import logging

from django.apps import AppConfig
from django.conf import settings

from enterprise_access.apps.events.utils import create_topics

logger = logging.getLogger(__name__)

class EventsConfig(AppConfig):
    """
    Application Configuration for the events app.
    """

    name = 'enterprise_access.apps.events'

    def ready(self):
        if settings.KAFKA_ENABLED:  # pragma: no cover
            try:
                topics = settings.KAFKA_TOPICS
                logger.info("Creating topics: %s", topics)
                create_topics(topics)
            except Exception:  # pylint: disable=broad-except
                logger.exception("Error creating topics.")
