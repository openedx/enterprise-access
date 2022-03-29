"""
Tasks for subsidy requests app.
"""

import logging
from datetime import datetime

from celery import shared_task
from django.apps import apps
from django.conf import settings
from requests.exceptions import HTTPError

from enterprise_access.apps.api_client.braze_client import BrazeApiClient
from enterprise_access.apps.api_client.discovery_client import DiscoveryApiClient
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates
from enterprise_access.tasks import LoggedTaskWithRetry
from enterprise_access.utils import get_subsidy_model

logger = logging.getLogger(__name__)


@shared_task(base=LoggedTaskWithRetry)
def update_course_title_for_subsidy_request_task(subsidy_type, subsidy_request_uuid):
    """
    Get course_title from lms and update subsidy_request with it
    """
    subsidy_model = get_subsidy_model(subsidy_type)
    subsidy_request = subsidy_model.objects.get(uuid=subsidy_request_uuid)

    discovery_client = DiscoveryApiClient()
    course_data = discovery_client.get_course_data(subsidy_request.course_id)
    subsidy_request.course_title = course_data['title']

    # Use bulk_update so we don't trigger save() again
    subsidy_model.bulk_update([subsidy_request], ['course_title'])


def _get_manage_requests_url(subsidy_model, enterprise_slug):
    """
    Get a manage_requests url based on the type of subsidy.

    Args:
        subsidy_model (class):  class of the subsidy object
        enterprise_slug (string): slug of the enterprise's name
    Returns:
        string: a url to the manage learners page.
    """
    if subsidy_model == apps.get_model('subsidy_request.LicenseRequest'):
        subsidy_string = 'subscriptions'
    else:
        subsidy_string = 'coupons'

    url = f'{settings.ENTERPRISE_ADMIN_PORTAL_URL}/{enterprise_slug}/admin/{subsidy_string}/manage-requests'
    return url


@shared_task(base=LoggedTaskWithRetry)
def send_admins_email_with_new_requests_task(enterprise_customer_uuid):
    """
    Task to send new-request emails to admins.

    Args:
        enterprise_customer_uuid (str): enterprise customer uuid identifier
    Raises:
        HTTPError if Braze client callfails with an HTTPError
    """
    lms_client = LmsApiClient()
    enterprise_customer_data = lms_client.get_enterprise_customer_data(enterprise_customer_uuid)

    config_model = apps.get_model('subsidy_request.SubsidyRequestCustomerConfiguration')
    customer_config = config_model.objects.get(
        enterprise_customer_uuid=enterprise_customer_uuid,
    )

    subsidy_model = get_subsidy_model(customer_config.subsidy_type)
    subsidy_requests = subsidy_model.objects.filter(
        enterprise_customer_uuid=enterprise_customer_uuid,
        state=SubsidyRequestStates.REQUESTED,
    )
    # Filter when we last run this unless we never ran before
    # "future" is greater than "past"
    # so if created is greater than last remind date, it means
    # it was created after cron was last run
    if customer_config.last_remind_date is not None:
        subsidy_requests = subsidy_requests.filter(created__gte=customer_config.last_remind_date)

    subsidy_requests = subsidy_requests.order_by("-created")

    if not subsidy_requests:
        logger.info(
            'No new subsidy requests. Not sending new requests '
            f'email to admins for enterprise {enterprise_customer_uuid}.'
            )
        return

    braze_trigger_properties = {}
    enterprise_slug = enterprise_customer_data.get('slug')
    braze_trigger_properties['manage_requests_url'] = _get_manage_requests_url(subsidy_model, enterprise_slug)

    braze_trigger_properties['requests'] = []
    for subsidy_request in subsidy_requests:

        user_email = subsidy_request.user.email
        course_title = subsidy_request.course_title

        braze_trigger_properties['requests'].append({
            'user_email': user_email,
            'course_title': course_title,
        })

    admin_users = lms_client.get_enterprise_admin_users(enterprise_customer_uuid)

    logger.info(
        f'Sending new-requests email to admins for enterprise {enterprise_customer_uuid}. '
        f'The email includes {len(subsidy_requests)} subsidy requests.'
    )
    braze_client = BrazeApiClient()
    try:
        braze_client.send_campaign_message(
            settings.BRAZE_NEW_REQUESTS_NOTIFICATION_CAMPAIGN,
            emails=[admin_user['email'].lower() for admin_user in admin_users],
            trigger_properties=braze_trigger_properties,
        )
    except HTTPError as exc:
        logger.exception(exc)
        raise

    customer_config.last_remind_date = datetime.now()
    customer_config.save()
