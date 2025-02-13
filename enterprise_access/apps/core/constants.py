""" Constants for the core app. """

# Role-based access control
REQUESTS_ADMIN_ROLE = 'enterprise_access_requests_admin'
REQUESTS_LEARNER_ROLE = 'enterprise_access_requests_learner'

SYSTEM_ENTERPRISE_ADMIN_ROLE = 'enterprise_admin'
SYSTEM_ENTERPRISE_LEARNER_ROLE = 'enterprise_learner'
SYSTEM_ENTERPRISE_OPERATOR_ROLE = 'enterprise_openedx_operator'
SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE = 'enterprise_provisioning_admin'

REQUESTS_ADMIN_ACCESS_PERMISSION = 'requests.has_admin_access'
REQUESTS_ADMIN_LEARNER_ACCESS_PERMISSION = 'requests.has_learner_or_admin_access'

SUBSIDY_ACCESS_POLICY_OPERATOR_ROLE = 'enterprise_access_subsidy_access_policy_operator'
SUBSIDY_ACCESS_POLICY_LEARNER_ROLE = 'enterprise_access_subsidy_access_policy_learner'
SUBSIDY_ACCESS_POLICY_READ_PERMISSION = 'subsidy_access_policy.has_read_access'
SUBSIDY_ACCESS_POLICY_WRITE_PERMISSION = 'subsidy_access_policy.has_write_access'
SUBSIDY_ACCESS_POLICY_REDEMPTION_PERMISSION = 'subsidy_access_policy.has_redemption_access'
SUBSIDY_ACCESS_POLICY_ALLOCATION_PERMISSION = 'subsidy_access_policy.has_allocation_access'

CONTENT_ASSIGNMENTS_OPERATOR_ROLE = 'enterprise_access_content_assignments_operator'
CONTENT_ASSIGNMENTS_ADMIN_ROLE = 'enterprise_access_content_assignments_admin'
CONTENT_ASSIGNMENTS_LEARNER_ROLE = 'enterprise_access_content_assignments_learner'
CONTENT_ASSIGNMENT_CONFIGURATION_READ_PERMISSION = 'content_assignment_configuration.has_read_access'
CONTENT_ASSIGNMENT_CONFIGURATION_WRITE_PERMISSION = 'content_assignment_configuration.has_write_access'
CONTENT_ASSIGNMENT_CONFIGURATION_ACKNOWLEDGE_PERMISSION = 'content_assignment_configuration.has_acknowledge_access'
CONTENT_ASSIGNMENT_ADMIN_READ_PERMISSION = 'content_assignment.has_admin_read_access'
CONTENT_ASSIGNMENT_ADMIN_WRITE_PERMISSION = 'content_assignment.has_admin_write_access'
CONTENT_ASSIGNMENT_LEARNER_READ_PERMISSION = 'content_assignment.has_learner_read_access'

BFF_LEARNER_ROLE = 'enterprise_access_bff_learner'
BFF_ADMIN_ROLE = 'enterprise_access_bff_admin'
BFF_OPERATOR_ROLE = 'enterprise_access_bff_operator'
BFF_READ_PERMISSION = 'bff.has_read_access'

PROVISIONING_ADMIN_ROLE = 'provisioning_admin'
PROVISIONING_CREATE_PERMISSION = 'provisioning.can_create'

ALL_ACCESS_CONTEXT = '*'


class Status:
    """Health statuses."""
    OK = "OK"
    UNAVAILABLE = "UNAVAILABLE"
