"""
Constants for customer_billing app.
"""

CHECKOUT_SESSION_ERROR_CODES = {
    'common': {
        'INCOMPLETE_DATA': ('incomplete_data', 'Not enough parameters were given.'),
    },
    'admin_email': {
        'INVALID_FORMAT': ('invalid_format', 'Invalid format for given email address.'),
        'NOT_REGISTERED': ('not_registered', 'Given email address does not correspond to an existing user.'),
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
