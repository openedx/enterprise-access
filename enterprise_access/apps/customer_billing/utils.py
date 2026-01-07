"""
Utility functions for customer billing app
"""

import datetime
from typing import Union

import pytz
from django.utils import timezone


def datetime_from_timestamp(timestamp: Union[int, float]) -> datetime.datetime:
    """
    Convert a Unix timestamp (seconds since epoch) into a timezone-aware UTC datetime.

    This function:
    - Interprets the input timestamp as seconds since the Unix epoch (1970-01-01T00:00:00).
    - Creates a ``datetime.datetime`` from the timestamp.
    - Converts the result into a timezone-aware datetime explicitly set to UTC.

    Args:
        timestamp (Union[int, float]): Unix timestamp in seconds.

    Returns:
        datetime.datetime: A timezone-aware datetime object with tzinfo set to UTC.

    Guarantees:
        - The returned datetime is always timezone-aware.
        - The timezone is explicitly UTC (pytz.UTC).
        - Safe for storage, comparison, and cross-system serialization.

    Raises:
        (none): Any exceptions originate from invalid timestamp values passed to ``fromtimestamp``.
    """
    dt = datetime.datetime.fromtimestamp(timestamp)
    return timezone.make_aware(dt, timezone=pytz.UTC)
