"""
Tasks for customer billing app.
"""

import logging

from celery import shared_task
from django.conf import settings

from enterprise_access.apps.api_client.braze_client import ENTERPRISE_BRAZE_ALIAS_LABEL, BrazeApiClient
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.provisioning.utils import validate_trial_subscription
from enterprise_access.tasks import LoggedTaskWithRetry
from enterprise_access.utils import cents_to_dollars

logger = logging.getLogger(__name__)


@shared_task(base=LoggedTaskWithRetry)
def send_enterprise_provision_signup_confirmation_email(
        subscription_start_date: str,
        subscription_end_date: str,
        number_of_licenses: int,
        organization_name: str,
        enterprise_slug: str,
):
    """
    Send confirmation emails to enterprise admins after successful signup and provisioning.

    This task handles sending a confirmation email to enterprise admins when their
    subscription has been successfully set up. It includes both subscription details and
    trial period information if a valid trial subscription exists.

    Args:
        subscription_start_date (str): The start date of the subscription
        subscription_end_date (str): The end date of the subscription
        number_of_licenses (int): Number of licenses purchased/allocated
        organization_name (str): Name of the enterprise organization
        enterprise_slug (str): URL-friendly slug for the enterprise

    Raises:
        BrazeClientError: If there's an error communicating with Braze
        Exception: For any other unexpected errors during email sending
    """

    is_valid, subscription = validate_trial_subscription(enterprise_slug)
    if not is_valid or not subscription:
        logger.error(
            'Email not sent: No valid trial subscription found for enterprise %s (slug: %s)',
            organization_name,
            enterprise_slug,
        )
        return

    logger.info(
        'Sending signup confirmation email for enterprise %s (slug: %s)',
        organization_name,
        enterprise_slug,
    )

    braze_client = BrazeApiClient()
    lms_client = LmsApiClient()

    enterprise_data = lms_client.get_enterprise_customer_data(enterprise_customer_slug=enterprise_slug)
    admin_users = enterprise_data.get('admin_users', [])

    if not admin_users:
        logger.error(
            'Signup email not sent: No admin users found for enterprise %s (slug: %s). Verify admin setup in LMS.',
            organization_name,
            enterprise_slug,
        )
        return

    braze_trigger_properties = {
        'subscription_start_date': subscription_start_date,
        'subscription_end_date': subscription_end_date,
        'number_of_licenses': number_of_licenses,
        'organization': organization_name,
        'enterprise_admin_portal_url': f'{settings.ENTERPRISE_ADMIN_PORTAL_URL}/{enterprise_slug}',
        'trial_start_date': subscription['trial_start'],
        'trial_end_date': subscription['trial_end'],
        'plan_amount': cents_to_dollars(subscription['plan']['amount']),
    }

    recipients = []
    for admin in admin_users:
        try:
            admin_email = admin['email']
            recipient = braze_client.create_braze_recipient(
                user_email=admin_email,
                lms_user_id=admin.get('lms_user_id'),
            )
            recipients.append(recipient)

        except Exception as exc:
            logger.warning(
                'Failed to create Braze recipient for admin email %s: %s',
                admin_email,
                str(exc)
            )

    if not recipients:
        logger.error(
            'Signup email not sent: No valid Braze recipients created for enterprise %s.'
            ' Check admin email errors above.',
            organization_name
        )
        return

    try:
        braze_client.send_campaign_message(
            settings.BRAZE_ENTERPRISE_PROVISION_SIGNUP_CONFIRMATION_CAMPAIGN,
            recipients=recipients,
            trigger_properties=braze_trigger_properties,
        )
        logger.info(
            'Successfully sent signup confirmation emails for enterprise %s to %d recipients',
            organization_name,
            len(recipients)
        )

    except Exception as exc:
        logger.exception(
            'Braze API error: Failed to send signup email for enterprise %s. Error: %s',
            organization_name,
            str(exc)
        )
        raise
