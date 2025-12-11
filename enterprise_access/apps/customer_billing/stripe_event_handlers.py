"""
Stripe event handlers
"""
import logging
from collections.abc import Callable
from functools import wraps
from uuid import UUID

import stripe
from django.conf import settings

from enterprise_access.apps.api_client.license_manager_client import LicenseManagerApiClient
from enterprise_access.apps.customer_billing.models import (
    CheckoutIntent,
    SelfServiceSubscriptionRenewal,
    StripeEventData,
    StripeEventSummary
)
from enterprise_access.apps.customer_billing.stripe_event_types import StripeEventType
from enterprise_access.apps.customer_billing.tasks import (
    send_billing_error_email_task,
    send_payment_receipt_email,
    send_trial_cancellation_email_task,
    send_trial_end_and_subscription_started_email_task,
    send_trial_ending_reminder_email_task
)

logger = logging.getLogger(__name__)

# Central registry for event handlers.
#
# Needs to be in module scope instead of class scope because the decorator
# didn't have access to the class name soon enough during runtime initialization.
_handlers_by_type: dict[StripeEventType, Callable[[stripe.Event], None]] = {}


def get_invoice_and_subscription(event: stripe.Event):
    """
    Given a stripe invoice event, return the invoice and related subscription records.
    """
    invoice = event.data.object
    subscription_details = invoice.parent.subscription_details
    return invoice, subscription_details


def get_checkout_intent_id_from_subscription(stripe_subscription):
    """
    Returns the CheckoutIntent identifier stored in the given
    stripe subscription's metadata, or None if no such value is present.
    """
    if 'checkout_intent_id' in stripe_subscription.metadata:
        # The stripe subscription object may actually be a SubscriptionDetails
        # record from an invoice.
        stripe_subscription_id = (
            getattr(stripe_subscription, 'id', None) or getattr(stripe_subscription, 'subscription', None)
        )
        checkout_intent_id = int(stripe_subscription.metadata['checkout_intent_id'])
        logger.info(
            'Found checkout_intent_id=%s from subscription=%s',
            checkout_intent_id, stripe_subscription_id,
        )
        return checkout_intent_id
    return None


def persist_stripe_event(event: stripe.Event) -> StripeEventData:
    """
    Creates and returns a new ``StripeEventData`` object.
    """
    stripe_subscription = None
    if event.type == 'invoice.paid':
        _, stripe_subscription = get_invoice_and_subscription(event)
    elif event.type.startswith('customer.subscription'):
        stripe_subscription = event.data.object

    if not stripe_subscription:
        logger.error(
            'Cannot persist StripeEventData, no subscription found for event %s with type %s',
            event.id,
            event.type,
        )
        return None

    checkout_intent_id = get_checkout_intent_id_from_subscription(stripe_subscription)
    checkout_intent = CheckoutIntent.objects.filter(
        id=checkout_intent_id,
        stripe_customer_id=event.data.object.get('customer'),
    ).first()

    record, _ = StripeEventData.objects.get_or_create(
        event_id=event.id,
        defaults={
            'event_type': event.type,
            'checkout_intent': checkout_intent,
            'data': dict(event),
        },
    )
    logger.info('Persisted StripeEventData %s', record)
    return record


def get_checkout_intent_or_raise(checkout_intent_id, event_id) -> CheckoutIntent:
    """
    Returns a CheckoutIntent with the given id, or logs and raises an exception.
    """
    try:
        checkout_intent = CheckoutIntent.objects.get(id=checkout_intent_id)
        return checkout_intent
    except CheckoutIntent.DoesNotExist:
        logger.warning(
            'Could not find CheckoutIntent record with id %s for event %s',
            checkout_intent_id, event_id,
        )
        raise


def handle_pending_update(subscription_id: str, checkout_intent_id: int, pending_update):
    """
    Log pending update information for visibility.
    Assumes a pending_update is present.
    """
    # TODO: take necessary action on the actual SubscriptionPlan and update the CheckoutIntent.
    logger.warning(
        "Subscription %s has pending update: %s. checkout_intent_id: %s",
        subscription_id,
        pending_update,
        checkout_intent_id,
    )


def link_event_data_to_checkout_intent(event, checkout_intent):
    """
    Set the StripeEventData record for the given event to point at the provided CheckoutIntent.
    """
    event_data = StripeEventData.objects.get(event_id=event.id)
    if not event_data.checkout_intent:
        event_data.checkout_intent = checkout_intent
        event_data.save()  # this triggers a post_save signal that updates the related summary record


def cancel_all_future_plans(checkout_intent):
    """
    Deactivate (cancel) all future renewal plans descending from the
    anchor plan for this enterprise.
    """
    unprocessed_renewals = checkout_intent.renewals.filter(processed_at__isnull=True)
    if not unprocessed_renewals.exists():
        logger.warning('No renewals to cancel for Checkout Intent %s', checkout_intent.uuid)
        return []

    client = LicenseManagerApiClient()
    deactivated: list[UUID] = []

    for renewal in unprocessed_renewals:
        client.update_subscription_plan(
            str(renewal.renewed_subscription_plan_uuid),
            is_active=False,
        )
        deactivated_plan_uuid = renewal.renewed_subscription_plan_uuid
        deactivated.append(deactivated_plan_uuid)
        logger.info('Future plan %s de-activated for Checkout Intent %s', deactivated_plan_uuid, checkout_intent.uuid)

    return deactivated


class StripeEventHandler:
    """
    Container for Stripe event handler logic.
    """
    @classmethod
    def dispatch(cls, event: stripe.Event) -> None:
        if event.type not in _handlers_by_type:
            logger.warning('No stripe event handler configured for event type %s', event.type)
            return
        _handlers_by_type[event.type](event)

    @staticmethod
    def on_stripe_event(event_type: StripeEventType):
        """
        Decorator to register a function as an event handler.
        """
        def decorator(handler_method: Callable[[stripe.Event], None]):

            # Wrap the handler to add helpful logging.
            @wraps(handler_method)
            def wrapper(event: stripe.Event) -> None:
                # The default __repr__ is really long because it just barfs out the entire payload.
                event_short_repr = f'<stripe.Event id={event.id} type={event.type}>'
                logger.info(f'[StripeEventHandler] handling {event_short_repr}.')
                event_record = persist_stripe_event(event)
                handler_method(event)
                # Mark event as handled if we persisted it successfully and no exception was raised
                if event_record is not None:
                    event_record.refresh_from_db()
                    event_record.mark_as_handled()
                logger.info(f'[StripeEventHandler] handler for {event_short_repr} complete.')

            # Register the wrapped handler method.
            _handlers_by_type[event_type] = wrapper

            return wrapper
        return decorator

    ##################
    # BEGIN HANDLERS #
    ##################

    @on_stripe_event('invoice.paid')
    @staticmethod
    def invoice_paid(event: stripe.Event) -> None:
        """
        Handle invoice.paid events.
        """
        invoice, subscription_details = get_invoice_and_subscription(event)
        stripe_customer_id = invoice['customer']

        checkout_intent_id = get_checkout_intent_id_from_subscription(subscription_details)
        try:
            checkout_intent = get_checkout_intent_or_raise(checkout_intent_id, event.id)
        except CheckoutIntent.DoesNotExist:
            logger.error(
                '[StripeEventHandler] invoice.paid event %s could not find Checkout Intent id=%s to mark as paid',
                event.id, checkout_intent_id,
            )
            return

        try:
            checkout_intent.mark_as_paid(stripe_customer_id=stripe_customer_id)
            logger.info(
                'Marked checkout_intent_id=%s as paid via invoice=%s',
                checkout_intent_id, invoice.id,
            )
        except ValueError as exc:
            logger.warning(
                'Could not mark checkout intent % as paid via invoice %s, because %s',
                checkout_intent_id, invoice.id, exc,
            )
            if settings.STRIPE_GRACEFUL_EXCEPTION_MODE:
                return
            raise

        link_event_data_to_checkout_intent(event, checkout_intent)

        send_payment_receipt_email.delay(
            invoice_data=invoice,
            subscription_data=subscription_details,
            enterprise_customer_name=checkout_intent.enterprise_name,
            enterprise_slug=checkout_intent.enterprise_slug,
        )

    @on_stripe_event('customer.subscription.trial_will_end')
    @staticmethod
    def trial_will_end(event: stripe.Event) -> None:
        """
        Handle customer.subscription.trial_will_end events.
        Send reminder email 72 hours before trial ends.
        """
        subscription = event.data.object
        checkout_intent_id = get_checkout_intent_id_from_subscription(
            subscription
        )
        try:
            checkout_intent = get_checkout_intent_or_raise(
                checkout_intent_id, event.id
            )
        except CheckoutIntent.DoesNotExist:
            logger.error(
                "[StripeEventHandler] trial_will_end event %s could not find CheckoutIntent id=%s",
                event.id,
                checkout_intent_id,
            )
            return

        link_event_data_to_checkout_intent(event, checkout_intent)

        logger.info(
            (
                "Subscription %s trial ending in 72 hours. "
                "Queuing trial ending reminder email for checkout_intent_id=%s"
            ),
            subscription.id,
            checkout_intent_id,
        )

        # Queue the trial ending reminder email task
        send_trial_ending_reminder_email_task.delay(
            checkout_intent_id=checkout_intent.id,
        )

    @on_stripe_event('payment_method.attached')
    @staticmethod
    def payment_method_attached(event: stripe.Event) -> None:
        pass

    @on_stripe_event('customer.subscription.created')
    @staticmethod
    def subscription_created(event: stripe.Event) -> None:
        """
        Handle customer.subscription.created events.
        Enable pending updates to prevent license count drift on failed payments.
        """
        subscription = event.data.object

        checkout_intent_id = get_checkout_intent_id_from_subscription(
            subscription
        )
        checkout_intent = get_checkout_intent_or_raise(
            checkout_intent_id, event.id
        )
        link_event_data_to_checkout_intent(event, checkout_intent)

        try:
            # Update the subscription to enable pending updates for future modifications
            # This ensures that quantity changes through the billing portal will only
            # be applied if payment succeeds, preventing license count drift
            logger.info(f'Enabling pending updates for created subscription {subscription.id}')
            stripe.Subscription.modify(
                subscription.id,
                payment_behavior='pending_if_incomplete',
            )

            logger.info('Successfully enabled pending updates for subscription %s', subscription.id)
        except stripe.StripeError as e:
            logger.error('Failed to enable pending updates for subscription %s: %s', subscription.id, e)

        summary = StripeEventSummary.objects.get(event_id=event.id)
        try:
            summary.update_upcoming_invoice_amount_due()
        except stripe.StripeError as exc:
            logger.warning('Error updating upcoming invoice amount due: %s', exc)
            if not settings.STRIPE_GRACEFUL_EXCEPTION_MODE:
                raise

    @on_stripe_event('customer.subscription.updated')
    @staticmethod
    def subscription_updated(event: stripe.Event) -> None:
        """
        Handle customer.subscription.updated events.
        Track when subscriptions have pending updates and update related CheckoutIntent state.
        Send cancellation notification email when a trial subscription is canceled.
        """
        subscription = event.data.object
        checkout_intent_id = get_checkout_intent_id_from_subscription(subscription)
        checkout_intent = get_checkout_intent_or_raise(checkout_intent_id, event.id)
        link_event_data_to_checkout_intent(event, checkout_intent)

        # Pending update
        pending_update = getattr(subscription, "pending_update", None)
        if pending_update:
            handle_pending_update(subscription.id, checkout_intent_id, pending_update)

        current_status = subscription.get("status")
        prior_status = getattr(checkout_intent.previous_summary(event), 'subscription_status', None)

        # Handle trial-to-paid transition for renewal processing
        if prior_status == "trialing" and current_status == "active":
            logger.info(
                f"Subscription {subscription.id} transitioned from trial to active. "
                f"Processing renewal for checkout_intent_id={checkout_intent_id}"
            )
            _process_trial_to_paid_renewal(checkout_intent, subscription.id, event)
            send_trial_end_and_subscription_started_email_task.delay(
                subscription_id=subscription.id,
                checkout_intent_id=checkout_intent.id,
            )

        # Trial cancellation transition
        if current_status == "canceled" and prior_status != "canceled":
            logger.info(
                f"Subscription {subscription.id} status changed from '{prior_status}' to 'canceled'. "
            )
            trial_end = subscription.get("trial_end")
            if trial_end:
                logger.info(f"Queuing trial cancellation email for checkout_intent_id={checkout_intent_id}")
                send_trial_cancellation_email_task.delay(
                    checkout_intent_id=checkout_intent.id,
                    trial_end_timestamp=trial_end,
                )
            else:
                logger.info(
                    f"Subscription {subscription.id} canceled but has no trial_end, skipping cancellation email"
                )

        # Past due transition
        if current_status == "past_due" and prior_status != "past_due":
            logger.warning(
                'Stripe subscription %s was %s but is now past_due. '
                'Checkout intent: %s',
                subscription.id, prior_status, checkout_intent.id,
            )
            enterprise_uuid = checkout_intent.enterprise_uuid
            if enterprise_uuid:
                cancel_all_future_plans(checkout_intent)
            else:
                logger.error(
                    (
                        "Cannot deactivate future plans for subscription %s: "
                        "missing enterprise_uuid on CheckoutIntent %s"
                    ),
                    subscription.id,
                    checkout_intent.id,
                )
            send_billing_error_email_task.delay(checkout_intent_id=checkout_intent.id)

    @on_stripe_event("customer.subscription.deleted")
    @staticmethod
    def subscription_deleted(event: stripe.Event) -> None:
        """
        Handle customer.subscription.deleted events.
        """


def _process_trial_to_paid_renewal(checkout_intent: CheckoutIntent, stripe_subscription_id: str, event: stripe.Event):
    """
    Process the trial-to-paid renewal for a subscription.

    This function:
    1. Finds the existing SelfServiceSubscriptionRenewal record
    2. Updates it with the Stripe event data and subscription ID
    3. Calls license manager to process the renewal
    4. Marks the renewal as processed

    Args:
        checkout_intent: The CheckoutIntent associated with the subscription
        stripe_subscription_id: The Stripe subscription ID
        event: The Stripe event that triggered the renewal
    """
    try:
        # Find the SelfServiceSubscriptionRenewal record for this checkout intent
        renewal = SelfServiceSubscriptionRenewal.objects.filter(
            checkout_intent=checkout_intent,
            processed_at__isnull=True  # Only unprocessed renewals
        ).first()

        if not renewal:
            logger.error(
                f"No unprocessed SelfServiceSubscriptionRenewal found for checkout_intent {checkout_intent.id}"
            )
            return

        # Get the StripeEventData record for this event
        event_data = StripeEventData.objects.get(event_id=event.id)

        # Update the renewal record with event data and subscription ID
        renewal.stripe_event_data = event_data
        renewal.stripe_subscription_id = stripe_subscription_id
        renewal.save(update_fields=['stripe_event_data', 'stripe_subscription_id', 'modified'])

        logger.info(
            f"Updated SelfServiceSubscriptionRenewal {renewal.id} with event data {event_data.event_id} "
            f"and subscription {stripe_subscription_id}"
        )

        # Process the renewal via license manager
        license_manager_client = LicenseManagerApiClient()
        result = license_manager_client.process_subscription_plan_renewal(renewal.subscription_plan_renewal_id)

        logger.info(
            f"Successfully processed subscription plan renewal {renewal.subscription_plan_renewal_id} "
            f"via license manager. Result: {result}"
        )

        # Mark the renewal as processed
        renewal.mark_as_processed()

        logger.info(
            f"Marked SelfServiceSubscriptionRenewal {renewal.id} as processed for "
            f"subscription {stripe_subscription_id}"
        )

    except Exception as exc:
        logger.exception(
            f"Failed to process trial-to-paid renewal for checkout_intent {checkout_intent.id}, "
            f"subscription {stripe_subscription_id}: {exc}"
        )
        raise
