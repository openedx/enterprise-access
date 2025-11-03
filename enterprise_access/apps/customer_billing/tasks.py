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
from enterprise_access.apps.content_assignments.content_metadata_api import format_datetime_obj
from enterprise_access.apps.customer_billing.api import create_stripe_billing_portal_session
from enterprise_access.apps.customer_billing.models import CheckoutIntent
from enterprise_access.apps.provisioning.utils import validate_trial_subscription
from enterprise_access.tasks import LoggedTaskWithRetry
from enterprise_access.utils import cents_to_dollars

logger = logging.getLogger(__name__)


@shared_task(base=LoggedTaskWithRetry)
def send_payment_receipt_email(
    invoice_data,
    subscription_data,
    enterprise_customer_name,
    enterprise_slug,
):
    """
    Send payment receipt emails to enterprise admins after successful payment.

    Args:
        invoice_data (dict): The Stripe invoice data containing payment details
        subscription_data (dict): The Stripe subscription data
        enterprise_customer_name (str): Name of the enterprise organization
        enterprise_slug (str): URL-friendly slug for the enterprise

    Raises:
        BrazeClientError: If there's an error communicating with Braze
        Exception: For any other unexpected errors during email sending
    """
    logger.info(
        'Sending payment receipt confirmation email for enterprise %s (slug: %s)',
        enterprise_customer_name,
        enterprise_slug,
    )

    braze_client = BrazeApiClient()
    lms_client = LmsApiClient()

    enterprise_data = lms_client.get_enterprise_customer_data(enterprise_customer_slug=enterprise_slug)
    admin_users = enterprise_data.get('admin_users', [])

    if not admin_users:
        logger.error(
            'Payment receipt confirmation email not sent: No admin users found for enterprise %s (slug: %s)',
            enterprise_customer_name,
            enterprise_slug,
        )
        return

    # Format the payment date
    payment_date = datetime.fromtimestamp(invoice_data.get('created', 0))
    formatted_date = format_datetime_obj(payment_date, '%d %B %Y')

    # Get payment method details
    payment_method = invoice_data.get('payment_intent', {}).get('payment_method', {})
    card_details = payment_method.get('card', {})
    payment_method_display = f"{card_details.get('brand', 'Card')} - {card_details.get('last4', '****')}"

    # Get subscription details
    quantity = subscription_data.get('quantity', 0)
    price_per_license = subscription_data.get('plan', {}).get('amount', 0)
    total_amount = quantity * price_per_license

    # Get billing address
    billing_details = payment_method.get('billing_details', {})
    address = billing_details.get('address', {})
    billing_address = '\n'.join(filter(None, [
        address.get('line1', ''),
        address.get('line2', ''),
        f"{address.get('city', '')}, {address.get('state', '')} {address.get('postal_code', '')}",
        address.get('country', '')
    ]))

    braze_trigger_properties = {
        'total_paid_amount': cents_to_dollars(total_amount),
        'date_paid': formatted_date,
        'payment_method': payment_method_display,
        'license_count': quantity,
        'price_per_license': cents_to_dollars(price_per_license),
        'customer_name': billing_details.get('name', ''),
        'organization': enterprise_customer_name,
        'billing_address': billing_address,
        'enterprise_admin_portal_url': f'{settings.ENTERPRISE_ADMIN_PORTAL_URL}/{enterprise_slug}',
        'receipt_number': invoice_data.get('id', ''),
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
            'Payment receipt confirmation email not sent: No valid Braze recipients created for enterprise %s.'
            ' Check admin email errors above.',
            enterprise_customer_name
        )
        return

    try:
        braze_client.send_campaign_message(
            settings.BRAZE_ENTERPRISE_PROVISION_PAYMENT_RECEIPT_CAMPAIGN,
            recipients=recipients,
            trigger_properties=braze_trigger_properties,
        )
        logger.info(
            'Successfully sent payment receipt confirmation emails for enterprise %s to %d recipients',
            enterprise_customer_name,
            len(recipients)
        )

    except Exception as exc:
        logger.exception(
            'Braze API error: Failed to send payment receipt confirmation email for enterprise %s. Error: %s',
            enterprise_customer_name,
            str(exc)
        )
        raise


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

        except Exception as exc:  # pylint: disable=broad-exception-caught
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
    except Exception as exc:  # pylint: disable=broad-exception-caught
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
        except Exception as exc:  # pylint: disable=broad-exception-caught
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
