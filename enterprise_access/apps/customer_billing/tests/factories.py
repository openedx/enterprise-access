"""
Test factories for customer_billing app.
"""
from datetime import timedelta
from uuid import uuid4

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory
from faker import Faker

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.constants import INVOICE_PAID_PARENT_TYPE_IDENTIFIER, CheckoutIntentState
from enterprise_access.apps.customer_billing.models import (
    CheckoutIntent,
    SelfServiceSubscriptionRenewal,
    StripeEventData,
    StripeEventSummary
)

FAKER = Faker()


def get_stripe_object_for_event_type(event_type, **overrides):
    """Generate realistic Stripe object data based on event type."""
    if event_type == 'invoice.paid':
        return {
            'object': 'invoice',
            'id': f'in_{FAKER.bothify("?" * 24)}',
            'subscription': f'sub_{FAKER.bothify("?" * 24)}',
            'amount_paid': FAKER.random_int(min=1000, max=10000),  # Amount in cents
            'currency': 'usd',
            'lines': {
                'data': [
                    {
                        'quantity': FAKER.random_int(min=1, max=100),
                        'pricing': {
                            'unit_amount': FAKER.random_int(min=100, max=5000),
                            'unit_amount_decimal': f'{FAKER.random_int(min=100, max=5000)}.0'
                        },
                        'parent': {
                            'type': INVOICE_PAID_PARENT_TYPE_IDENTIFIER
                        }
                    }
                ]
            },
            'parent': {
                'subscription_details': {
                    'subscription': f'sub_{FAKER.bothify("?" * 24)}'
                }
            },
            **overrides,
        }
    elif event_type.startswith('customer.subscription'):
        return {
            'object': 'subscription',
            'id': f'sub_{FAKER.bothify("?" * 24)}',
            'status': FAKER.random_element(elements=['active', 'trialing', 'past_due']),
            'currency': 'usd',
            'items': {
                'data': [
                    {
                        'current_period_start': int((timezone.now() - timedelta(days=30)).timestamp()),
                        'current_period_end': int((timezone.now() + timedelta(days=30)).timestamp())
                    }
                ]
            },
            **overrides,
        }
    else:
        # Default generic object for other event types
        return {
            'object': 'unknown',
            'id': f'obj_{FAKER.bothify("?" * 24)}',
            **overrides,
        }


class CheckoutIntentFactory(DjangoModelFactory):
    """
    Factory for creating CheckoutIntent instances for testing.
    """
    class Meta:
        model = CheckoutIntent

    user = factory.SubFactory(UserFactory)
    uuid = factory.LazyFunction(uuid4)
    state = CheckoutIntentState.CREATED
    enterprise_name = factory.Faker('company')
    enterprise_slug = factory.LazyAttribute(
        lambda obj: obj.enterprise_name.lower().replace(' ', '-').replace(',', '').replace('.', '')
    )
    enterprise_uuid = factory.LazyFunction(uuid4)
    # bothify generates a string where each placeholder '?' is replaced with a random ascii letter.
    # other placeholder values are allowed, see method help text
    stripe_customer_id = factory.LazyAttribute(lambda x: f'cus_{FAKER.bothify("?" * 17)}_00')
    expires_at = factory.LazyAttribute(lambda x: timezone.now() + timedelta(hours=1))
    stripe_checkout_session_id = factory.LazyAttribute(
        lambda x: f'cs_test_{FAKER.bothify("?" * 36)}'
    )
    quantity = factory.Faker('random_int', min=1, max=100)
    country = 'US'
    last_checkout_error = None
    last_provisioning_error = None
    workflow = None
    terms_metadata = factory.LazyFunction(
        lambda: {
            'accepted_at': timezone.now().isoformat(),
            'version': '1.0'
        }
    )


class StripeEventDataFactory(DjangoModelFactory):
    """
    Factory for creating StripeEventData instances for testing.
    """
    class Meta:
        model = StripeEventData

    event_id = factory.LazyAttribute(lambda x: f'evt_{FAKER.bothify("?" * 24)}')
    event_type = factory.Faker('random_element', elements=[
        'invoice.paid',
        'customer.subscription.updated',
        'customer.subscription.created',
        'customer.subscription.trial_will_end',
        'customer.subscription.deleted'
    ])
    checkout_intent = factory.SubFactory(CheckoutIntentFactory)
    handled_at = None
    data = factory.LazyAttribute(lambda obj: {
        'id': obj.event_id,
        'type': obj.event_type,
        'created': int(timezone.now().timestamp()),
        'data': {
            'object': get_stripe_object_for_event_type(obj.event_type)
        }
    })


class StripeEventSummaryFactory(DjangoModelFactory):
    """
    Factory for creating StripeEventSummary instances for testing.
    """
    class Meta:
        model = StripeEventSummary
        django_get_or_create = ("event_id",)

    stripe_event_data = factory.SubFactory(StripeEventDataFactory)
    event_id = factory.LazyAttribute(lambda obj: obj.stripe_event_data.event_id)
    event_type = factory.LazyAttribute(lambda obj: obj.stripe_event_data.event_type)
    stripe_event_created_at = factory.LazyFunction(timezone.now)
    checkout_intent = factory.LazyAttribute(lambda obj: obj.stripe_event_data.checkout_intent)

    # Subscription plan UUIDs
    subscription_plan_uuid = factory.LazyFunction(uuid4)
    future_subscription_plan_uuid = factory.LazyFunction(uuid4)
    subscription_plan_renewal_uuid = factory.LazyFunction(uuid4)

    # Stripe identifiers
    stripe_subscription_id = factory.LazyFunction(lambda: f'sub_{FAKER.bothify("?" * 24)}')


class SelfServiceSubscriptionRenewalFactory(DjangoModelFactory):
    """
    Factory for creating StripeEventSummary instances for testing.
    """
    class Meta:
        model = SelfServiceSubscriptionRenewal

    checkout_intent = factory.SubFactory(CheckoutIntentFactory)
    subscription_plan_renewal_id = factory.Faker('random_int', min=1, max=10000)
    stripe_event_data = factory.SubFactory(StripeEventDataFactory)
    stripe_subscription_id = factory.LazyFunction(lambda: f'sub_{FAKER.bothify("?" * 24)}')
