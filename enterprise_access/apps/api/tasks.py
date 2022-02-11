"""
Celery tasks for Enterprise Access API.
"""

from braze.exceptions import BrazeClientError
from celery import shared_task
from celery_utils.logged_task import LoggedTask

from django.conf import settings

from enterprise_access.apps.api_client.braze_client import BrazeApiClient
from enterprise_access.apps.subsidy_request.constants import (
    SubsidyRequestStates,
    SUBSIDY_TYPE_CHANGE_DECLINATION,
    SubsidyTypeChoices,
)
from enterprise_access.apps.subsidy_request.models import CouponCodeRequest, LicenseRequest


def _aliased_recipient_object_from_email(user_email):
    """
    Returns a dictionary with a braze recipient object, including
    a braze alias object.
    """
    return {
        'attributes': {'email': user_email},
        'user_alias': {
            'alias_label': ENTERPRISE_BRAZE_ALIAS_LABEL,
            'alias_name': user_email,
        },
    }


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
    braze_client_instance = BrazeApiClient()
    braze_campaign_id = settings.BRAZE_DECLINE_NOTIFICATION_CAMPAIGN

    for subsidy_request in subsidy_requests:
        user_email = subsidy_request.user_email
        recipient = _aliased_recipient_object_from_email(user_email)
        # Todo: add things to this dictionary once the campaign template exists
        braze_trigger_properties = {}

        braze_client_instance.send_campaign_message(
            braze_campaign_id,
            recipients=[recipient],
            trigger_properties=braze_trigger_properties,
        )
