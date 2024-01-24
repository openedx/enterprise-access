"""
Utils for any app in the enterprise-access project.
"""
from datetime import datetime

from django.apps import apps
from pytz import UTC

from enterprise_access.apps.subsidy_request.constants import SubsidyTypeChoices

_MEMO_MISS = object()


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


def is_not_none(thing):
    return thing is not None


def is_none(thing):
    return thing is None


def localized_utcnow():
    """Helper function to return localized utcnow()."""
    return datetime.now().replace(tzinfo=UTC)
