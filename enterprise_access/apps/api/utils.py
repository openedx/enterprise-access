"""
Utility functions for Enterprise Access API.
"""

from uuid import UUID

from rest_framework.exceptions import ParseError

from enterprise_access.apps.subsidy_request.constants import SubsidyTypeChoices
from enterprise_access.apps.subsidy_request.models import CouponCodeRequest, LicenseRequest

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


def get_subsidy_model(subsidy_type):
    """
    Get subsidy model from subsidy_type string

    Args:
        subsidy_type (string): string name of subsidy
    """
    subsidy_model = None
    if subsidy_type == SubsidyTypeChoices.COUPON:
        subsidy_model = CouponCodeRequest
    if subsidy_type == SubsidyTypeChoices.LICENSE:
        subsidy_model = LicenseRequest
    return subsidy_model


# Can use this to replace above logic in other utils functions,
# but not yet to avoid merge conflicts
def validate_uuid(uuid):
    """ Check if UUID is valid. If not, raise an error. """
    try:
        return UUID(uuid)
    except ValueError as ex:
        raise ParseError('{} is not a valid uuid.'.format(uuid)) from ex
