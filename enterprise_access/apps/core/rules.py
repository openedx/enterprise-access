"""
Rules needed to restrict access to the enterprise access service.
"""
import crum
import rules
from edx_rbac.utils import get_decoded_jwt, request_user_has_implicit_access_via_jwt, user_has_access_via_database

from enterprise_access.apps.core import constants
from enterprise_access.apps.core.models import EnterpriseAccessRoleAssignment


def _has_implicit_access_to_role(_, enterprise_customer_uuid, feature_role):
    """
    Helper to check if the request user has implicit access (via their JWT)
    to the given enterprise UUID for the specified role

    Returns:
        boolean: whether the request user has access to the given role for the given customer.
    """
    if not enterprise_customer_uuid:
        return False

    return request_user_has_implicit_access_via_jwt(
        get_decoded_jwt(crum.get_current_request()),
        feature_role,
        str(enterprise_customer_uuid),
    )


def _has_explicit_access_to_role(user, enterprise_customer_uuid, feature_role):
    """
    Helper to check if the request user has explicit access (via a database record)
    to the given role and enterprise customer uuid.
    Returns:
        boolean: whether the request user has DB-defined access.
    """
    if not enterprise_customer_uuid:
        return False

    return user_has_access_via_database(
        user,
        feature_role,
        EnterpriseAccessRoleAssignment,
        str(enterprise_customer_uuid),
    )


########################
# All rule predicates. #
########################

@rules.predicate
def has_implicit_access_to_requests_admin(_, enterprise_customer_uuid):
    """
    Check that if request user has implicit access to the given enterprise UUID for the
    `REQUESTS_ADMIN_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_implicit_access_to_role(_, enterprise_customer_uuid, constants.REQUESTS_ADMIN_ROLE)


@rules.predicate
def has_explicit_access_to_requests_admin(user, enterprise_customer_uuid):
    """
    Check that if request user has explicit access to `REQUESTS_ADMIN_ROLE` feature role.
    Returns:
        boolean: whether the request user has access.
    """
    return _has_explicit_access_to_role(user, enterprise_customer_uuid, constants.REQUESTS_ADMIN_ROLE)


@rules.predicate
def has_implicit_access_to_requests_learner(_, enterprise_customer_uuid):
    """
    Check that if request user has implicit access to the given enterprise UUID for the
    `REQUESTS_LEARNER_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_implicit_access_to_role(_, enterprise_customer_uuid, constants.REQUESTS_LEARNER_ROLE)


@rules.predicate
def has_explicit_access_to_requests_learner(user, enterprise_customer_uuid):
    """
    Check that if request user has explicit access to `REQUESTS_LEARNER_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_explicit_access_to_role(user, enterprise_customer_uuid, constants.REQUESTS_LEARNER_ROLE)


# Subsidy Access Policy rule predicates:
@rules.predicate
def has_implicit_access_to_policy_operator(_, enterprise_customer_uuid):
    """
    Check that if request user has implicit access to the given enterprise UUID for the
    `SUBSIDY_ACCESS_POLICY_OPERATOR_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_implicit_access_to_role(_, enterprise_customer_uuid, constants.SUBSIDY_ACCESS_POLICY_OPERATOR_ROLE)


@rules.predicate
def has_explicit_access_to_policy_operator(user, enterprise_customer_uuid):
    """
    Check that if request user has explicit access to `SUBSIDY_ACCESS_POLICY_OPERATOR_ROLE` feature role.
    Returns:
        boolean: whether the request user has access.
    """
    return _has_explicit_access_to_role(user, enterprise_customer_uuid, constants.SUBSIDY_ACCESS_POLICY_OPERATOR_ROLE)


@rules.predicate
def has_implicit_access_to_policy_learner(_, enterprise_customer_uuid):
    """
    Check that if request user has implicit access to the given enterprise UUID for the
    `SUBSIDY_ACCESS_POLICY_LEARNER_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_implicit_access_to_role(_, enterprise_customer_uuid, constants.SUBSIDY_ACCESS_POLICY_LEARNER_ROLE)


@rules.predicate
def has_explicit_access_to_policy_learner(user, enterprise_customer_uuid):
    """
    Check that if request user has explicit access to `SUBSIDY_ACCESS_POLICY_LEARNER_ROLE` feature role.
    Returns:
        boolean: whether the request user has access.
    """
    return _has_explicit_access_to_role(user, enterprise_customer_uuid, constants.SUBSIDY_ACCESS_POLICY_LEARNER_ROLE)


# Content Assignment rule predicates:
@rules.predicate
def has_implicit_access_to_content_assignments_operator(_, enterprise_customer_uuid):
    """
    Check that if request user has implicit access to the given enterprise UUID for the
    `CONTENT_ASSIGNMENTS_OPERATOR_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_implicit_access_to_role(_, enterprise_customer_uuid, constants.CONTENT_ASSIGNMENTS_OPERATOR_ROLE)


@rules.predicate
def has_explicit_access_to_content_assignments_operator(user, enterprise_customer_uuid):
    """
    Check that if request user has explicit access to `CONTENT_ASSIGNMENTS_OPERATOR_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_explicit_access_to_role(user, enterprise_customer_uuid, constants.CONTENT_ASSIGNMENTS_OPERATOR_ROLE)


@rules.predicate
def has_implicit_access_to_content_assignments_admin(_, enterprise_customer_uuid):
    """
    Check that if request user has implicit access to the given enterprise UUID for the
    `CONTENT_ASSIGNMENTS_ADMIN_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_implicit_access_to_role(_, enterprise_customer_uuid, constants.CONTENT_ASSIGNMENTS_ADMIN_ROLE)


@rules.predicate
def has_explicit_access_to_content_assignments_admin(user, enterprise_customer_uuid):
    """
    Check that if request user has explicit access to `CONTENT_ASSIGNMENTS_ADMIN_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_explicit_access_to_role(user, enterprise_customer_uuid, constants.CONTENT_ASSIGNMENTS_ADMIN_ROLE)


@rules.predicate
def has_implicit_access_to_content_assignments_learner(_, enterprise_customer_uuid):
    """
    Check that if request user has implicit access to the given enterprise UUID for the
    `CONTENT_ASSIGNMENTS_LEARNER_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_implicit_access_to_role(_, enterprise_customer_uuid, constants.CONTENT_ASSIGNMENTS_LEARNER_ROLE)


@rules.predicate
def has_explicit_access_to_content_assignments_learner(user, enterprise_customer_uuid):
    """
    Check that if request user has explicit access to `CONTENT_ASSIGNMENTS_LEARNER_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_explicit_access_to_role(user, enterprise_customer_uuid, constants.CONTENT_ASSIGNMENTS_LEARNER_ROLE)


@rules.predicate
def has_implicit_access_to_bff_learner(_, enterprise_customer_uuid):
    """
    Check that if request user has implicit access to the given enterprise UUID for the
    `BFF_LEARNER_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_implicit_access_to_role(_, enterprise_customer_uuid, constants.BFF_LEARNER_ROLE)


@rules.predicate
def has_explicit_access_to_bff_learner(user, enterprise_customer_uuid):
    """
    Check that if request user has explicit access to `BFF_LEARNER_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_explicit_access_to_role(user, enterprise_customer_uuid, constants.BFF_LEARNER_ROLE)


@rules.predicate
def has_implicit_access_to_bff_admin(_, enterprise_customer_uuid):
    """
    Check that if request user has implicit access to the given enterprise UUID for the
    `BFF_ADMIN_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_implicit_access_to_role(_, enterprise_customer_uuid, constants.BFF_ADMIN_ROLE)


@rules.predicate
def has_explicit_access_to_bff_admin(user, enterprise_customer_uuid):
    """
    Check that if request user has explicit access to `BFF_ADMIN_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_explicit_access_to_role(user, enterprise_customer_uuid, constants.BFF_ADMIN_ROLE)


@rules.predicate
def has_implicit_access_to_bff_operator(_, enterprise_customer_uuid):
    """
    Check that if request user has implicit access to the given enterprise UUID for the
    `BFF_OPERATOR_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_implicit_access_to_role(_, enterprise_customer_uuid, constants.BFF_OPERATOR_ROLE)


@rules.predicate
def has_explicit_access_to_bff_operator(user, enterprise_customer_uuid):
    """
    Check that if request user has explicit access to `BFF_OPERATOR_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_explicit_access_to_role(user, enterprise_customer_uuid, constants.BFF_OPERATOR_ROLE)


@rules.predicate
def has_implicit_access_to_provisioning_admin(_, *args, **kwargs):
    """
    Check if request user has implicit access to the provisioning admin role.
    Note, there is no enterprise customer context against which access to this
    role is checked.

    Returns:
        boolean: whether the request user has access.
    """
    return request_user_has_implicit_access_via_jwt(
        get_decoded_jwt(crum.get_current_request()),
        constants.PROVISIONING_ADMIN_ROLE,
        context=None,
    )


# Customer Billing rule predicates:
@rules.predicate
def has_implicit_access_to_customer_billing_operator(_, enterprise_customer_uuid):
    """
    Check that if request user has implicit access to the given enterprise UUID for the
    `CUSTOMER_BILLING_OPERATOR_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_implicit_access_to_role(_, enterprise_customer_uuid, constants.CUSTOMER_BILLING_OPERATOR_ROLE)


@rules.predicate
def has_explicit_access_to_customer_billing_operator(user, enterprise_customer_uuid):
    """
    Check that if request user has explicit access to `CUSTOMER_BILLING_OPERATOR_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_explicit_access_to_role(user, enterprise_customer_uuid, constants.CUSTOMER_BILLING_OPERATOR_ROLE)


@rules.predicate
def has_implicit_access_to_customer_billing_admin(_, enterprise_customer_uuid):
    """
    Check that if request user has implicit access to the given enterprise UUID for the
    `CUSTOMER_BILLING_ADMIN_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_implicit_access_to_role(_, enterprise_customer_uuid, constants.CUSTOMER_BILLING_ADMIN_ROLE)


@rules.predicate
def has_explicit_access_to_customer_billing_admin(user, enterprise_customer_uuid):
    """
    Check that if request user has explicit access to `CUSTOMER_BILLING_ADMIN_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_explicit_access_to_role(user, enterprise_customer_uuid, constants.CUSTOMER_BILLING_ADMIN_ROLE)


######################################################
# Consolidate implicit and explicit rule predicates. #
######################################################


has_subsidy_request_admin_access = (
    has_implicit_access_to_requests_admin | has_explicit_access_to_requests_admin
)


has_subsidy_request_learner_access = (
    has_implicit_access_to_requests_learner | has_explicit_access_to_requests_learner
)


has_subsidy_access_policy_operator_access = (
    has_implicit_access_to_policy_operator | has_explicit_access_to_policy_operator
)


has_subsidy_access_policy_learner_access = (
    has_implicit_access_to_policy_learner | has_explicit_access_to_policy_learner
)


has_content_assignments_operator_access = (
    has_implicit_access_to_content_assignments_operator | has_explicit_access_to_content_assignments_operator
)


has_content_assignments_admin_access = (
    has_implicit_access_to_content_assignments_admin | has_explicit_access_to_content_assignments_admin
)


has_content_assignments_learner_access = (
    has_implicit_access_to_content_assignments_learner | has_explicit_access_to_content_assignments_learner
)

has_bff_learner_access = (
    has_implicit_access_to_bff_learner | has_explicit_access_to_bff_learner
)

has_bff_admin_access = (
    has_implicit_access_to_bff_admin | has_explicit_access_to_bff_admin
)

has_bff_operator_access = (
    has_implicit_access_to_bff_operator | has_explicit_access_to_bff_operator
)


has_customer_billing_operator_access = (
    has_implicit_access_to_customer_billing_operator | has_explicit_access_to_customer_billing_operator
)


has_customer_billing_admin_access = (
    has_implicit_access_to_customer_billing_admin | has_explicit_access_to_customer_billing_admin
)


###############################################
# Map permissions to consolidated predicates. #
###############################################

rules.add_perm(
    constants.REQUESTS_ADMIN_ACCESS_PERMISSION,
    has_subsidy_request_admin_access,
)

# Grants access permission if the user is a learner or admin
rules.add_perm(
    constants.REQUESTS_ADMIN_LEARNER_ACCESS_PERMISSION,
    has_subsidy_request_admin_access | has_subsidy_request_learner_access,
)


# Grants policy read permission if the user is a policy learner or admin
rules.add_perm(
    constants.SUBSIDY_ACCESS_POLICY_READ_PERMISSION,
    has_subsidy_access_policy_operator_access | has_subsidy_access_policy_learner_access
)


# Grants policy write permission if the user is a policy operator.
rules.add_perm(
    constants.SUBSIDY_ACCESS_POLICY_WRITE_PERMISSION,
    has_subsidy_access_policy_operator_access
)


# Grants policy redemption permission if the user is a policy learner or admin
rules.add_perm(
    constants.SUBSIDY_ACCESS_POLICY_REDEMPTION_PERMISSION,
    has_subsidy_access_policy_operator_access | has_subsidy_access_policy_learner_access
)


# Grants content assignment configuration read permission to content assignment admins+operators.
rules.add_perm(
    constants.CONTENT_ASSIGNMENT_CONFIGURATION_READ_PERMISSION,
    has_content_assignments_operator_access | has_content_assignments_admin_access,
)


# Grants content assignment configuration write permission to content assignment operators.
rules.add_perm(
    constants.CONTENT_ASSIGNMENT_CONFIGURATION_WRITE_PERMISSION,
    has_content_assignments_operator_access,
)


# Grants content assignment admin read permission to content assignment admins+operators.
rules.add_perm(
    constants.CONTENT_ASSIGNMENT_ADMIN_READ_PERMISSION,
    has_content_assignments_operator_access | has_content_assignments_admin_access,
)


# Grants content assignment admin write permission to enterprise admins+operators.
rules.add_perm(
    constants.CONTENT_ASSIGNMENT_ADMIN_WRITE_PERMISSION,
    has_content_assignments_operator_access | has_content_assignments_admin_access,
)


# Grants content assignment learner read permission to practically everybody.
rules.add_perm(
    constants.CONTENT_ASSIGNMENT_LEARNER_READ_PERMISSION,
    (
        has_content_assignments_operator_access |
        has_content_assignments_admin_access |
        has_content_assignments_learner_access
    ),
)


# Grants permission to allocate assignments from a policy if the user is a content assignment configuration admin.
rules.add_perm(
    constants.SUBSIDY_ACCESS_POLICY_ALLOCATION_PERMISSION,
    (
        has_content_assignments_operator_access |
        has_content_assignments_admin_access |
        has_subsidy_access_policy_operator_access
    ),
)

# Grants permission to acknowledge assignments if the user is linked to the enterprise customer
# associated with the content assignment configuration.
rules.add_perm(
    constants.CONTENT_ASSIGNMENT_CONFIGURATION_ACKNOWLEDGE_PERMISSION,
    (
        has_content_assignments_operator_access |
        has_content_assignments_admin_access |
        has_content_assignments_learner_access
    ),
)

rules.add_perm(
    constants.BFF_READ_PERMISSION,
    (
        has_bff_learner_access |
        has_bff_admin_access |
        has_bff_operator_access
    ),
)

rules.add_perm(
    constants.PROVISIONING_CREATE_PERMISSION,
    has_implicit_access_to_provisioning_admin,
)

# Grants billing plan creation permissions to operators only.
rules.add_perm(
    constants.CUSTOMER_BILLING_CREATE_PLAN_PERMISSION,
    has_customer_billing_operator_access,
)

# Grants billing plan "create portal session" permissions to operators+admins.
rules.add_perm(
    constants.CUSTOMER_BILLING_CREATE_PORTAL_SESSION_PERMISSION,
    has_customer_billing_operator_access | has_customer_billing_admin_access,
)
