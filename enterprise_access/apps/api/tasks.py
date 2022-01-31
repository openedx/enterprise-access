"""
Celery tasks for Enterprise Access API.
"""

from celery import shared_task
from celery_utils.logged_task import LoggedTask

from enterprise_access.apps.subsidy_request.constants import SubsidyTypeChoices
from enterprise_access.apps.subsidy_request.models import CouponCodeRequest, LicenseRequest


@shared_task(base=LoggedTask)
def delete_enterprise_subsidy_requests_task(enterprise_customer_uuid, subsidy_type):
    """
    Delete all subsidy requests of the given type for the enterprise customer.
    """

    if subsidy_type == SubsidyTypeChoices.COUPON:
        CouponCodeRequest.objects.filter(
            enterprise_customer_uuid=enterprise_customer_uuid
        ).delete()

    if subsidy_type == SubsidyTypeChoices.LICENSE:
        LicenseRequest.objects.filter(
            enterprise_customer_uuid=enterprise_customer_uuid
        ).delete()
