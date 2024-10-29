"""
Constants for API client
"""
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeoutError

autoretry_for_exceptions = (
    RequestsConnectionError,
    RequestsTimeoutError,
)


class LicenseStatuses:
    """
    Statuses defined for the "status" field in the License model.
    """
    ACTIVATED = 'activated'
    ASSIGNED = 'assigned'
    UNASSIGNED = 'unassigned'
    REVOKED = 'revoked'
    REVOCABLE_STATUSES = [ACTIVATED, ASSIGNED]
