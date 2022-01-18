""" Constants for the core app. """

# Role-based access control
REQUESTS_ADMIN_ROLE = 'enterprise_access_requests_admin'
REQUESTS_LEARNER_ROLE = 'enterprise_access_requests_learner'

SYSTEM_ENTERPRISE_ADMIN_ROLE = 'enterprise_admin'
SYSTEM_ENTERPRISE_LEARNER_ROLE = 'enterprise_learner'
SYSTEM_ENTERPRISE_OPERATOR_ROLE = 'enterprise_openedx_operator'

REQUESTS_ADMIN_ACCESS_PERMISSION = 'requests.has_admin_access'
REQUESTS_ADMIN_LEARNER_ACCESS_PERMISSION = 'requests.has_learner_or_admin_access'

class Status:
    """Health statuses."""
    OK = "OK"
    UNAVAILABLE = "UNAVAILABLE"
