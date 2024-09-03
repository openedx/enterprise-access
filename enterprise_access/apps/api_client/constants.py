"""
Constants for API client
"""
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeoutError

autoretry_for_exceptions = (
    RequestsConnectionError,
    RequestsTimeoutError,
)
