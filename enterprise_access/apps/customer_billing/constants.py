"""
Constants for customer_billing app.
"""

CHECKOUT_SESSION_ERROR_CODES = {
    'admin_email': {
        'INVALID_FORMAT': 'invalid_format',
        'NOT_REGISTERED': 'not_registered',
    },
    'enterprise_slug': {
        'EXISTING_ENTERPRISE_CUSTOMER': 'existing_enterprise_customer',
        'EXISTING_ENTERPRISE_CUSTOMER_FOR_ADMIN': 'existing_enterprise_customer_for_admin',
    },
}
