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
def has_implicit_access_to_content_assignment_operator(_, enterprise_customer_uuid):
    """
    Check that if request user has implicit access to the given enterprise UUID for the
    `CONTENT_ASSIGNMENTS_OPERATOR_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_implicit_access_to_role(_, enterprise_customer_uuid, constants.CONTENT_ASSIGNMENTS_OPERATOR_ROLE)


@rules.predicate
def has_explicit_access_to_content_assignment_operator(user, enterprise_customer_uuid):
    """
    Check that if request user has explicit access to `CONTENT_ASSIGNMENTS_OPERATOR_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_explicit_access_to_role(user, enterprise_customer_uuid, constants.CONTENT_ASSIGNMENTS_OPERATOR_ROLE)


@rules.predicate
def has_implicit_access_to_content_assignment_admin(_, enterprise_customer_uuid):
    """
    Check that if request user has implicit access to the given enterprise UUID for the
    `CONTENT_ASSIGNMENTS_ADMIN_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_implicit_access_to_role(_, enterprise_customer_uuid, constants.CONTENT_ASSIGNMENTS_ADMIN_ROLE)


@rules.predicate
def has_explicit_access_to_content_assignment_admin(user, enterprise_customer_uuid):
    """
    Check that if request user has explicit access to `CONTENT_ASSIGNMENTS_ADMIN_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_explicit_access_to_role(user, enterprise_customer_uuid, constants.CONTENT_ASSIGNMENTS_ADMIN_ROLE)


######################################################
# Consolidate implicit and explicit rule predicates. #
######################################################

# pylint: disable=unsupported-binary-operation
has_subsidy_request_admin_access = (
    has_implicit_access_to_requests_admin | has_explicit_access_to_requests_admin
)


# pylint: disable=unsupported-binary-operation
has_subsidy_request_learner_access = (
    has_implicit_access_to_requests_learner | has_explicit_access_to_requests_learner
)


# pylint: disable=unsupported-binary-operation
has_subsidy_access_policy_operator_access = (
    has_implicit_access_to_policy_operator | has_explicit_access_to_policy_operator
)


# pylint: disable=unsupported-binary-operation
has_subsidy_access_policy_learner_access = (
    has_implicit_access_to_policy_learner | has_explicit_access_to_policy_learner
)


# pylint: disable=unsupported-binary-operation
has_content_assignment_operator_access = (
    has_implicit_access_to_content_assignment_operator | has_explicit_access_to_content_assignment_operator
)


# pylint: disable=unsupported-binary-operation
has_content_assignment_admin_access = (
    has_implicit_access_to_content_assignment_admin | has_explicit_access_to_content_assignment_admin
)


rules.add_perm(
    constants.REQUESTS_ADMIN_ACCESS_PERMISSION,
    has_subsidy_request_admin_access,
)

###############################################
# Map permissions to consolidated predicates. #
###############################################

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


# Grants content assignment configuration read permission if the user is a content assignment configuration admin.
rules.add_perm(
    constants.CONTENT_ASSIGNMENTS_CONFIGURATION_READ_PERMISSION,
    has_content_assignment_operator_access | has_content_assignment_admin_access,
)


# Grants content assignment configuration write permission if the user is a content assignment configuration operator.
rules.add_perm(
    constants.CONTENT_ASSIGNMENTS_CONFIGURATION_WRITE_PERMISSION,
    has_content_assignment_operator_access,
)


# Grants permission to allocate assignments from a policy if the user is a content assignment configuration admin.
rules.add_perm(
    constants.SUBSIDY_ACCESS_POLICY_ALLOCATION_PERMISSION,
    (
        has_content_assignment_operator_access |
        has_content_assignment_admin_access |
        has_subsidy_access_policy_operator_access
    ),
)
