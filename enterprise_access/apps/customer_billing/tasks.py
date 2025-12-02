"""
Tasks for customer billing app.
"""

import logging
from datetime import datetime

import stripe
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from enterprise_access.apps.api_client.braze_client import BrazeApiClient
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.customer_billing.api import create_stripe_billing_portal_session
from enterprise_access.apps.customer_billing.models import CheckoutIntent, StripeEventSummary
from enterprise_access.apps.customer_billing.stripe_api import get_stripe_subscription, get_stripe_trialing_subscription
from enterprise_access.apps.provisioning.utils import validate_trial_subscription
from enterprise_access.tasks import LoggedTaskWithRetry
from enterprise_access.utils import cents_to_dollars, format_cents_for_user_display, format_datetime_obj

logger = logging.getLogger(__name__)


def _get_admin_recipients(enterprise_slug: str | None) -> list:
    """Return Braze recipients for all admins of the given enterprise slug."""
    if not enterprise_slug:
        logger.error(
            "Email not sent: Missing enterprise slug; cannot look up admin recipients.",
        )
        return []

    lms_client = LmsApiClient()
    try:
        enterprise_data = lms_client.get_enterprise_customer_data(
            enterprise_customer_slug=enterprise_slug
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error(
            "Failed to fetch enterprise data for slug %s: %s. Cannot create recipients.",
            enterprise_slug,
            str(exc),
        )
        return []

    admin_users = enterprise_data.get("admin_users", [])
    if not admin_users:
        logger.error(
            "Email not sent: No admin users found for enterprise slug %s. Verify admin setup in LMS.",
            enterprise_slug,
        )
        return []

    braze_client = BrazeApiClient()
    recipients: list = []
    for admin in admin_users:
        try:
            admin_email = admin.get("email")
            recipient = braze_client.create_braze_recipient(
                user_email=admin_email,
                lms_user_id=admin.get("lms_user_id"),
            )
            recipients.append(recipient)
        except Exception as exc:  # pylint: disable-broad-exception-caught
            logger.warning(
                "Failed to create Braze recipient for admin email %s: %s",
                admin.get("email"),
                str(exc),
            )

    if not recipients:
        logger.error(
            "Email not sent: No valid Braze recipients created for enterprise slug %s. Check admin email errors above.",
            enterprise_slug,
        )

    return recipients


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


def _prepare_admin_recipients_and_portal(checkout_intent_or_id):
    """
    Prepare Braze recipients for enterprise admins and the Stripe billing portal URL.

    Centralizes the common logic used by multiple email tasks:
    - Load CheckoutIntent
    - Fetch enterprise admins from LMS
    - Create Braze recipients
    - Build Stripe billing portal URL (with learner portal fallback)

    Args:
        checkout_intent_or_id (CheckoutIntent | int): CheckoutIntent instance or ID

    Returns:
        tuple[list, str | None, str | None]: (recipients, enterprise_slug, portal_url)
            - recipients: list of Braze recipients (empty if any prerequisite fails)
            - enterprise_slug: slug string if available; otherwise None
            - portal_url: URL string if available; otherwise None
    """
    if isinstance(checkout_intent_or_id, CheckoutIntent):
        checkout_intent = checkout_intent_or_id
        checkout_intent_id = checkout_intent.id
    else:
        checkout_intent_id = checkout_intent_or_id
        try:
            checkout_intent = CheckoutIntent.objects.get(id=checkout_intent_id)
        except CheckoutIntent.DoesNotExist:
            logger.error(
                "Email not sent: CheckoutIntent %s not found",
                checkout_intent_id,
            )
            return [], None, None

    enterprise_slug = checkout_intent.enterprise_slug
    # Build the portal URL early; it doesn't depend on LMS call success
    portal_url = _get_billing_portal_url(checkout_intent)

    recipients = _get_admin_recipients(enterprise_slug)
    if not recipients:
        return [], enterprise_slug, portal_url

    return recipients, enterprise_slug, portal_url


@shared_task(base=LoggedTaskWithRetry)
def send_enterprise_provision_signup_confirmation_email(
        subscription_start_date: datetime,
        subscription_end_date: datetime,
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

    trial_start_date = timezone.make_aware(datetime.fromtimestamp(subscription['trial_start']))
    trial_end_date = timezone.make_aware(datetime.fromtimestamp(subscription['trial_end']))

    braze_trigger_properties = {
        'subscription_start_date': format_datetime_obj(subscription_start_date),
        'subscription_end_date': format_datetime_obj(subscription_end_date),
        'number_of_licenses': number_of_licenses,
        'organization': organization_name,
        'enterprise_admin_portal_url': f'{settings.ENTERPRISE_ADMIN_PORTAL_URL}/{enterprise_slug}',
        'trial_start_date': format_datetime_obj(trial_start_date),
        'trial_end_date': format_datetime_obj(trial_end_date),
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
    recipients, enterprise_slug, portal_url = _prepare_admin_recipients_and_portal(checkout_intent_id)

    if not recipients:
        return

    logger.info(
        "Sending trial cancellation email for CheckoutIntent %s (enterprise slug: %s)",
        checkout_intent_id,
        enterprise_slug,
    )

    # Format trial end date for email template
    trial_end_date = format_datetime_obj(timezone.make_aware(datetime.fromtimestamp(trial_end_timestamp)))

    braze_trigger_properties = {
        "trial_end_date": trial_end_date,
        "restart_subscription_url": portal_url,
    }

    # Send the campaign message to all admin recipients
    try:
        braze_client = BrazeApiClient()
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


@shared_task(base=LoggedTaskWithRetry)
def send_billing_error_email_task(checkout_intent_id: int):
    """
    Send Braze email notification when a subscription encounters a billing error
    (e.g., transitions to past_due).

    The email includes a link to the Stripe billing portal so admins can fix their
    payment method and restart the subscription.

    Args:
        checkout_intent_id (int): ID of the CheckoutIntent record
    """
    recipients, enterprise_slug, portal_url = _prepare_admin_recipients_and_portal(checkout_intent_id)

    if not recipients:
        return

    logger.info(
        "Sending billing error email for CheckoutIntent %s (enterprise slug: %s)",
        checkout_intent_id,
        enterprise_slug,
    )

    braze_trigger_properties = {
        "restart_subscription_url": portal_url,
    }

    # Send the campaign message to all admin recipients
    try:
        braze_client = BrazeApiClient()
        braze_client.send_campaign_message(
            settings.BRAZE_BILLING_ERROR_CAMPAIGN,
            recipients=recipients,
            trigger_properties=braze_trigger_properties,
        )
        logger.info(
            "Successfully sent billing error emails for CheckoutIntent %s to %d recipients",
            checkout_intent_id,
            len(recipients),
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception(
            "Braze API error: Failed to send billing error email for CheckoutIntent %s. Error: %s",
            checkout_intent_id,
            str(exc),
        )
        raise


@shared_task(base=LoggedTaskWithRetry)
def send_trial_ending_reminder_email_task(checkout_intent_id):
    """
    Send Braze email notification 72 hours before trial subscription ends.

    This task handles sending a reminder email to enterprise admins when their
    trial subscription is about to end. The email includes subscription details,
    renewal information, and a link to manage their subscription.

    Args:
        checkout_intent_id (int): ID of the CheckoutIntent record

    Raises:
        BrazeClientError: If there's an error communicating with Braze
        Exception: For any other unexpected errors during email sending
    """
    try:
        checkout_intent = CheckoutIntent.objects.get(id=checkout_intent_id)
    except CheckoutIntent.DoesNotExist:
        logger.error(
            "Email not sent: CheckoutIntent %s not found for trial ending reminder email",
            checkout_intent_id,
        )
        return

    enterprise_slug = checkout_intent.enterprise_slug
    logger.info(
        "Sending trial ending reminder email for CheckoutIntent %s (enterprise slug: %s)",
        checkout_intent_id,
        enterprise_slug,
    )

    # DRY: reuse shared helper to build recipients and billing portal URL
    recipients, _slug, subscription_management_url = _prepare_admin_recipients_and_portal(checkout_intent)
    if not recipients:
        return

    # Retrieve subscription details from Stripe
    try:
        if not checkout_intent.stripe_customer_id:
            logger.error(
                "Trial ending reminder email not sent: No Stripe customer ID for CheckoutIntent %s",
                checkout_intent_id,
            )
            return

        # Get the trialing subscription using the existing utility method
        subscription = get_stripe_trialing_subscription(
            checkout_intent.stripe_customer_id
        )

        if not subscription:
            logger.error(
                "Trial ending reminder email not sent: No active trial subscription found for customer %s",
                checkout_intent.stripe_customer_id,
            )
            return

        if not subscription["items"].data:
            logger.error(
                "Trial ending reminder email not sent: Subscription %s has no items",
                subscription.id,
            )
            return

        first_item = subscription["items"].data[0]
        renewal_date = format_datetime_obj(
            timezone.make_aware(datetime.fromtimestamp(first_item.current_period_end))
        )
        license_count = first_item.quantity

        # Get payment method details with card brand
        payment_method_info = ""
        if subscription.default_payment_method:
            payment_method = stripe.PaymentMethod.retrieve(
                subscription.default_payment_method
            )
            if payment_method.type == "card":
                brand = (
                    payment_method.card.brand.capitalize()
                )  # e.g., "Visa", "Mastercard"
                last4 = payment_method.card.last4
                payment_method_info = f"{brand} ending in {last4}"

        total_paid_amount = "$0.00 USD"
        if subscription.latest_invoice:
            invoice_summary = StripeEventSummary.get_latest_invoice_paid(
                subscription.latest_invoice
            )

            if invoice_summary and invoice_summary.invoice_amount_paid is not None:
                total_paid_amount = format_cents_for_user_display(
                    invoice_summary.invoice_amount_paid
                )
            else:
                logger.warning(
                    "No invoice summary found for invoice %s, falling back to $0.00 USD",
                    subscription.latest_invoice,
                )

    except stripe.StripeError as exc:
        logger.error(
            "Stripe API error while fetching subscription details for CheckoutIntent %s: %s",
            checkout_intent_id,
            str(exc),
        )
        return
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error(
            "Error retrieving subscription details for CheckoutIntent %s: %s",
            checkout_intent_id,
            str(exc),
        )
        return

    braze_trigger_properties = {
        "renewal_date": renewal_date,
        "subscription_management_url": subscription_management_url,
        "license_count": license_count,
        "payment_method": payment_method_info,
        "total_paid_amount": total_paid_amount,
    }

    try:
        BrazeApiClient().send_campaign_message(
            settings.BRAZE_ENTERPRISE_PROVISION_TRIAL_ENDING_SOON_CAMPAIGN,
            recipients=recipients,
            trigger_properties=braze_trigger_properties,
        )
        logger.info(
            "Successfully sent trial ending reminder emails for CheckoutIntent %s to %d recipients",
            checkout_intent_id,
            len(recipients),
        )

    except Exception as exc:
        logger.exception(
            "Braze API error: Failed to send trial ending reminder email for CheckoutIntent %s. Error: %s",
            checkout_intent_id,
            str(exc),
        )
        raise


@shared_task(base=LoggedTaskWithRetry)
def send_trial_end_and_subscription_started_email_task(
    subscription_id: str,
    checkout_intent_id: int,
):  # pylint: disable=too-many-statements
    """
    Send an email to all enterprise admins notifying about trial end and subscription start.

    Args:
        subscription_id (str): Stripe subscription ID
        checkout_intent_id (int): CheckoutIntent DB ID
    """
    logger.info(
        "Sending trial end and subscription started email for subscription %s and checkout_intent %s",
        subscription_id, checkout_intent_id
    )
    braze_client = BrazeApiClient()
    lms_client = LmsApiClient()

    subscription = get_stripe_subscription(subscription_id)
    checkout_intent = CheckoutIntent.objects.get(id=checkout_intent_id)

    total_license = subscription.get('quantity')
    plan = subscription.get('plan', {})
    amount_cents = plan.get('amount', 0)
    billing_amount = str(cents_to_dollars(amount_cents)) if amount_cents else None

    period_start = subscription.get('current_period_start')
    period_end = subscription.get('current_period_end')
    subscription_period = None
    next_payment_date = None
    if period_start and period_end:
        start_str = format_datetime_obj(datetime.utcfromtimestamp(period_start))
        end_str = format_datetime_obj(datetime.utcfromtimestamp(period_end))
        subscription_period = f"{start_str} – {end_str}"
        next_payment_date = end_str

    organization_name = checkout_intent.enterprise_name
    enterprise_slug = checkout_intent.enterprise_slug

    invoice_url = None
    latest_invoice = subscription.get('latest_invoice')
    if latest_invoice:
        invoice_url = getattr(latest_invoice, 'hosted_invoice_url', None)
        if not invoice_url and isinstance(latest_invoice, dict):
            invoice_url = latest_invoice.get('hosted_invoice_url')

    admin_emails = []
    if enterprise_slug:
        try:
            enterprise_data = lms_client.get_enterprise_customer_data(enterprise_customer_slug=enterprise_slug)
            admin_users = enterprise_data.get('admin_users', [])
            admin_emails = [admin['email'] for admin in admin_users if 'email' in admin]
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error(
                "Failed to fetch admin users for enterprise slug %s: %s. No emails will be sent.",
                enterprise_slug, str(exc)
            )
            return
    if not admin_emails:
        logger.error(
            "No admin users found for enterprise slug %s. No emails will be sent.",
            enterprise_slug
        )
        return

    recipients = []
    for admin_email in admin_emails:
        try:
            recipient = braze_client.create_braze_recipient(user_email=admin_email)
            recipients.append(recipient)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                'Failed to create Braze recipient for admin email %s: %s',
                admin_email, str(exc)
            )

    if not recipients:
        logger.error(
            'No valid Braze recipients created for enterprise %s. Check admin email errors above.',
            enterprise_slug
        )
        return

    trigger_properties = {
        'total_license': total_license,
        'billing_amount': billing_amount,
        'subscription_period': subscription_period,
        'next_payment_date': next_payment_date,
        'organization': organization_name,
        'enterprise_admin_portal_url': f'{settings.ENTERPRISE_ADMIN_PORTAL_URL}/{enterprise_slug}',
        'invoice_url': invoice_url,
    }

    try:
        braze_client.send_campaign_message(
            settings.BRAZE_ENTERPRISE_PROVISION_TRIAL_END_SUBSCRIPTION_STARTED_CAMPAIGN,
            recipients=recipients,
            trigger_properties=trigger_properties,
        )
        logger.info(
            'Successfully sent trial end and subscription started email to %d admin(s) for enterprise %s',
            len(recipients), enterprise_slug
        )
    except Exception as exc:
        logger.exception(
            'Braze API error: Failed to send trial end/subscription started email for enterprise %s. Error: %s',
            enterprise_slug, str(exc)
        )
        raise


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

    recipients = _get_admin_recipients(enterprise_slug)
    if not recipients:
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

    # recipients already prepared above

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
