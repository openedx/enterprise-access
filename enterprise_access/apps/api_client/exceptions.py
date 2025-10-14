"""
Custom Exception classes for Enterprise Access API client.
"""
from logging import getLogger

logger = getLogger(__name__)


def safe_error_response_content(exception_object):
    """
    Helper to safely fetch http error response content.
    """
    try:
        response_content = getattr(exception_object.response, 'content', None)
        if response_content:
            return response_content.decode()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning('Could not determine response content for %s', exc)
        return None
    return None


class APIClientException(Exception):
    """
    An exception wrapper that injects error response payload
    into the exception message.
    """
    code = 'api_client_exception'

    def __init__(self, message, exc):
        self.message = message
        self.message += f'\nresponse content: {safe_error_response_content(exc)}'
        super().__init__(self.message)


class FetchGroupMembersConflictingParamsException(Exception):
    """
    An exception indicating that a subsidy request cannot be created.
    """
