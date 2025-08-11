"""
Python API for interacting with Stripe (aside from functions contained in ``pricing_api.py``).
"""
import stripe
from django.conf import settings

stripe.api_key = settings.STRIPE_API_KEY


def create_subscription_checkout_session(input_data, lms_user_id) -> stripe.checkout.Session:
    """
    Creates a free trial subscription checkout session.
    """
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
                # Communicate to downstream services what the admin intends the enterprise customer slug to be.
                'enterprise_customer_slug': input_data['enterprise_slug'],
                # Store the lms_user_id for improved debugging experience.
                'lms_user_id': str(lms_user_id),
            }
        },
        # Always collect payment method, not just when the amount is greater than zero.  This is influential for
        # creating a free trial plan because the amount is always zero.
        'payment_method_collection': 'always',

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

    return stripe.checkout.Session.create(**create_kwargs)
