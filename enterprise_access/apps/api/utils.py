"""
Utility functions for Enterprise Access API.
"""

import logging
from uuid import UUID

from rest_framework.exceptions import ParseError

from enterprise_access.apps.bffs.api import (
    get_and_cache_enterprise_customer_users,
    transform_enterprise_customer_users_data
)
from enterprise_access.apps.content_assignments.api import get_assignment_configuration
from enterprise_access.apps.subsidy_access_policy.api import get_subsidy_access_policy

logger = logging.getLogger(__name__)


def get_enterprise_uuid_from_query_params(request):
    """
    Extracts enterprise_customer_uuid from query params.
    """

    enterprise_customer_uuid = request.query_params.get('enterprise_customer_uuid')

    if not enterprise_customer_uuid:
        return None

    try:
        return UUID(enterprise_customer_uuid)
    except ValueError as ex:
        raise ParseError('{} is not a valid uuid.'.format(enterprise_customer_uuid)) from ex


def get_enterprise_uuid_from_request_data(request):
    """
    Extracts enterprise_customer_uuid from the request payload.
    """

    enterprise_customer_uuid = request.data.get('enterprise_customer_uuid')

    if not enterprise_customer_uuid:
        return None

    try:
        return UUID(enterprise_customer_uuid)
    except ValueError as ex:
        raise ParseError('{} is not a valid uuid.'.format(enterprise_customer_uuid)) from ex


# Can use this to replace above logic in other utils functions,
# but not yet to avoid merge conflicts
def validate_uuid(uuid):
    """ Check if UUID is valid. If not, raise an error. """
    try:
        return UUID(uuid)
    except ValueError as ex:
        raise ParseError('{} is not a valid uuid.'.format(uuid)) from ex


def get_policy_customer_uuid(policy_uuid):
    """
    Given a policy uuid, returns the corresponding, string-ified
    customer uuid associated with that policy, if found.
    """
    policy = get_subsidy_access_policy(policy_uuid)
    if policy:
        return str(policy.enterprise_customer_uuid)
    return None


def get_assignment_config_customer_uuid(assignment_configuration_uuid):
    """
    Given an AssignmentConfiguration uuid, returns the corresponding, string-ified customer uuid associated with that
    policy, if found.
    """
    assignment_config = get_assignment_configuration(assignment_configuration_uuid)
    if assignment_config:
        return str(assignment_config.enterprise_customer_uuid)
    return None


def get_or_fetch_enterprise_uuid_for_bff_request(request):
    """
    Extracts enterprise_customer_uuid from query params or request data.
    """
    enterprise_customer_uuid = (
        get_enterprise_uuid_from_query_params(request) or
        get_enterprise_uuid_from_request_data(request)
    )
    if enterprise_customer_uuid:
        return enterprise_customer_uuid

    enterprise_customer_slug = (
        request.query_params.get('enterprise_customer_slug') or
        request.data.get('enterprise_customer_slug')
    )
    if enterprise_customer_slug:
        try:
            enterprise_customer_users_data = get_and_cache_enterprise_customer_users(
                request,
                traverse_pagination=True
            )
            transformed_data = transform_enterprise_customer_users_data(
                enterprise_customer_users_data,
                request=request,
                enterprise_customer_slug=enterprise_customer_slug,
                enterprise_customer_uuid=enterprise_customer_uuid,
            )
            enterprise_customer = transformed_data.get('enterprise_customer') or {}
            return enterprise_customer.get('uuid')
        except Exception:  # pylint: disable=broad-except
            logger.exception('Error retrieving linked enterprise customers')
            return None

    # Could not derive enterprise_customer_uuid for the BFF request.
    return None


def add_bulk_approve_operation_result(
    results_dict, category, uuid, state, detail
):
    """
    Add a standardized result entry to a bulk operation results dictionary.

    Args:
        results_dict (dict): Dictionary containing categorized results
        category (str): Result category (e.g., 'approved', 'failed', 'skipped', 'not_found')
        uuid (str): UUID of the request being processed
        state (str|None): Current state of the request, or None if not applicable
        detail (str): Descriptive message about the operation result
    """
    results_dict[category].append(
        {"uuid": str(uuid), "state": state, "detail": detail}
    )
