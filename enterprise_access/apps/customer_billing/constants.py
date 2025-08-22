"""
Constants for customer_billing app.
"""
from enum import StrEnum

CHECKOUT_SESSION_ERROR_CODES = {
    'common': {
        'INCOMPLETE_DATA': ('incomplete_data', 'Not enough parameters were given.'),
    },
    'admin_email': {
        'INVALID_FORMAT': ('invalid_format', 'Invalid format for given email address.'),
        'NOT_REGISTERED': ('not_registered', 'Given email address does not correspond to an existing user.'),
    },
    'user': {
        'IS_NULL': ('is_null', 'The user object cannot be null'),
        'ADMIN_EMAIL_MISMATCH': (
            'admin_email_mismatch',
            'The provided admin_email differs from an existing user making the request.'
        ),
    },
    'full_name': {
        'IS_NULL': ('is_null', 'The user object cannot be null'),
    },
    'company_name': {
        'IS_NULL': ('is_null', 'Company name cannot be empty.'),
        'EXISTING_ENTERPRISE_CUSTOMER': (
            'existing_enterprise_customer',
            'An enterprise customer with this name already exists.'
        ),
    },
    'enterprise_slug': {
        'INVALID_FORMAT': ('invalid_format', 'Invalid format for given slug.'),
        'EXISTING_ENTERPRISE_CUSTOMER': (
            'existing_enterprise_customer',
            'The slug conflicts with an existing customer.',
        ),
        'EXISTING_ENTERPRISE_CUSTOMER_FOR_ADMIN': (
            # NOTE: Use the same error code as above for now. Until we figure out better
            # security, this prevents exposure of admin<->customer relationships.
            'existing_enterprise_customer',
            'The slug conflicts with an existing customer.',
        ),
        'SLUG_RESERVED': (
            'slug_reserved',
            'The slug is currently reserved by another user.',
        ),
    },
    'quantity': {
        'INVALID_FORMAT': ('invalid_format', 'Must be a positive integer.'),
        'RANGE_EXCEEDED': ('range_exceeded', 'Exceeded allowed range for given stripe_price_id.'),
    },
    'stripe_price_id': {
        'INVALID_FORMAT': ('invalid_format', 'Must be a non-empty string.'),
        'DOES_NOT_EXIST': ('does_not_exist', 'This stripe_price_id has not been configured.'),
    },
}

# According to stripe's AI assistant: "When a Checkout Session is created,
# Stripe automatically sets the expires_at timestamp to 24 hours in the future."
# We want the slug duration to last at least as long as the checkout session expiry.
SLUG_RESERVATION_DURATION_MINUTES = 24 * 60
INTENT_RESERVATION_DURATION_MINUTES = 24 * 60


class CheckoutIntentState(StrEnum):
    """
    Namespace for CheckoutIntent state values
    """
    CREATED = 'created'
    PAID = 'paid'
    FULFILLED = 'fulfilled'
    ERRORED_STRIPE_CHECKOUT = 'errored_stripe_checkout'
    ERRORED_PROVISIONING = 'errored_provisioning'
    EXPIRED = 'expired'


ALLOWED_CHECKOUT_INTENT_STATE_TRANSITIONS = {
    CheckoutIntentState.CREATED: [
        CheckoutIntentState.PAID,
        CheckoutIntentState.ERRORED_STRIPE_CHECKOUT,
        CheckoutIntentState.EXPIRED,
    ],
    CheckoutIntentState.PAID: [
        CheckoutIntentState.FULFILLED,
        CheckoutIntentState.ERRORED_PROVISIONING,
    ],
    CheckoutIntentState.ERRORED_STRIPE_CHECKOUT: [
        CheckoutIntentState.PAID,
    ],
    CheckoutIntentState.ERRORED_PROVISIONING: [
        CheckoutIntentState.FULFILLED,
    ],
    CheckoutIntentState.EXPIRED: [
        CheckoutIntentState.CREATED,
    ],
    CheckoutIntentState.FULFILLED: [],
}
