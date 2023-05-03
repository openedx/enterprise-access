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


# pylint: disable=unsupported-binary-operation
has_subsidy_request_admin_access = (
    has_implicit_access_to_requests_admin | has_explicit_access_to_requests_admin
)

rules.add_perm(
    constants.REQUESTS_ADMIN_ACCESS_PERMISSION,
    has_subsidy_request_admin_access,
)


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


# pylint: disable=unsupported-binary-operation
has_subsidy_request_learner_access = (
    has_implicit_access_to_requests_learner | has_explicit_access_to_requests_learner
)

# Grants access permission if the user is a learner or admin
rules.add_perm(
    constants.REQUESTS_ADMIN_LEARNER_ACCESS_PERMISSION,
    has_subsidy_request_admin_access | has_subsidy_request_learner_access,
)


# Subsidy Access Policy rules and permissions
@rules.predicate
def has_implicit_access_to_policy_admin(_, enterprise_customer_uuid):
    """
    Check that if request user has implicit access to the given enterprise UUID for the
    `POLICY_ADMIN_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_implicit_access_to_role(_, enterprise_customer_uuid, constants.POLICY_ADMIN_ROLE)


@rules.predicate
def has_explicit_access_to_policy_admin(user, enterprise_customer_uuid):
    """
    Check that if request user has explicit access to `POLICY_ADMIN_ROLE` feature role.
    Returns:
        boolean: whether the request user has access.
    """
    return _has_explicit_access_to_role(user, enterprise_customer_uuid, constants.POLICY_ADMIN_ROLE)


# pylint: disable=unsupported-binary-operation
has_subsidy_access_policy_admin_access = (
    has_implicit_access_to_policy_admin | has_explicit_access_to_policy_admin
)


@rules.predicate
def has_implicit_access_to_policy_learner(_, enterprise_customer_uuid):
    """
    Check that if request user has implicit access to the given enterprise UUID for the
    `POLICY_LEARNER_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    return _has_implicit_access_to_role(_, enterprise_customer_uuid, constants.POLICY_LEARNER_ROLE)


@rules.predicate
def has_explicit_access_to_policy_learner(user, enterprise_customer_uuid):
    """
    Check that if request user has explicit access to `POLICY_LEARNER_ROLE` feature role.
    Returns:
        boolean: whether the request user has access.
    """
    return _has_explicit_access_to_role(user, enterprise_customer_uuid, constants.POLICY_LEARNER_ROLE)


# pylint: disable=unsupported-binary-operation
has_subsidy_access_policy_learner_access = (
    has_implicit_access_to_policy_learner | has_explicit_access_to_policy_learner
)

# Grants policy read permission if the user is a policy learner or admin
rules.add_perm(
    constants.POLICY_READ_PERMISSION,
    has_subsidy_access_policy_admin_access | has_subsidy_access_policy_learner_access
)
