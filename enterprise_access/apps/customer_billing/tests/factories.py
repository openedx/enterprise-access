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
from enterprise_access.apps.customer_billing.constants import SUBSCRIPTION_ITEM_TYPE, CheckoutIntentState
from enterprise_access.apps.customer_billing.models import (
    CheckoutIntent,
    EnterpriseStripeCustomer,
    LearnerCreditTopUp,
    SelfServiceSubscriptionRenewal,
    StripeEventData,
    StripeEventSummary
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory
)

FAKER = Faker()


def get_stripe_object_for_event_type(event_type, **overrides):
    """Generate realistic Stripe object data based on event type."""
    if event_type in ('invoice.paid', 'invoice.created'):
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
                            'type': SUBSCRIPTION_ITEM_TYPE
                        },
                        'period': {
                            'start': int((timezone.now() + timedelta(days=30)).timestamp()),
                            'end': int((timezone.now() + timedelta(days=395)).timestamp()),
                        },
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


class EnterpriseStripeCustomerFactory(DjangoModelFactory):
    """
    Factory for creating EnterpriseStripeCustomer instances for testing.
    """
    class Meta:
        model = EnterpriseStripeCustomer

    enterprise_customer_uuid = factory.LazyFunction(uuid4)
    stripe_customer_id = factory.LazyAttribute(lambda x: f'cus_{FAKER.bothify("?" * 17)}_00')
    source = EnterpriseStripeCustomer.SourceChoices.CREATED_FOR_TOP_UP


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
    Factory for creating SelfServiceSubscriptionRenewal instances for testing.
    """
    class Meta:
        model = SelfServiceSubscriptionRenewal

    checkout_intent = factory.SubFactory(CheckoutIntentFactory)
    subscription_plan_renewal_id = factory.Faker('random_int', min=1, max=10000)
    stripe_event_data = factory.SubFactory(StripeEventDataFactory)
    stripe_subscription_id = factory.LazyFunction(lambda: f'sub_{FAKER.bothify("?" * 24)}')
    stripe_invoice_id = None
    effective_date = None


class LearnerCreditTopUpFactory(DjangoModelFactory):
    """
    Factory for creating LearnerCreditTopUp instances for testing.

    enterprise_customer_uuid, subsidy_uuid and initiated_by_lms_user_id are left unset,
    so the model copies them from the policy and the initiating user.

    A top-up can only be created in ``created``. Pass ``state`` (and ``paid_at``, ``deposited_at``
    or ``fulfilled_at``) to get one already in a later state: it is created as a quote, then moved
    there directly in the database, skipping transition_to() and writing no history record.
    """
    class Meta:
        model = LearnerCreditTopUp

    subsidy_access_policy = factory.SubFactory(PerLearnerSpendCapLearnerCreditAccessPolicyFactory)
    initiated_by = factory.SubFactory(UserFactory)
    amount_cents = 100_000
    discount_rate = None
    discount_cents = None
    charge_amount_cents = factory.LazyAttribute(lambda obj: obj.amount_cents - (obj.discount_cents or 0))
    stripe_customer_id = factory.LazyAttribute(lambda x: f'cus_{FAKER.bothify("?" * 17)}_00')

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        later = {
            name: kwargs.pop(name)
            for name in ('state', *model_class.STATE_TIMESTAMP_FIELDS.values())
            if name in kwargs
        }
        top_up = super()._create(model_class, *args, **kwargs)
        if later:
            model_class.objects.filter(pk=top_up.pk).update(**later)
            top_up.refresh_from_db()
        return top_up
