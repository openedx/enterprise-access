"""
Stripe event handlers
"""
import logging
from collections.abc import Callable
from functools import wraps

import stripe
from django.apps import apps

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


def handle_trial_cancellation(
    checkout_intent: CheckoutIntent,
    checkout_intent_id: int,
    subscription_id: str,
    trial_end
):
    """
    Send cancellation email for a trial subscription that has just transitioned to canceled.
    Assumes caller validated status transition and presence of trial_end.
    """
    logger.info(
        (
            "Subscription %s transitioned to 'canceled'. "
            "Sending cancellation email for checkout_intent_id=%s"
        ),
        subscription_id,
        checkout_intent_id,
    )

    send_trial_cancellation_email_task.delay(
        checkout_intent_id=checkout_intent.id,
        trial_end_timestamp=trial_end,
    )


def future_plans_of_current(current_plan_uuid: str, plans: list[dict]) -> list[dict]:
    """
    Return plans that are future renewals of the current plan,
    based on prior_renewals linkage.
    """
    def is_future_of_current(plan_dict):
        if str(plan_dict.get('uuid')) == current_plan_uuid:
            return False
        for renewal in plan_dict.get('prior_renewals', []) or []:
            if str(renewal.get('prior_subscription_plan_id')) == current_plan_uuid:
                return True
        return False

    return [p for p in plans if is_future_of_current(p)]


def _get_subscription_plan_uuid_from_checkout_intent(checkout_intent: CheckoutIntent | None) -> str | None:
    """
    Try to resolve the License Manager SubscriptionPlan UUID
    associated with the given CheckoutIntent.

    1) If the CheckoutIntent has a provisioning workflow,
       read the GetCreateSubscriptionPlanStep output uuid.
    2) Otherwise, look up the most recent StripeEventSummary for this
       CheckoutIntent that contains a subscription_plan_uuid and use that value.
    """
    if not checkout_intent:
        return None

    # 1) From provisioning workflow step output
    try:
        workflow = checkout_intent.workflow
        if workflow:
            subscription_step_model = apps.get_model('provisioning', 'GetCreateSubscriptionPlanStep')
            step = subscription_step_model.objects.filter(
                workflow_record_uuid=workflow.uuid,
            ).first()
            output_obj = getattr(step, 'output_object', None)
            if output_obj and getattr(output_obj, 'uuid', None):
                return str(output_obj.uuid)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception(
            "Failed resolving subscription plan uuid from workflow for CheckoutIntent %s: %s",
            checkout_intent.id, exc,
        )

    # 2) From StripeEventSummary records linked to this CheckoutIntent
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
            checkout_intent.id, exc,
        )

    return None


def _build_lineage_from_anchor(anchor_plan_uuid: str, plans: list[dict]) -> set[str]:
    """
    Return the anchor plan and all of its future renewals.
    """
    anchor = str(anchor_plan_uuid)

    # Index: parent_plan_uuid -> set(child_plan_uuid)
    children_index: dict[str, set[str]] = {}
    for plan in plans:
        child_uuid = str(plan.get('uuid'))
        for renewal in plan.get('prior_renewals', []) or []:
            parent_uuid = str(renewal.get('prior_subscription_plan_id'))
            if parent_uuid:
                children_index.setdefault(parent_uuid, set()).add(child_uuid)

    # BFS/DFS from anchor through children links
    lineage: set[str] = {anchor}
    stack = [anchor]
    while stack:
        parent = stack.pop()
        for child in children_index.get(parent, ()):  # empty tuple default avoids branch
            if child not in lineage:
                lineage.add(child)
                stack.append(child)

    return lineage


def cancel_all_future_plans(
    enterprise_uuid: str,
    reason: str = 'delayed_payment',
    subscription_id_for_logs: str | None = None,
    checkout_intent: CheckoutIntent | None = None,
) -> list[str]:
    """
    Deactivate (cancel) all future renewal plans descending from the
    anchor plan for this enterprise.

    Strict contract:
    - We REQUIRE an anchor plan uuid resolvable from the provided
      CheckoutIntent.
    - If no anchor can be resolved, we perform NO cancellations
      (safety: avoid wrong lineage).
    - Only descendants (children, grandchildren, etc.) of the anchor
      are canceled; the anchor/current plan is untouched.

    Returns list of deactivated descendant plan UUIDs (may be empty).
    """
    client = LicenseManagerApiClient()
    deactivated: list[str] = []
    try:
        anchor_uuid = _get_subscription_plan_uuid_from_checkout_intent(checkout_intent)
        if not anchor_uuid:
            logger.warning(
                (
                    "Skipping future plan cancellation for enterprise %s (subscription %s): "
                    "no anchor SubscriptionPlan UUID could be resolved from CheckoutIntent."
                ),
                enterprise_uuid,
                subscription_id_for_logs,
            )
            return deactivated

        all_list = client.list_subscriptions(enterprise_uuid)
        all_plans = (all_list or {}).get('results', [])

        lineage_set = _build_lineage_from_anchor(str(anchor_uuid), all_plans)
        lineage_plans = [p for p in all_plans if str(p.get('uuid')) in lineage_set]

        logger.debug(
            (
                "[cancel_all_future_plans] anchor=%s enterprise=%s subscription=%s "
                "total_plans=%d lineage_size=%d lineage=%s"
            ),
            anchor_uuid,
            enterprise_uuid,
            subscription_id_for_logs,
            len(all_plans),
            len(lineage_set),
            list(lineage_set),
        )

        current_plan = next((p for p in lineage_plans if p.get('is_current')), None)
        if not current_plan:
            logger.warning(
                (
                    "No current subscription plan found within lineage for enterprise %s "
                    "when canceling future plans (subscription %s)"
                ),
                enterprise_uuid,
                subscription_id_for_logs,
            )
            return deactivated

        current_plan_uuid = str(current_plan.get('uuid'))
        future_plan_uuids = [str(uuid) for uuid in lineage_set if str(uuid) != current_plan_uuid]

        logger.debug(
            "[cancel_all_future_plans] current_plan=%s future_plan_uuids=%s",
            current_plan_uuid,
            future_plan_uuids,
        )

        if not future_plan_uuids:
            logger.info(
                (
                    "No future plans (descendants) to deactivate for enterprise %s (current plan %s) "
                    "(subscription %s)"
                ),
                enterprise_uuid,
                current_plan_uuid,
                subscription_id_for_logs,
            )
            return deactivated

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
                    future_uuid, enterprise_uuid, reason, subscription_id_for_logs,
                )
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception(
                    "Failed to deactivate future plan %s for enterprise %s (reason=%s): %s",
                    future_uuid, enterprise_uuid, reason, exc,
                )
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception(
            "Unexpected error canceling future plans for enterprise %s (subscription %s): %s",
            enterprise_uuid,
            subscription_id_for_logs,
            exc,
        )

    return deactivated


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
            checkout_intent_id,
            invoice.id,
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
                handle_trial_cancellation(checkout_intent, checkout_intent_id, subscription.id, trial_end)
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
            # Fire billing error email to enterprise admins
            try:
                send_billing_error_email_task.delay(checkout_intent_id=checkout_intent.id)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.exception(
                    "Failed to enqueue billing error email for CheckoutIntent %s: %s",
                    checkout_intent.id,
                    str(exc),
                )

            enterprise_uuid = checkout_intent.enterprise_uuid
            if enterprise_uuid:
                cancel_all_future_plans(
                    enterprise_uuid=enterprise_uuid,
                    reason='delayed_payment',
                    subscription_id_for_logs=subscription.id,
                    checkout_intent=checkout_intent,
                )
            else:
                logger.error(
                    (
                        "Cannot deactivate future plans for subscription %s: "
                        "missing enterprise_uuid on CheckoutIntent %s"
                    ),
                    subscription.id,
                    checkout_intent.id,
                )

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
