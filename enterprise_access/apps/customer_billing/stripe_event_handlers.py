"""
Stripe event handlers
"""
import logging
from collections.abc import Callable
from functools import wraps

import stripe

from enterprise_access.apps.customer_billing.models import CheckoutIntent
from enterprise_access.apps.customer_billing.stripe_api import get_stripe_subscription
from enterprise_access.apps.customer_billing.stripe_event_types import StripeEventType

logger = logging.getLogger(__name__)


# Central registry for event handlers.
#
# Needs to be in module scope instead of class scope because the decorator
# didn't have access to the class name soon enough during runtime initialization.
_handlers_by_type: dict[StripeEventType, Callable[[stripe.Event], None]] = {}


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
        invoice = event.data.object

        # Extract the checkout_intent ID from the related subscription.
        subscription_id = invoice['subscription']
        subscription = get_stripe_subscription(subscription_id)
        checkout_intent_id = int(subscription.metadata['checkout_intent_id'])

        logger.info(
            f'Found checkout_intent_id="{checkout_intent_id}" '
            f'stored on the Subscription <subscription_id="{subscription_id}"> '
            f'related to Invoice <invoice_id="{invoice["id"]}">.'
        )

        checkout_intent = CheckoutIntent.objects.get(id=checkout_intent_id)
        logger.info(
            'Found existing CheckoutIntent record with '
            f'id={checkout_intent_id}, '
            f'stripe_checkout_session_id={checkout_intent.stripe_checkout_session_id}, '
            f'state={checkout_intent.state}.  '
            'Marking intent as paid...'
        )
        checkout_intent.mark_as_paid()

    @on_stripe_event('customer.subscription.trial_will_end')
    @staticmethod
    def trial_will_end(event: stripe.Event) -> None:
        pass

    @on_stripe_event('payment_method.attached')
    @staticmethod
    def payment_method_attached(event: stripe.Event) -> None:
        pass

    @on_stripe_event('customer.subscription.deleted')
    @staticmethod
    def subscription_deleted(event: stripe.Event) -> None:
        pass
