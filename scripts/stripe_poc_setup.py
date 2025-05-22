"""
"""
import sys
import stripe
import pathlib
sys.path.append(str(pathlib.Path(__file__).parent.parent.resolve()))
from enterprise_access.settings.private import STRIPE_API_KEY

stripe.api_key = STRIPE_API_KEY

try:
    product_quarterly = stripe.Product.retrieve("prod_RnxwBMaYC6Dp4W")
except stripe.error.InvalidRequestError:
    product_quarterly = stripe.Product.create(
        id="prod_RnxwBMaYC6Dp4W",
        name="Subscription License Quarterly Plan",
        unit_label="licenses",
    )
print(product_quarterly)

try:
    product_yearly = stripe.Product.retrieve("prod_RnxwVyZDvZvkyi")
except stripe.error.InvalidRequestError:
    product_yearly = stripe.Product.create(
        id="prod_RnxwVyZDvZvkyi",
        name="Subscription License Yearly Plan",
        unit_label="licenses",
    )
print(product_yearly)

try:
    price_quarterly = stripe.Price.search(query="lookup_key:'price_quarterly_0002'")['data'][0]
except (stripe.error.InvalidRequestError, IndexError):
    price_quarterly = stripe.Price.create(
        lookup_key="price_quarterly_0002",
        currency="usd",
        unit_amount=33*100, # $33
        billing_scheme="per_unit",
        # Quarterly = Every 3 months.
        recurring={
            "interval": "month",
            "interval_count": 3,
        },
        product=product_quarterly,
    )
print(price_quarterly)

try:
    price_yearly = stripe.Price.search(query="lookup_key:'price_yearly_0001'")['data'][0]
except (stripe.error.InvalidRequestError, IndexError):
    price_yearly = stripe.Price.create(
        lookup_key="price_yearly_0001",
        currency="usd",
        unit_amount=30*100*12,  # Cheaper than the quarterly plan.
        billing_scheme="per_unit",
        recurring={"interval": "year"},
        product=product_yearly,
    )
print(price_yearly)

billing_portal_features={
    "customer_update": {
        "enabled": True,
        "allowed_updates": ["address", "tax_id"],
    },
    "payment_method_update": {"enabled": True},
    # Show all previous invoices.
    "invoice_history": {"enabled": True},
    # Allow self-service plan cancellation.
    "subscription_cancel": {
        "enabled": True,
        "cancellation_reason": {
           "enabled": True,
           "options": [
               "customer_service", # Customer service was less than expected
               "low_quality",  # Quality was less than expected
               "missing_features",  # Some features are missing
               "other",  # Other reason
               "switched_service",  # I’m switching to a different service
               "too_complex",  # Ease of use was less than expected
               "too_expensive",  # It’s too expensive
               "unused",  # I don’t use the service enough
            ],
        },
        # Do not cancel the subscription immediately, do it at the end of the
        # period that they already paid for.
        "mode": "at_period_end",
    },
    "subscription_update": {
        "enabled": True,
        "default_allowed_updates": ["price", "quantity"],
        "products": [
            {
                "product": product_quarterly['id'],
                "prices": [price_quarterly['id']],
            },
            {
                "product": product_yearly['id'],
                "prices": [price_yearly['id']],
            },
        ],
    },
}
try:
    billing_portal_config = stripe.billing_portal.Configuration.list(limit=1)['data'][0]
    stripe.billing_portal.Configuration.modify(
        billing_portal_config.id,
        features=billing_portal_features,
    )
except (stripe.error.InvalidRequestError, IndexError):
    billing_portal_config = stripe.billing_portal.Configuration.create(
        features=billing_portal_features,
    )
print(billing_portal_config)
