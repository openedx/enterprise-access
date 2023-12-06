"""
API Filters for resources defined in the ``subsidy_access_policy`` app.
"""
from django_filters import rest_framework as drf_filters

from ...subsidy_access_policy.models import SubsidyAccessPolicy
from .base import HelpfulFilterSet


class SubsidyAccessPolicyFilter(HelpfulFilterSet):
    """
    Base filter for SubsidyAccessPolicy views.
    """
    enterprise_customer_uuid = drf_filters.UUIDFilter(
        required=True,
        help_text=SubsidyAccessPolicy._meta.get_field('enterprise_customer_uuid').help_text,
    )
    active = drf_filters.BooleanFilter(
        required=False,
        help_text=SubsidyAccessPolicy._meta.get_field('active').help_text,
    )

    class Meta:
        model = SubsidyAccessPolicy
        fields = ['policy_type']
