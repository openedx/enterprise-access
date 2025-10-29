"""
Stripe event handlers
"""
import logging
from collections.abc import Callable
from functools import wraps

import stripe

from enterprise_access.apps.customer_billing.models import CheckoutIntent, StripeEventData
from enterprise_access.apps.customer_billing.stripe_event_types import StripeEventType
from enterprise_access.apps.customer_billing.tasks import send_trial_cancellation_email_task

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
    event_data.checkout_intent = checkout_intent
    event_data.save()


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


def handle_trial_cancellation(checkout_intent: CheckoutIntent, checkout_intent_id: int, subscription_id: str, trial_end):
    """
    Send cancellation email for a trial subscription that has just transitioned to canceled.
    Assumes caller validated status transition and presence of trial_end.
    """
    logger.info(
        f"Subscription {subscription_id} transitioned to 'canceled'. "
        f"Queuing trial cancellation email for checkout_intent_id={checkout_intent_id}"
    )

    send_trial_cancellation_email_task.delay(
        checkout_intent_id=checkout_intent.id,
        trial_end_timestamp=trial_end,
    )


def future_plans_of_current(current_plan_uuid: str, plans: list[dict]) -> list[dict]:
    """
    Return plans that are future renewals of the current plan, based on prior_renewals linkage.
    """
    def is_future_of_current(plan_dict):
        if str(plan_dict.get('uuid')) == current_plan_uuid:
            return False
        for renewal in plan_dict.get('prior_renewals', []) or []:
            if str(renewal.get('prior_subscription_plan_id')) == current_plan_uuid:
                return True
        return False

    return [p for p in plans if is_future_of_current(p)]


def cancel_all_future_plans(enterprise_uuid: str, reason: str = 'delayed_payment', subscription_id_for_logs: str | None = None) -> list[str]:
    """
    Deactivate (cancel) all future plans for the current plan of the given enterprise.

    Returns list of deactivated plan UUIDs. Logs warnings/info for observability.
    """
    from enterprise_access.apps.api_client.license_manager_client import LicenseManagerApiClient

    client = LicenseManagerApiClient()
    deactivated = []
    try:
        current_list = client.list_subscriptions(enterprise_uuid, current=True)
        current_results = (current_list or {}).get('results', [])
        current_plan = current_results[0] if current_results else None
        if not current_plan:
            logger.warning(
                "No current subscription plan found for enterprise %s when canceling future plans (subscription %s)",
                enterprise_uuid, subscription_id_for_logs,
            )
            return deactivated

        current_plan_uuid = str(current_plan.get('uuid'))

        # Fetch all active plans for enterprise
        all_list = client.list_subscriptions(enterprise_uuid)
        all_plans = (all_list or {}).get('results', [])

        future_plans = future_plans_of_current(current_plan_uuid, all_plans)
        if not future_plans:
            logger.info(
                "No future plans to deactivate for enterprise %s (current plan %s) (subscription %s)",
                enterprise_uuid, current_plan_uuid, subscription_id_for_logs,
            )
            return deactivated

        # Deactivate all future plans
        for future in future_plans:
            future_uuid = future.get('uuid')
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
            except Exception as exc: # pylint: disable=broad-except
                logger.exception(
                    "Failed to deactivate future plan %s for enterprise %s (reason=%s): %s",
                    future_uuid, enterprise_uuid, reason, exc,
                )
    except Exception as exc: # pylint: disable=broad-except
        logger.exception(
            "Unexpected error canceling future plans for enterprise %s (subscription %s): %s",
            enterprise_uuid, subscription_id_for_logs, exc,
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
                persist_stripe_event(event)
                handler_method(event)
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

    @on_stripe_event('customer.subscription.trial_will_end')
    @staticmethod
    def trial_will_end(event: stripe.Event) -> None:
        pass

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

        logger.info(f'Enabling pending updates for created subscription {subscription.id}')

        try:
            # Update the subscription to enable pending updates for future modifications
            # This ensures that quantity changes through the billing portal will only
            # be applied if payment succeeds, preventing license count drift
            stripe.Subscription.modify(
                subscription.id,
                payment_behavior='pending_if_incomplete',
            )

            logger.info(f'Successfully enabled pending updates for subscription {subscription.id}')
        except stripe.StripeError as e:
            logger.error(f'Failed to enable pending updates for subscription {subscription.id}: {e}')

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

        # Trial cancellation transition
        current_status = subscription.get("status")
        prior_status = getattr(checkout_intent.previous_summary(event), 'subscription_status', None)
        if current_status == "canceled" and prior_status != "canceled":
            trial_end = subscription.get("trial_end")
            if trial_end:
                handle_trial_cancellation(checkout_intent, checkout_intent_id, subscription.id, trial_end)

        # Past due transition
        if current_status == "past_due" and prior_status != "past_due":
            enterprise_uuid = checkout_intent.enterprise_uuid
            if enterprise_uuid:
                cancel_all_future_plans(
                    enterprise_uuid=enterprise_uuid,
                    reason='delayed_payment',
                    subscription_id_for_logs=subscription.id,
                )
            else:
                logger.error(
                    "Cannot deactivate future plans for subscription %s: missing enterprise_uuid on CheckoutIntent %s",
                    subscription.id, checkout_intent.id,
                )



    @on_stripe_event("customer.subscription.deleted")
    @staticmethod
    def subscription_deleted(event: stripe.Event) -> None:
        """
        Handle customer.subscription.deleted events.
        """
