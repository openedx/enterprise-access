"""
Celery tasks for Enterprise Access API.
"""
import logging

from celery import shared_task
from celery_utils.logged_task import LoggedTask
from django.conf import settings

from enterprise_access.apps.api_client.braze_client import BrazeApiClient
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.subsidy_request.constants import (
    ENTERPRISE_BRAZE_ALIAS_LABEL,
    SUBSIDY_TYPE_CHANGE_DECLINATION,
    SubsidyRequestStates,
    SubsidyTypeChoices
)
from enterprise_access.apps.subsidy_request.models import CouponCodeRequest, LicenseRequest

logger = logging.getLogger(__name__)


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
def decline_enterprise_subsidy_requests_task(enterprise_customer_uuid, subsidy_type, send_notification):
    """
    Decline all subsidy requests of the given type for the enterprise customer.
    """

    if subsidy_type == SubsidyTypeChoices.COUPON:
        subsidy_requests = CouponCodeRequest.objects.filter(
            enterprise_customer_uuid=enterprise_customer_uuid,
            state__in=[
                SubsidyRequestStates.REQUESTED,
                SubsidyRequestStates.PENDING,
                SubsidyRequestStates.ERROR
            ],
        )
    if subsidy_type == SubsidyTypeChoices.LICENSE:
        subsidy_requests = LicenseRequest.objects.filter(
            enterprise_customer_uuid=enterprise_customer_uuid,
            state__in=[
                SubsidyRequestStates.REQUESTED,
                SubsidyRequestStates.PENDING,
                SubsidyRequestStates.ERROR
            ],
        )

    # Why I don't used subsidy_requests.update() #
    # When you run .update() on a queryset, you "lose" the objects, because by
    # nature of them being updated in the DB (update runs raw SQL),
    # they no longer are returned by the original
    # queryset. To make sure we send out notifications for the exact objects we are
    # declining here, I've opted to use a save() in a for-loop (which the django
    # docs even recommend in some cases).
    for subsidy_request in subsidy_requests:
        logger.info(f'Declining subsidy {subsidy_request} because subsidy type changed on Configuration.')
        subsidy_request.state = SubsidyRequestStates.DECLINED
        subsidy_request.decline_reason = SUBSIDY_TYPE_CHANGE_DECLINATION
        subsidy_request.save()

    if send_notification:

        braze_client_instance = BrazeApiClient()
        braze_campaign_id = settings.BRAZE_DECLINE_NOTIFICATION_CAMPAIGN
        lms_client = LmsApiClient()

        for subsidy_request in subsidy_requests:
            logger.info(f'Looking up user email in the LMS to send notifcation for subsidy request {subsidy_request}')
            enterprise_customer_user_data = lms_client.get_enterprise_learner_data(subsidy_request.lms_user_id)
            user_email = enterprise_customer_user_data['user']['email']
            recipient = _aliased_recipient_object_from_email(user_email)
            # Todo: add things to this dictionary once the campaign template exists
            braze_trigger_properties = {}

            logger.info(f'Sending braze campaign message for subsidy request {subsidy_request}')
            braze_client_instance.send_campaign_message(
                braze_campaign_id,
                recipients=[recipient],
                trigger_properties=braze_trigger_properties,
            )
