"""
Celery tasks for Enterprise Access API.
"""

from celery import shared_task
from celery_utils.logged_task import LoggedTask

from enterprise_access.apps.subsidy_request.constants import (
    SubsidyRequestStates,
    SUBSIDY_TYPE_CHANGE_DECLINATION,
    SubsidyTypeChoices,
)
from enterprise_access.apps.subsidy_request.models import CouponCodeRequest, LicenseRequest


@shared_task(base=LoggedTask)
def decline_enterprise_subsidy_requests_task(enterprise_customer_uuid, subsidy_type):
    """
    Decline all subsidy requests of the given type for the enterprise customer.
    """

    if subsidy_type == SubsidyTypeChoices.COUPON:
        subsidy_requests = CouponCodeRequest.objects.filter(
            enterprise_customer_uuid=enterprise_customer_uuid,
            status__in=[
                SubsidyRequestStates.REQUESTED,
                SubsidyRequestStates.PENDING,
                SubsidyRequestStates.ERROR
            ],
        )
        subsidy_requests.state = SubsidyRequestStates.DECLINED
        subsidy_requests.decline_reason = SUBSIDY_TYPE_CHANGE_DECLINATION
        subsidy_requests.save()

    elif subsidy_type == SubsidyTypeChoices.LICENSE:
        subsidy_requests = LicenseRequest.objects.filter(
            enterprise_customer_uuid=enterprise_customer_uuid,
            status__in=[
                SubsidyRequestStates.REQUESTED,
                SubsidyRequestStates.PENDING,
                SubsidyRequestStates.ERROR
            ],
        )
        subsidy_requests.state = SubsidyRequestStates.DECLINED
        subsidy_requests.decline_reason = SUBSIDY_TYPE_CHANGE_DECLINATION
        subsidy_requests.save()

    return subsidy_requests


@shared_task(base=LoggedTask)
def send_decline_notifications_task(subsidy_requests):
    """
    Send email notifications for each subsidy_requests
    """
    pass