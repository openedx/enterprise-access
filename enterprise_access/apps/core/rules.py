"""
Rules needed to restrict access to the enterprise access service.
"""
import crum
import rules
from edx_rbac.utils import get_decoded_jwt, request_user_has_implicit_access_via_jwt, user_has_access_via_database

from enterprise_access.apps.core import constants
from enterprise_access.apps.core.models import EnterpriseAccessRoleAssignment


@rules.predicate
def has_implicit_access_to_requests_admin(_, enterprise_customer_uuid):
    """
    Check that if request user has implicit access to the given enterprise UUID for the
    `REQUESTS_ADMIN_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    if not enterprise_customer_uuid:
        return False

    return request_user_has_implicit_access_via_jwt(
        get_decoded_jwt(crum.get_current_request()),
        constants.REQUESTS_ADMIN_ROLE,
        str(enterprise_customer_uuid),
    )


@rules.predicate
def has_explicit_access_to_requests_admin(user, enterprise_customer_uuid):
    """
    Check that if request user has explicit access to `REQUESTS_ADMIN_ROLE` feature role.
    Returns:
        boolean: whether the request user has access.
    """
    if not enterprise_customer_uuid:
        return False

    return user_has_access_via_database(
        user,
        constants.REQUESTS_ADMIN_ROLE,
        EnterpriseAccessRoleAssignment,
        str(enterprise_customer_uuid),
    )


has_admin_access = has_implicit_access_to_requests_admin | has_explicit_access_to_requests_admin  # pylint: disable=unsupported-binary-operation

rules.add_perm(
    constants.REQUESTS_ADMIN_ACCESS_PERMISSION,
    has_admin_access,
)


@rules.predicate
def has_implicit_access_to_requests_learner(_, enterprise_customer_uuid):
    """
    Check that if request user has implicit access to the given enterprise UUID for the
    `REQUESTS_LEARNER_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    if not enterprise_customer_uuid:
        return False

    return request_user_has_implicit_access_via_jwt(
        get_decoded_jwt(crum.get_current_request()),
        constants.REQUESTS_LEARNER_ROLE,
        str(enterprise_customer_uuid),
    )


@rules.predicate
def has_explicit_access_to_requests_learner(user, enterprise_customer_uuid):
    """
    Check that if request user has explicit access to `REQUESTS_LEARNER_ROLE` feature role.

    Returns:
        boolean: whether the request user has access.
    """
    if not enterprise_customer_uuid:
        return False

    return user_has_access_via_database(
        user,
        constants.REQUESTS_LEARNER_ROLE,
        EnterpriseAccessRoleAssignment,
        str(enterprise_customer_uuid),
    )


has_learner_access = has_implicit_access_to_requests_learner | has_explicit_access_to_requests_learner  # pylint: disable=unsupported-binary-operation

# Grants access permission if the user is a learner or admin
rules.add_perm(
    constants.REQUESTS_ADMIN_LEARNER_ACCESS_PERMISSION,
    has_admin_access | has_learner_access,
)
