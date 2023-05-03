"""
Utility functions for Enterprise Access API.
"""

from uuid import UUID

from rest_framework.exceptions import ParseError

from enterprise_access.apps.subsidy_access_policy.api import get_subsidy_access_policy


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
