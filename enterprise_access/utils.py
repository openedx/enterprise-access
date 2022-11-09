"""
Utils for any app in the enterprise-access project.
"""

from django.apps import apps
from django.db import models
from uuid import uuid4
from model_utils.models import TimeStampedModel

from enterprise_access.apps.subsidy_request.constants import SubsidyTypeChoices


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


class TimeStampedModelWithUuid(TimeStampedModel):
    class Meta:
        abstract = True

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
        unique=True,
    )
