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
    retry_kwargs = {'max_retries': settings.TASK_MAX_RETRIES}
    # Use exponential backoff for retrying tasks
    retry_backoff = 5  # delay factor of 5 seconds
    # Add randomness to backoff delays to prevent all tasks in queue from executing simultaneously
    retry_jitter = True
