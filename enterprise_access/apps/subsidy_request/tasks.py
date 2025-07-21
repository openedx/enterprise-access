"""
Tasks for subsidy requests app.
"""

import logging
from datetime import datetime

from celery import shared_task
from django.apps import apps
from django.conf import settings

from enterprise_access.apps.api_client.braze_client import BrazeApiClient
from enterprise_access.apps.api_client.discovery_client import DiscoveryApiClient
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.content_assignments.tasks import BrazeCampaignSender, _get_assignment_or_raise
from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates
from enterprise_access.tasks import LoggedTaskWithRetry
from enterprise_access.utils import get_subsidy_model

logger = logging.getLogger(__name__)


def _get_course_partners(course_data):
    """
    Returns a list of course partner data for subsidy requests given a course dictionary.
    """
    owners = course_data.get('owners') or []
    return [{'uuid': owner.get('uuid'), 'name': owner.get('name')} for owner in owners]


@shared_task(base=LoggedTaskWithRetry)
def update_course_info_for_subsidy_request_task(model_name, subsidy_request_uuid):
    """
    Get course info (e.g. title, partners) from lms and update subsidy_request with it.
    """
    subsidy_model = apps.get_model('subsidy_request', model_name)
    subsidy_request = subsidy_model.objects.get(uuid=subsidy_request_uuid)

    discovery_client = DiscoveryApiClient()
    course_data = discovery_client.get_course_data(subsidy_request.course_id)
    subsidy_request.course_title = course_data['title']
    subsidy_request.course_partners = _get_course_partners(course_data)

    # Use bulk_update so we don't trigger save() again
    subsidy_model.bulk_update([subsidy_request], ['course_title', 'course_partners'])


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
        HTTPError if Braze client call fails with an HTTPError
    """
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
    lms_client = LmsApiClient()
    enterprise_customer_data = lms_client.get_enterprise_customer_data(enterprise_customer_uuid)
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

    admin_users = enterprise_customer_data['admin_users']

    logger.info(
        f'Sending new-requests email to admins for enterprise {enterprise_customer_uuid}. '
        f'The email includes {len(subsidy_requests)} subsidy requests. '
        f'Sending to: {admin_users}'
    )
    braze_client = BrazeApiClient()
    recipients = [
        braze_client.create_recipient(
            user_email=admin_user['email'],
            lms_user_id=admin_user['lms_user_id']
        )
        for admin_user in admin_users
    ]
    try:
        braze_client.send_campaign_message(
            settings.BRAZE_NEW_REQUESTS_NOTIFICATION_CAMPAIGN,
            recipients=recipients,
            trigger_properties=braze_trigger_properties,
        )

    except Exception:
        logger.exception(f'Exception sending braze campaign email message for enterprise {enterprise_customer_uuid}.')
        raise

    customer_config.last_remind_date = datetime.now()
    customer_config.save()


@shared_task(base=LoggedTaskWithRetry)
def send_learner_credit_bnr_admins_email_with_new_requests_task(
        policy_uuid, lc_request_config_uuid, enterprise_customer_uuid
):
    """
    Task to send new learner credit request emails to admins.

    This task can be manually triggered from browse_and_request when a new
    LearnerCreditRequest is created. It will send the latest 10 requests in
    REQUESTED state to enterprise admins.

    Args:
        policy_uuid (str): subsidy access policy uuid identifier
        lc_request_config_uuid (str): learner credit request config uuid identifier
        enterprise_customer_uuid (str): enterprise customer uuid identifier
    Raises:
        HTTPError if Braze client call fails with an HTTPError
    """

    subsidy_model = apps.get_model('subsidy_request.LearnerCreditRequest')
    subsidy_requests = subsidy_model.objects.filter(
        enterprise_customer_uuid=enterprise_customer_uuid,
        learner_credit_request_config__uuid=lc_request_config_uuid,
        state=SubsidyRequestStates.REQUESTED,
    )
    latest_subsidy_requests = subsidy_requests.order_by('-created')[:10]

    if not subsidy_requests:
        logger.info(
            'No learner credit requests in REQUESTED state. Not sending new requests '
            f'email to admins for enterprise {enterprise_customer_uuid}.'
        )
        return

    lms_client = LmsApiClient()
    enterprise_customer_data = lms_client.get_enterprise_customer_data(enterprise_customer_uuid)
    enterprise_slug = enterprise_customer_data.get('slug')

    manage_requests_url = (f'{settings.ENTERPRISE_ADMIN_PORTAL_URL}/{enterprise_slug}'
                           f'/admin/learner-credit/{policy_uuid}/requests')
    admin_users = enterprise_customer_data['admin_users']

    if not admin_users:
        logger.info(
            f'No admin users found for enterprise {enterprise_customer_uuid}. '
            'Not sending new requests email.'
        )
        return
    braze_client = BrazeApiClient()
    recipients = [
        braze_client.create_recipient(
            user_email=admin_user['email'],
            lms_user_id=admin_user['lms_user_id']
        )
        for admin_user in admin_users
    ]

    braze_trigger_properties = {
        'manage_requests_url': manage_requests_url,
        'requests': [],
        'total_requests': len(subsidy_requests),
    }

    for subsidy_request in latest_subsidy_requests:
        braze_trigger_properties['requests'].append({
            'user_email': subsidy_request.user.email,
            'course_title': subsidy_request.course_title,
        })

    logger.info(
        f'Sending learner credit requests email to admins for enterprise {enterprise_customer_uuid}. '
        f'This includes {len(subsidy_requests)} requests. '
        f'Sending to: {admin_users}'
    )

    try:
        braze_client.send_campaign_message(
            settings.BRAZE_LEARNER_CREDIT_BNR_NEW_REQUESTS_NOTIFICATION_CAMPAIGN,
            recipients=recipients,
            trigger_properties=braze_trigger_properties,
        )
    except Exception:
        logger.exception(
            f'Exception sending Braze campaign email for enterprise {enterprise_customer_uuid}.'
        )
        raise


@shared_task(base=LoggedTaskWithRetry)
def send_learner_credit_bnr_request_approve_task(approved_assignment_uuid):
    """
    Send email via braze for approving bnr learner credit request.

    Args:
        approved_assignment_uuid: (string) the approved assignment uuid
    """
    assignment = _get_assignment_or_raise(approved_assignment_uuid)
    campaign_sender = BrazeCampaignSender(assignment)

    braze_trigger_properties = campaign_sender.get_properties(
        'contact_admin_link',
        'organization',
        'course_title',
        'start_date',
        'course_partner',
        'course_card_image'
    )
    campaign_uuid = settings.BRAZE_LEARNER_CREDIT_BNR_APPROVED_NOTIFICATION_CAMPAIGN
    campaign_sender.send_campaign_message(
        braze_trigger_properties,
        campaign_uuid,
    )
    logger.info(f'Sent braze campaign approved uuid={campaign_uuid} message for assignment {assignment}')


@shared_task(base=LoggedTaskWithRetry)
def send_reminder_email_for_pending_learner_credit_request(assignment_uuid):
    """
    Send email via braze for reminding users of their pending learner credit request
    Args:
        assignment_uuid (str): The UUID of the LearnerContentAssignment associated with the LCR.
    """
    assignment = _get_assignment_or_raise(assignment_uuid)

    campaign_sender = BrazeCampaignSender(assignment)
    braze_trigger_properties = campaign_sender.get_properties(
        'contact_admin_link',
        'organization',
        'course_title',
        'start_date',
        'course_partner',
        'course_card_image',
    )
    campaign_uuid = settings.BRAZE_LEARNER_CREDIT_BNR_REMIND_NOTIFICATION_CAMPAIGN
    campaign_sender.send_campaign_message(
        braze_trigger_properties,
        campaign_uuid,
    )
    logger.info(f'Sent braze campaign reminder uuid={campaign_uuid} message for assignment {assignment}')
