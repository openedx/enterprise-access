"""
Initialization app for enterprise_access.apps.track.

"""


import analytics
from django.apps import AppConfig
from django.conf import settings


class TrackConfig(AppConfig):
    """
    Application Configuration for the track app.
    """

    name = 'enterprise_access.apps.track'

    def ready(self):
        """
        Initialize Segment analytics module by setting the write_key.
        """
        if hasattr(settings, "SEGMENT_KEY"):
            analytics.write_key = settings.SEGMENT_KEY
