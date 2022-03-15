"""
Utils for any app in the enterprise-access project.
"""

from django.apps import apps

from enterprise_access.apps.subsidy_request.constants import ENTERPRISE_BRAZE_ALIAS_LABEL, SubsidyTypeChoices


def get_aliased_recipient_object_from_email(user_email):
    """
    Returns a dictionary with a braze recipient object, including
    a braze alias object.

    Args:
        user_email (string): email of user

    Returns:
        a dictionary with a braze recipient object, including a braze alias object.
    """
    return {
        'attributes': {'email': user_email},
        'user_alias': {
            'alias_label': ENTERPRISE_BRAZE_ALIAS_LABEL,
            'alias_name': user_email,
        },
    }


def get_subsidy_model(subsidy_type):
    """
    Get subsidy model from subsidy_type string

    Args:
        subsidy_type (string): string name of subsidy
    Returns:
        Class of a model object
    """
    subsidy_model = None
    if subsidy_type == SubsidyTypeChoices.COUPON:
        subsidy_model = apps.get_model('subsidy_request.CouponCodeRequest')
    if subsidy_type == SubsidyTypeChoices.LICENSE:
        subsidy_model = apps.get_model('subsidy_request.LicenseRequest')
    return subsidy_model
