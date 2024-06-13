"""
Base task that includes retries for different error types.
"""

from braze.exceptions import BrazeClientError
from celery_utils.logged_task import LoggedTask
from django.conf import settings
from django.db import OperationalError
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import HTTPError
from requests.exceptions import Timeout as RequestsTimeoutError


class LoggedTaskWithRetry(LoggedTask):  # pylint: disable=abstract-method
    """
    Shared base task that allows tasks that raise some common exceptions to retry automatically.
    See https://docs.celeryproject.org/en/stable/userguide/tasks.html#automatic-retry-for-known-exceptions for
    more documentation.
    """
    autoretry_for = (
        RequestsConnectionError,
        RequestsTimeoutError,
        HTTPError,
        OperationalError,
        BrazeClientError,
    )
    # The default number of max_retries is 3, but Braze times out a lot so we will use 5 instead.
    # 5 retries means retrying potentially up to ~31 minutes:
    # (2⁰ + 2¹ + 2² + 2³ + 2⁴) × (60 seconds) = 31 min
    retry_kwargs = {'max_retries': settings.TASK_MAX_RETRIES}
    # Use exponential backoff for retrying tasks
    # see https://docs.celeryq.dev/en/stable/userguide/tasks.html#Task.retry_backoff
    # First retry will delay 60 seconds, second will delay 120 seconds, third 240 seconds.
    # The retry_backoff_max default value is 600 seconds -> 10 minutes
    # https://docs.celeryq.dev/en/stable/userguide/tasks.html#Task.retry_backoff_max
    # This will result in the final backoff time reducing from 2⁴ × 60 seconds = 960 seconds to 600 seconds
    retry_backoff = 60
    # Add randomness to backoff delays to prevent all tasks in queue from executing simultaneously
    retry_jitter = True
