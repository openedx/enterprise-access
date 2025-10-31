"""
Python API for interacting with Stripe (aside from functions contained in ``pricing_api.py``).
"""
import logging
from functools import wraps
from typing import Optional

import stripe
from django.conf import settings
from edx_django_utils.cache import TieredCache

logger = logging.getLogger(__name__)

## This is where the Stripe API key is set on the client.
## Don't remove.
stripe.api_key = settings.STRIPE_API_KEY


def create_subscription_checkout_session(input_data, lms_user_id, checkout_intent) -> stripe.checkout.Session:
    """
    Creates a free trial subscription checkout session.
    """
    stripe.api_key = settings.STRIPE_API_KEY
    create_kwargs: stripe.checkout.Session.CreateParams = {
        'mode': 'subscription',
        # Intended UI will be a custom react component.
        'ui_mode': 'custom',
        # Specify the type and quantity of what is being purchased.  Units for `quantity` depends on
        # the price specified, and the product associated with the price.
        'line_items': [{
            'price': input_data['stripe_price_id'],
            'quantity': input_data['quantity'],
        }],
        # Defer payment collection until the last moment, then cancel
        # the subscription if payment info has not been submitted.
        'subscription_data': {
            'trial_period_days': settings.SSP_TRIAL_PERIOD_DAYS,
            'trial_settings': {
                # Just in case the admin removes the payment method via their stripe billing portal, cancel the trial
                # after it ends.
                'end_behavior': {'missing_payment_method': 'cancel'},
            },
            'metadata': {
                # Downstream services need to know the intended enterprise customer name & slug.
                'enterprise_customer_name': input_data['company_name'],
                'enterprise_customer_slug': input_data['enterprise_slug'],
                # Store the lms_user_id for improved debugging experience.
                'lms_user_id': str(lms_user_id),
                # Store the checkout_intent ID for cross-service reference
                'checkout_intent_id': str(checkout_intent.id),
                'checkout_intent_uuid': str(checkout_intent.uuid),
            }
        },
        # Always collect payment method, not just when the amount is greater than zero.  This is influential for
        # creating a free trial plan because the amount is always zero.
        'payment_method_collection': 'always',
        # This restricts the output for the payment element to only show the card option
        'payment_method_types': [
            'card'
        ]

        # `return_url` is not required because we won't use any "redirect-based" payment methods,
        # including: iDEAL, Bancontact, SOFORT, Apple Pay, Google Pay, etc. We only support the
        # `card` payment method which is not redirect-based.
    }
    # Eagerly find an existing Stripe customer if one already exists with the same email, otherwise excluding it from
    # the request will cause Stripe to generate a new one.
    #
    # NOTE: The Stripe "customer" is more closely representative of the human admin than the "enterprise customer".
    stripe_customer_search_result = stripe.Customer.search(query=f"email: '{input_data['admin_email']}'")
    found_stripe_customer_by_email = (
        stripe_customer_search_result.data[0] if stripe_customer_search_result.data else None
    )
    if found_stripe_customer_by_email:
        create_kwargs['customer'] = found_stripe_customer_by_email['id']
    else:
        create_kwargs['customer_email'] = input_data['admin_email']

    return stripe.checkout.Session.create(**create_kwargs)


def stripe_cache(timeout=settings.DEFAULT_STRIPE_CACHE_TIMEOUT):
    """
    Decorator for caching Stripe API responses.

    Args:
        timeout (int): Cache timeout in seconds, defaults to 60

    Returns:
        function: Decorated function with caching
    """
    def decorator(func):
        @wraps(func)
        def wrapper(resource_id, *args, **kwargs):
            # Create cache key based on function name and resource ID
            func_name = func.__name__
            cache_key = f"stripe_{func_name}_{resource_id}"

            # Try to get from cache first
            cached_response = TieredCache.get_cached_response(cache_key)
            if cached_response.is_found:
                logger.info(f'Cache hit for Stripe {func_name} {resource_id}')
                return cached_response.value

            # If not in cache, call the original function
            result = func(resource_id, *args, **kwargs)

            # Cache the result
            if result:
                TieredCache.set_all_tiers(
                    cache_key,
                    result,
                    django_cache_timeout=timeout,
                )
                logger.info(f'Cached Stripe {func_name} data for {resource_id}')

            return result
        return wrapper
    return decorator


@stripe_cache()
def get_stripe_checkout_session(session_id) -> stripe.checkout.Session:
    """
    Retrieve a Stripe Checkout Session.

    Args:
        session_id (str): The Stripe Checkout Session ID

    Returns:
        dict: The Stripe Checkout Session object

    Docs: https://stripe.com/docs/api/checkout/sessions/retrieve
    """
    return stripe.checkout.Session.retrieve(session_id)


@stripe_cache()
def get_stripe_payment_intent(payment_intent_id) -> stripe.PaymentIntent:
    """
    Retrieve a Stripe Payment Intent.

    Args:
        payment_intent_id (str): The Stripe Payment Intent ID

    Returns:
        dict: The Stripe Payment Intent object

    Docs: https://stripe.com/docs/api/payment_intents/retrieve
    """
    return stripe.PaymentIntent.retrieve(payment_intent_id)


@stripe_cache()
def get_stripe_invoice(invoice_id) -> stripe.Invoice:
    """
    Retrieve a Stripe Invoice.

    Args:
        invoice_id (str): The Stripe Invoice ID

    Returns:
        dict: The Stripe Invoice object

    Docs: https://stripe.com/docs/api/invoices/retrieve
    """
    return stripe.Invoice.retrieve(invoice_id)


@stripe_cache()
def get_stripe_payment_method(payment_method_id) -> stripe.PaymentMethod:
    """
    Retrieve a Stripe Payment Method.

    Args:
        payment_method_id (str): The Stripe Payment Method ID

    Returns:
        dict: The Stripe Payment Method object

    Docs: https://stripe.com/docs/api/payment_methods/retrieve
    """
    return stripe.PaymentMethod.retrieve(payment_method_id)


@stripe_cache()
def get_stripe_customer(customer_id) -> stripe.Customer:
    """
    Retrieve a Stripe Customer.

    Args:
        customer_id (str): The Stripe Customer ID

    Returns:
        dict: The Stripe Customer object

    Docs: https://stripe.com/docs/api/customers/retrieve
    """
    return stripe.Customer.retrieve(customer_id)


@stripe_cache()
def get_stripe_subscription(subscription_id) -> stripe.Subscription:
    """
    Retrieve a Stripe Subscription.

    Args:
        subscription_id (str): The Stripe Subscription ID

    Returns:
        dict: The Stripe Subscription object

    Docs: https://stripe.com/docs/api/subscriptions/retrieve
    """
    return stripe.Subscription.retrieve(subscription_id)


@stripe_cache()
def get_stripe_trialing_subscription(
        stripe_customer_id: str, status: str = 'trialing'
) -> Optional[stripe.Subscription]:
    """
    Retrieve the most recent subscription with given status for a Stripe customer.

    Args:
        stripe_customer_id (str): The Stripe Customer ID to search subscriptions for.
        status (str): The subscription status to filter by, defaults to 'trialing'.
                     See https://stripe.com/docs/api/subscriptions/list#list_subscriptions-status
                     for possible values.

    Returns:
        Optional[stripe.Subscription]: The most recent subscription matching the criteria,
                                     or None if no matching subscription is found.

    Docs: https://stripe.com/docs/api/subscriptions/list
    """
    subscription_list = stripe.Subscription.list(
        customer=stripe_customer_id,
        status=status,
        limit=1,
    )
    return subscription_list.data[0] if subscription_list.data else None


@stripe_cache()
def upcoming_invoice(stripe_customer_id, stripe_subscription_id):
    """
    https://docs.stripe.com/changelog/basil/2025-03-31/invoice-preview-api-deprecations
    https://docs.stripe.com/api/invoices/create_preview?architecture-style=resources
    """
    return stripe.Invoice.create_preview(
        customer=stripe_customer_id,
        subscription=stripe_subscription_id,
    )
