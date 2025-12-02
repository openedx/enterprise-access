"""
Stripe event handlers
"""
import logging
from collections.abc import Callable
from functools import wraps

import stripe

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
    except CheckoutIntent.DoesNotExist:
        logger.warning(
            'Could not find CheckoutIntent record with id %s for event %s',
            checkout_intent_id, event_id,
        )
        raise

    logger.info(
        'Found existing CheckoutIntent record with id=%s, state=%s, for event=%s',
        checkout_intent.id, checkout_intent.state, event_id,
    )
    return checkout_intent


def link_event_data_to_checkout_intent(event, checkout_intent):
    """
    Sets the StripeEventData record for the given event to point at the provided CheckoutIntent.
    """
    event_data = StripeEventData.objects.get(event_id=event.id)
    if not event_data.checkout_intent:
        event_data.checkout_intent = checkout_intent
        event_data.save()  # this triggers a post_save signal that updates the related summary record


def _get_subscription_plan_uuid_from_checkout_intent(checkout_intent: CheckoutIntent | None) -> str | None:
    """Return the anchor SubscriptionPlan UUID using the latest StripeEventSummary only."""
    if not checkout_intent:
        return None

    try:
        summary_with_uuid = (
            StripeEventSummary.objects
            .filter(checkout_intent=checkout_intent, subscription_plan_uuid__isnull=False)
            .order_by('-stripe_event_created_at')
            .first()
        )
        if summary_with_uuid and summary_with_uuid.subscription_plan_uuid:
            return str(summary_with_uuid.subscription_plan_uuid)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception(
            "Failed resolving subscription plan uuid from StripeEventSummary for CheckoutIntent %s: %s",
            checkout_intent.id,
            exc,
        )

    return None


def _get_current_plan_uuid(checkout_intent: CheckoutIntent | None) -> str | None:
    """Return the plan currently in effect for the checkout intent."""
    anchor_uuid = _get_subscription_plan_uuid_from_checkout_intent(checkout_intent)
    if not (checkout_intent and anchor_uuid):
        return None

    current_plan_uuid = anchor_uuid
    processed_renewals = (
        SelfServiceSubscriptionRenewal.objects
        .filter(
            checkout_intent=checkout_intent,
            processed_at__isnull=False,
            renewed_subscription_plan_uuid__isnull=False,
        )
        .order_by('processed_at', 'created')
    )

    for renewal in processed_renewals:
        current_plan_uuid = str(renewal.renewed_subscription_plan_uuid)

    return current_plan_uuid


def _get_future_plan_uuids(checkout_intent: CheckoutIntent | None, current_plan_uuid: str | None) -> list[str]:
    """Gather future plan UUIDs using pending renewal summaries."""
    if not (checkout_intent and current_plan_uuid):
        return []

    pending_summaries = (
        StripeEventSummary.objects.filter(
            checkout_intent=checkout_intent,
            stripe_event_data__renewal_processing__processed_at__isnull=True,
            stripe_event_data__renewal_processing__prior_subscription_plan_uuid__isnull=False,
            stripe_event_data__renewal_processing__renewed_subscription_plan_uuid__isnull=False,
        )
        .order_by('stripe_event_created_at', 'event_id')
        .select_related('stripe_event_data__renewal_processing')
    )
    parent_to_child: dict[str, str] = {}

    for summary in pending_summaries:
        renewal = summary.stripe_event_data.renewal_processing
        parent_uuid = str(renewal.prior_subscription_plan_uuid)
        child_uuid = str(renewal.renewed_subscription_plan_uuid)

        if parent_uuid == child_uuid:
            continue

        parent_to_child.setdefault(parent_uuid, child_uuid)

    future_plan_uuids: list[str] = []
    visited: set[str] = set()
    next_parent = str(current_plan_uuid)

    while next_parent in parent_to_child:
        child_uuid = parent_to_child[next_parent]
        if child_uuid in visited:
            break
        future_plan_uuids.append(child_uuid)
        visited.add(child_uuid)
        next_parent = child_uuid

    return future_plan_uuids


def cancel_all_future_plans(
    enterprise_uuid: str,
    reason: str = 'delayed_payment',
    subscription_id_for_logs: str | None = None,
    checkout_intent: CheckoutIntent | None = None,
) -> list[str]:
    """
    Deactivate all future renewal plans descending from the anchor plan for this enterprise.
    Returns list of deactivated descendant plan UUIDs (may be empty).
    """
    deactivated: list[str] = []
    try:
        current_plan_uuid = _get_current_plan_uuid(checkout_intent)
        if not current_plan_uuid:
            logger.warning(
                (
                    "Skipping future plan cancellation for enterprise %s (subscription %s): "
                    "unable to resolve the current SubscriptionPlan UUID from CheckoutIntent."
                ),
                enterprise_uuid,
                subscription_id_for_logs,
            )
            return deactivated

        future_plan_uuids = _get_future_plan_uuids(checkout_intent, current_plan_uuid)

        if not future_plan_uuids:
            return deactivated

        client = LicenseManagerApiClient()
        for future_uuid in future_plan_uuids:
            try:
                client.update_subscription_plan(
                    future_uuid,
                    is_active=False,
                    change_reason=reason,
                )
                deactivated.append(str(future_uuid))
                logger.info(
                    "Deactivated future plan %s for enterprise %s (reason=%s) (subscription %s)",
                    future_uuid,
                    enterprise_uuid,
                    reason,
                    subscription_id_for_logs,
                )
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception(
                    "Failed to deactivate future plan %s for enterprise %s (reason=%s): %s",
                    future_uuid,
                    enterprise_uuid,
                    reason,
                    exc,
                )
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception(
            "Unexpected error canceling future plans for enterprise %s (subscription %s): %s",
            enterprise_uuid,
            subscription_id_for_logs,
            exc,
        )

    return deactivated


def _handle_past_due_transition(
    subscription,
    checkout_intent: CheckoutIntent,
    prior_status: str | None,
) -> None:
    """Process the transition to past_due state."""
    current_status = subscription.get("status")
    if current_status != 'past_due' or prior_status == 'past_due':
        return

    try:
        send_billing_error_email_task.delay(checkout_intent_id=checkout_intent.id)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception(
            "Failed to enqueue billing error email for CheckoutIntent %s: %s",
            checkout_intent.id,
            exc,
        )

    enterprise_uuid = checkout_intent.enterprise_uuid
    if not enterprise_uuid:
        logger.error(
            "Cannot deactivate future plans for subscription %s: CheckoutIntent %s missing enterprise_uuid",
            subscription.get('id'),
            checkout_intent.id,
        )
        return

    cancel_all_future_plans(
        enterprise_uuid=str(enterprise_uuid),
        reason='delayed_payment',
        subscription_id_for_logs=subscription.get('id'),
        checkout_intent=checkout_intent,
    )


class StripeEventHandler:
    """
    Container for Stripe event handler logic.
    """
    @classmethod
    def dispatch(cls, event: stripe.Event) -> None:
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

        logger.info(
            'Marking checkout_intent_id=%s as paid via invoice=%s',
            checkout_intent_id, invoice.id,
        )
        checkout_intent.mark_as_paid(stripe_customer_id=stripe_customer_id)
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
            "Subscription %s trial ending in 72 hours. Queuing trial ending reminder email for checkout_intent_id=%s",
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

            logger.info(f'Successfully enabled pending updates for subscription {subscription.id}')
        except stripe.StripeError as e:
            logger.error(f'Failed to enable pending updates for subscription {subscription.id}: {e}')

        summary = StripeEventSummary.objects.get(event_id=event.id)
        summary.update_upcoming_invoice_amount_due()

    @on_stripe_event('customer.subscription.updated')
    @staticmethod
    def subscription_updated(event: stripe.Event) -> None:
        """
        Handle customer.subscription.updated events.
        Track when subscriptions have pending updates and update related CheckoutIntent state.
        Send cancellation notification email when a trial subscription is canceled.
        """
        subscription = event.data.object
        pending_update = getattr(subscription, "pending_update", None)

        checkout_intent_id = get_checkout_intent_id_from_subscription(
            subscription
        )
        checkout_intent = get_checkout_intent_or_raise(
            checkout_intent_id, event.id
        )
        link_event_data_to_checkout_intent(event, checkout_intent)

        if pending_update:
            # TODO: take necessary action on the actual SubscriptionPlan
            # and update the CheckoutIntent.
            logger.warning(
                "Subscription %s has pending update: %s. checkout_intent_id: %s",
                subscription.id,
                pending_update,
                get_checkout_intent_id_from_subscription(subscription),
            )

        # Handle trial-to-paid transition for renewal processing
        current_status = subscription.get("status")
        prior_status = getattr(checkout_intent.previous_summary(event), 'subscription_status', None)

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

        # Handle trial subscription cancellation
        # Check if status changed to canceled to avoid duplicate emails
        if current_status == "canceled":
            # Only send email if status changed from non-canceled to canceled
            if prior_status != 'canceled':
                trial_end = subscription.get("trial_end")
                if trial_end:
                    logger.info(
                        f"Subscription {subscription.id} status changed from '{prior_status}' to 'canceled'. "
                        f"Queuing trial cancellation email for checkout_intent_id={checkout_intent_id}"
                    )

                    send_trial_cancellation_email_task.delay(
                        checkout_intent_id=checkout_intent.id,
                        trial_end_timestamp=trial_end,
                    )
                else:
                    logger.info(
                        f"Subscription {subscription.id} canceled but has no trial_end, skipping cancellation email"
                    )
            else:
                logger.info(
                    f"Subscription {subscription.id} already canceled (status unchanged), skipping cancellation email"
                )

        _handle_past_due_transition(subscription, checkout_intent, prior_status)

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
