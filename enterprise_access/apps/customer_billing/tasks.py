"""
Tasks for customer billing app.
"""

import logging
from datetime import datetime

import stripe
from celery import shared_task
from django.conf import settings

from enterprise_access.apps.api_client.braze_client import BrazeApiClient
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.customer_billing.api import create_stripe_billing_portal_session
from enterprise_access.apps.customer_billing.models import CheckoutIntent
from enterprise_access.tasks import LoggedTaskWithRetry

logger = logging.getLogger(__name__)


def _get_billing_portal_url(checkout_intent):
    """
    Generate Stripe billing portal URL for the customer to restart their subscription.
    Falls back to learner portal if billing portal creation fails.

    Args:
        checkout_intent (CheckoutIntent): The checkout intent record

    Returns:
        str: Stripe billing portal URL or fallback learner portal URL
    """
    # Construct the return URL where user will be redirected after using the portal
    return_url = (
        f"{settings.ENTERPRISE_LEARNER_PORTAL_URL}/{checkout_intent.enterprise_slug}"
        if checkout_intent.enterprise_slug
        else settings.ENTERPRISE_LEARNER_PORTAL_URL
    )

    # Use the reusable API helper to create the portal session
    try:
        portal_session = create_stripe_billing_portal_session(
            checkout_intent=checkout_intent,
            return_url=return_url,
        )
        return portal_session.url
    except (ValueError, stripe.StripeError) as exc:
        logger.warning(
            "Could not create billing portal URL for CheckoutIntent %s: %s. "
            "Using fallback learner portal URL.",
            checkout_intent.id,
            str(exc),
        )
        return return_url


@shared_task(base=LoggedTaskWithRetry)
def send_trial_cancellation_email_task(
    checkout_intent_id, trial_end_timestamp
):
    """
    Send Braze email notification when a trial subscription is canceled.

    This task handles sending a cancellation confirmation email to enterprise
    admins when their trial subscription has been canceled. The email includes
    the trial end date and a link to restart their subscription via the Stripe
    billing portal.

    Args:
        checkout_intent_id (int): ID of the CheckoutIntent record
        trial_end_timestamp (int): Unix timestamp of when the trial period ends

    Raises:
        BrazeClientError: If there's an error communicating with Braze
        Exception: For any other unexpected errors during email sending
    """
    try:
        checkout_intent = CheckoutIntent.objects.get(id=checkout_intent_id)
    except CheckoutIntent.DoesNotExist:
        logger.error(
            "Email not sent: CheckoutIntent %s not found for trial cancellation email",
            checkout_intent_id,
        )
        return

    enterprise_slug = checkout_intent.enterprise_slug
    logger.info(
        "Sending trial cancellation email for CheckoutIntent %s (enterprise slug: %s)",
        checkout_intent_id,
        enterprise_slug,
    )

    braze_client = BrazeApiClient()
    lms_client = LmsApiClient()

    # Fetch enterprise customer data to get admin users
    try:
        enterprise_data = lms_client.get_enterprise_customer_data(
            enterprise_customer_slug=enterprise_slug
        )
    except Exception as exc:
        logger.error(
            "Failed to fetch enterprise data for slug %s: %s. Cannot send cancellation email.",
            enterprise_slug,
            str(exc),
        )
        return

    admin_users = enterprise_data.get("admin_users", [])

    if not admin_users:
        logger.error(
            "Cancellation email not sent: No admin users found for enterprise slug %s. "
            "Verify admin setup in LMS.",
            enterprise_slug,
        )
        return

    # Format trial end date for email template
    trial_end_date = datetime.fromtimestamp(trial_end_timestamp).strftime(
        "%B %d, %Y"
    )

    # Generate Stripe billing portal URL for restarting subscription
    restart_url = _get_billing_portal_url(checkout_intent)

    braze_trigger_properties = {
        "trial_end_date": trial_end_date,
        "restart_subscription_url": restart_url,
    }

    # Create Braze recipients for all admin users
    recipients = []
    for admin in admin_users:
        try:
            admin_email = admin["email"]
            recipient = braze_client.create_braze_recipient(
                user_email=admin_email,
                lms_user_id=admin.get("lms_user_id"),
            )
            recipients.append(recipient)

        except Exception as exc:
            logger.warning(
                "Failed to create Braze recipient for admin email %s: %s",
                admin_email,
                str(exc),
            )

    if not recipients:
        logger.error(
            "Cancellation email not sent: No valid Braze recipients created for enterprise slug %s. "
            "Check admin email errors above.",
            enterprise_slug,
        )
        return

    # Send the campaign message to all admin recipients
    try:
        braze_client.send_campaign_message(
            settings.BRAZE_TRIAL_CANCELLATION_CAMPAIGN,
            recipients=recipients,
            trigger_properties=braze_trigger_properties,
        )
        logger.info(
            "Successfully sent trial cancellation emails for CheckoutIntent %s to %d recipients",
            checkout_intent_id,
            len(recipients),
        )

    except Exception as exc:
        logger.exception(
            "Braze API error: Failed to send trial cancellation email for CheckoutIntent %s. Error: %s",
            checkout_intent_id,
            str(exc),
        )
        raise
