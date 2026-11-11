"""
Tests for the ``enterprise_access.customer_billing.models`` module.
"""
import time
from datetime import datetime, timedelta
from decimal import Decimal
from random import randint
from typing import cast
from unittest import mock
from uuid import uuid4

import ddt
import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.db.models.signals import post_save
from django.test import TestCase, override_settings
from django.utils import timezone
from freezegun import freeze_time

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing import stripe_api
from enterprise_access.apps.customer_billing.constants import (
    ALLOWED_LEARNER_CREDIT_TOP_UP_STATE_TRANSITIONS,
    CheckoutIntentState,
    LearnerCreditTopUpState
)
from enterprise_access.apps.customer_billing.models import (
    CheckoutIntent,
    EnterpriseStripeCustomer,
    FailedCheckoutIntentConflict,
    InvalidLearnerCreditTopUpTransition,
    LearnerCreditTopUp,
    SelfServiceSubscriptionRenewal,
    SlugReservationConflict,
    SspProduct,
    StripeEventData,
    StripeEventSummary
)
from enterprise_access.apps.customer_billing.stripe_event_handlers import StripeEventHandler
from enterprise_access.apps.customer_billing.tests.factories import (
    EnterpriseStripeCustomerFactory,
    LearnerCreditTopUpFactory,
    StripeEventDataFactory
)
from enterprise_access.apps.customer_billing.tests.utils import AttrDict
from enterprise_access.apps.provisioning.models import (
    GetCreateFirstPaidSubscriptionPlanStep,
    GetCreateTrialSubscriptionPlanStep
)
from enterprise_access.apps.provisioning.tests.factories import ProvisionNewCustomerWorkflowFactory
from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory
)

User = get_user_model()


@ddt.ddt
class TestCheckoutIntentModel(TestCase):
    """
    Tests for the CheckoutIntent model methods.
    """

    def setUp(self):
        self.user1 = UserFactory()
        self.user2 = UserFactory()
        self.basic_data = {
            'enterprise_slug': 'test-enterprise',
            'enterprise_name': 'Test Enterprise',
            'quantity': 10,
        }

    def tearDown(self):
        CheckoutIntent.objects.all().delete()

    def test_cleanup_expired_without_mocks(self):
        """Test that cleanup_expired correctly updates state without mocking."""
        # Create a user for our test
        user = UserFactory()

        # Create several intents in different states
        # Intent 1: Expired time but still in CREATED state - should be updated
        expired_intent = CheckoutIntent.objects.create(
            user=user,
            enterprise_name="Expired Enterprise",
            enterprise_slug="expired-enterprise",
            state=CheckoutIntentState.CREATED,
            quantity=10,
            expires_at=timezone.now() - timedelta(minutes=5),
            country='US',
            terms_metadata={'version': '1.0'}
        )

        # Intent 2: Not expired time - should not be updated
        active_intent = CheckoutIntent.objects.create(
            user=UserFactory(),
            enterprise_name="Active Enterprise",
            enterprise_slug="active-enterprise",
            state=CheckoutIntentState.CREATED,
            quantity=15,
            expires_at=timezone.now() + timedelta(minutes=30),
            country='CA',
            terms_metadata={'version': '1.1'}
        )

        # Intent 3: Expired time but already in PAID state - should not be updated
        paid_intent = CheckoutIntent.objects.create(
            user=UserFactory(),
            enterprise_name="Paid Enterprise",
            enterprise_slug="paid-enterprise",
            state=CheckoutIntentState.PAID,
            quantity=20,
            expires_at=timezone.now() - timedelta(minutes=10),
            country='GB',
            terms_metadata={'version': '2.0'}
        )

        # Run the cleanup method
        updated_count = CheckoutIntent.cleanup_expired()

        # Verify it returns the correct count of updated intents
        self.assertEqual(updated_count, 1, "Should have updated exactly one intent")

        # Verify the expired intent was updated to EXPIRED state
        expired_intent.refresh_from_db()
        self.assertEqual(expired_intent.state, CheckoutIntentState.EXPIRED)

        # Verify the active intent was not updated
        active_intent.refresh_from_db()
        self.assertEqual(active_intent.state, CheckoutIntentState.CREATED)

        # Verify the paid intent was not updated
        paid_intent.refresh_from_db()
        self.assertEqual(paid_intent.state, CheckoutIntentState.PAID)

    def test_create_intent_success(self):
        """Test successful creation of checkout intent."""
        terms_metadata = {'version': '1.0', 'accepted_at': '2024-01-15T10:30:00Z'}
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity'],
            terms_metadata=terms_metadata
        )

        self.assertEqual(intent.user, self.user1)
        self.assertEqual(intent.enterprise_slug, self.basic_data['enterprise_slug'])
        self.assertEqual(intent.enterprise_name, self.basic_data['enterprise_name'])
        self.assertEqual(intent.quantity, self.basic_data['quantity'])
        self.assertEqual(intent.terms_metadata, terms_metadata)
        self.assertEqual(intent.state, CheckoutIntentState.CREATED)
        self.assertIsNone(intent.workflow)
        self.assertFalse(intent.is_expired())

    def test_create_intent_name_slug_conflict(self):
        """Test that creating an intent with conflicting slug/name fails."""
        # First user reserves slug and name
        CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Second user tries to use same slug
        with self.assertRaises(SlugReservationConflict):
            CheckoutIntent.create_intent(
                user=cast(AbstractUser, self.user2),
                slug=self.basic_data['enterprise_slug'],
                name='Different Enterprise',
                quantity=self.basic_data['quantity']
            )

        # Second user tries to use same name
        with self.assertRaises(SlugReservationConflict):
            CheckoutIntent.create_intent(
                user=cast(AbstractUser, self.user2),
                slug='different-slug',
                name=self.basic_data['enterprise_name'],
                quantity=self.basic_data['quantity']
            )

    def test_create_intent_updates_existing(self):
        """Test that creating an intent updates the user's existing one."""
        # Create first intent
        first_terms = {'version': '1.0', 'accepted_at': '2024-01-15T10:30:00Z'}
        first_intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug='first-slug',
            name='First Enterprise',
            quantity=5,
            terms_metadata=first_terms
        )

        # Create second intent for same user
        second_terms = {'version': '1.1', 'accepted_at': '2024-01-20T14:45:00Z'}
        second_intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug='second-slug',
            name='Second Enterprise',
            quantity=10,
            terms_metadata=second_terms
        )

        # Should be the same object but with updated fields
        self.assertEqual(first_intent.id, second_intent.id)
        self.assertEqual(second_intent.enterprise_slug, 'second-slug')
        self.assertEqual(second_intent.enterprise_name, 'Second Enterprise')
        self.assertEqual(second_intent.quantity, 10)
        self.assertEqual(second_intent.terms_metadata, second_terms)

        # Should still only have one intent for this user
        self.assertEqual(CheckoutIntent.objects.filter(user=self.user1).count(), 1)

    def test_ssp_product_nullable_fk_accepts_none_and_instance(self):
        """The SSP product relation should allow nulls and valid product instances.

        Create one CheckoutIntent without a product and then verify multiple
        SSP products (teams and the provided academy slugs) can be linked.
        """
        slugs = [
            settings.SSP_DEFAULT_PRODUCT_SLUG,
            'ai-academy-yearly',
            'sustainability-academy-yearly',
            'tech-digital-transformation-academy-yearly',
            'data-academy-yearly',
            'management-academy-yearly',
            'leadership-academy-yearly',
            'supply-chain-academy-yearly',
            'communication-academy-yearly',
        ]

        products = []
        # Ensure the canonical default SSP product exists for FK enforcement
        SspProduct.objects.get_or_create(
            slug=settings.SSP_DEFAULT_PRODUCT_SLUG,
            defaults={
                'stripe_price_lookup_key': 'teams_yearly_price',
                'catalog_query_uuid': uuid4(),
            },
        )
        # Create one intent without specifying a product; model default should apply
        intent_without_product = CheckoutIntent.objects.create(
            user=cast(AbstractUser, self.user1),
            enterprise_slug='nullable-product-enterprise',
            enterprise_name='Nullable Product Enterprise',
            quantity=3,
            expires_at=timezone.now() + timedelta(hours=1),
            country='US',
        )
        self.assertIsNotNone(intent_without_product.ssp_product)
        self.assertEqual(intent_without_product.ssp_product.slug, settings.SSP_DEFAULT_PRODUCT_SLUG)

        for slug in slugs:
            product, _ = SspProduct.objects.get_or_create(
                slug=slug,
                defaults={
                    'stripe_price_lookup_key': f"{slug.replace('-', '_')}_price",
                    'catalog_query_uuid': uuid4(),
                },
            )
            products.append(product)

        for product in products:
            intent = CheckoutIntent.objects.create(
                user=UserFactory(),
                enterprise_slug=f'linked-product-{product.slug}-enterprise',
                enterprise_name=f'Linked {product.slug} Enterprise',
                quantity=1,
                expires_at=timezone.now() + timedelta(hours=1),
                country='US',
                ssp_product=product,
            )
            self.assertEqual(intent.ssp_product, product)

    def test_create_intent_with_existing_failed_intent(self):
        """
        Test that trying to update an intent that's in a failure state raises an exception.
        """
        # First create a normal intent
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug='original-slug',
            name='Original Enterprise',
            quantity=5
        )

        # Now put it in a failure state
        intent.mark_as_paid('cs_test_123')
        intent.mark_backoffice_error('Salesforce integration failed')
        self.assertIn(intent.state, CheckoutIntent.FAILURE_STATES)

        # Trying to create a new intent for the same user should fail
        with self.assertRaises(FailedCheckoutIntentConflict) as context:
            CheckoutIntent.create_intent(
                user=cast(AbstractUser, self.user1),
                slug='new-slug',
                name='New Enterprise',
                quantity=10
            )

        # Verify error message mentions the failed intent
        self.assertIn("failed", str(context.exception).lower())

        # The original intent should remain unchanged
        intent.refresh_from_db()
        self.assertEqual(intent.enterprise_slug, 'original-slug')
        self.assertEqual(intent.enterprise_name, 'Original Enterprise')
        self.assertEqual(intent.quantity, 5)
        self.assertEqual(intent.state, CheckoutIntentState.ERRORED_BACKOFFICE)

    def test_state_transitions_happy_path(self):
        """Test the state transitions for the happy path."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # CREATED → PAID
        intent.mark_as_paid('cs_test_123')
        self.assertEqual(intent.state, CheckoutIntentState.PAID)
        self.assertEqual(intent.stripe_checkout_session_id, 'cs_test_123')

        # PAID → PAID
        intent.mark_as_paid('cs_test_123')
        self.assertEqual(intent.state, CheckoutIntentState.PAID)
        self.assertEqual(intent.stripe_checkout_session_id, 'cs_test_123')

        # PAID → FULFILLED
        workflow = ProvisionNewCustomerWorkflowFactory()
        intent.mark_as_fulfilled(workflow)
        self.assertEqual(intent.state, CheckoutIntentState.FULFILLED)
        self.assertEqual(intent.workflow, workflow)

    def test_state_transitions_error_paths(self):
        """Test state transitions for error paths."""
        # Test PAID → ERRORED_BACKOFFICE
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )
        intent.mark_as_paid('cs_test_123')
        intent.mark_backoffice_error('Salesforce integration failed')
        self.assertEqual(intent.state, CheckoutIntentState.ERRORED_BACKOFFICE)
        self.assertEqual(intent.last_provisioning_error, 'Salesforce integration failed')

        # Test PAID → ERRORED_PROVISIONING
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user2),
            slug='another-slug',
            name='Another Enterprise',
            quantity=7
        )
        intent.mark_as_paid('cs_test_456')
        workflow = ProvisionNewCustomerWorkflowFactory()
        intent.mark_provisioning_error('Provisioning failed: API error', workflow)
        self.assertEqual(intent.state, CheckoutIntentState.ERRORED_PROVISIONING)
        self.assertEqual(intent.last_provisioning_error, 'Provisioning failed: API error')
        self.assertEqual(intent.workflow, workflow)

    def test_invalid_state_transitions(self):
        """Test that invalid state transitions raise exceptions."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Move to ERRORED_PROVISIONING.
        intent.mark_as_paid()
        intent.mark_provisioning_error('Provisioning failed')

        # Should not be able to transition from ERRORED_PROVISIONING to PAID,
        # as this would require going back in time.
        with self.assertRaises(ValueError):
            intent.mark_as_paid()

        # Create a new intent
        intent2 = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user2),
            slug='another-slug',
            name='Another Enterprise',
            quantity=7
        )

        # Should not be able to go from CREATED to FULFILLED (skipping PAID)
        with self.assertRaises(ValueError):
            intent2.mark_as_fulfilled()

    def test_invalid_state_transitions_paid_different_session(self):
        """Attempting a PAID->PAID state transition with different checkout_session_id raises an exception."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Mark as paid
        intent.mark_as_paid('foobar')

        # Should not be able to transition from paid to paid while changing the stripe checkout session ID
        with self.assertRaises(ValueError):
            intent.mark_as_paid('binbaz')

    def test_intent_expiration(self):
        """Test intent expiration logic."""
        # Create an intent with expiry in the past
        past_time = timezone.now() - timedelta(minutes=5)

        intent = CheckoutIntent.objects.create(
            user=self.user1,
            state=CheckoutIntentState.CREATED,
            enterprise_slug='expired-slug',
            enterprise_name='Expired Enterprise',
            quantity=5,
            expires_at=past_time,
            country='FR',
            terms_metadata={'version': '1.0', 'test': True}
        )

        self.assertTrue(intent.is_expired())

        # Test the cleanup method
        with mock.patch.object(CheckoutIntent, 'cleanup_expired') as mock_cleanup:
            CheckoutIntent.cleanup_expired.return_value = 1
            CheckoutIntent.create_intent(
                user=cast(AbstractUser, self.user2),
                slug='new-slug',
                name='New Enterprise',
                quantity=10
            )
            mock_cleanup.assert_called_once()

    def test_admin_portal_url(self):
        """Test the admin_portal_url property."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Should be None before FULFILLED
        self.assertIsNone(intent.admin_portal_url)

        # Move to PAID
        intent.mark_as_paid()
        self.assertIsNone(intent.admin_portal_url)

        # Move to FULFILLED
        with self.settings(ENTERPRISE_ADMIN_PORTAL_URL='https://admin.example.com'):
            intent.mark_as_fulfilled()
            self.assertEqual(
                intent.admin_portal_url,
                f"https://admin.example.com/{self.basic_data['enterprise_slug']}"
            )

    def test_can_reserve(self):
        """Test the can_reserve method."""
        # Initially available
        self.assertTrue(CheckoutIntent.can_reserve(
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name']
        ))

        # Create an intent
        CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Now it should not be available
        self.assertFalse(CheckoutIntent.can_reserve(
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name']
        ))

        # But should be available for the same user
        self.assertTrue(CheckoutIntent.can_reserve(
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            exclude_user=self.user1
        ))

    def test_can_reserve_with_expired_state_future_date(self):
        """
        Test that can_reserve returns True when an intent with matching name/slug
        exists but is already marked as EXPIRED, even with a future expiration date.
        """
        # Create a user for our test
        user = UserFactory()

        # Create an intent with a future expiration date but EXPIRED state
        CheckoutIntent.objects.create(
            user=user,
            enterprise_name="Future Enterprise",
            enterprise_slug="future-enterprise",
            state=CheckoutIntentState.EXPIRED,  # Already expired state
            quantity=10,
            expires_at=timezone.now() + timedelta(hours=2),  # Future date
            country='DE',
            terms_metadata={'version': '1.5', 'future': True}
        )

        # Check if we can reserve the same name and slug
        can_reserve_name = CheckoutIntent.can_reserve(name="Future Enterprise")
        can_reserve_slug = CheckoutIntent.can_reserve(slug="future-enterprise")

        # Both should return True because the existing intent is in EXPIRED state
        self.assertTrue(
            can_reserve_name,
            "Should be able to reserve a name used by an EXPIRED intent even with future date"
        )
        self.assertTrue(
            can_reserve_slug,
            "Should be able to reserve a slug used by an EXPIRED intent even with future date"
        )

        can_reserve_both = CheckoutIntent.can_reserve(
            name="Future Enterprise",
            slug="future-enterprise",
        )
        self.assertTrue(
            can_reserve_both,
            "Combined can_reserve should return True for EXPIRED intent"
        )

    def test_filter_by_name_and_slug(self):
        """Test the filter_by_name_and_slug method."""
        # Create an intent
        CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug='test-slug-123',
            name='Test Name 123',
            quantity=5
        )

        # Should be able to find by slug
        results = CheckoutIntent.filter_by_name_and_slug(slug='test-slug-123')
        self.assertEqual(results.count(), 1)

        # Should be able to find by name
        results = CheckoutIntent.filter_by_name_and_slug(name='Test Name 123')
        self.assertEqual(results.count(), 1)

        # Should find nothing for non-existent values
        results = CheckoutIntent.filter_by_name_and_slug(slug='nonexistent')
        self.assertEqual(results.count(), 0)

        # Should raise error if neither slug nor name is provided
        with self.assertRaises(ValueError):
            CheckoutIntent.filter_by_name_and_slug()

    def test_get_latest_for_customer_requires_identifier(self):
        """The helper should require either customer_uuid or customer_slug."""
        with self.assertRaises(ValueError):
            CheckoutIntent.get_latest_for_customer()

    def test_get_latest_for_customer_filters_and_orders(self):
        """Returns newest matching intent and can require a Stripe customer id."""
        customer_uuid = uuid4()
        slug = 'latest-customer-slug'
        expires_at = timezone.now() + timedelta(hours=1)

        older_with_stripe = CheckoutIntent.objects.create(
            user=UserFactory(),
            enterprise_slug=slug,
            enterprise_uuid=customer_uuid,
            quantity=1,
            expires_at=expires_at,
            stripe_customer_id='cus_older',
        )
        newer_without_stripe = CheckoutIntent.objects.create(
            user=UserFactory(),
            enterprise_slug=slug,
            enterprise_uuid=customer_uuid,
            quantity=1,
            expires_at=expires_at,
            stripe_customer_id='',
        )

        latest_by_slug = CheckoutIntent.get_latest_for_customer(customer_slug=slug)
        self.assertEqual(latest_by_slug.id, newer_without_stripe.id)

        latest_with_stripe = CheckoutIntent.get_latest_for_customer(
            customer_uuid=customer_uuid,
            require_stripe_customer=True,
        )
        self.assertEqual(latest_with_stripe.id, older_with_stripe.id)

    @override_settings(INTENT_RESERVATION_DURATION_MINUTES=60)
    def test_custom_reservation_duration(self):
        """Test that custom reservation duration is respected."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Should expire in 60 minutes based on settings
        expected_expiry = timezone.now() + timedelta(minutes=60)
        time_diff = abs((intent.expires_at - expected_expiry).total_seconds())

        # Allow a 1 second tolerance for test execution time
        self.assertLess(time_diff, 1)

    def test_update_stripe_session_id(self):
        """Test updating Stripe session ID."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        original_modified = intent.modified

        # Update session ID
        intent.update_stripe_session_id('cs_test_789')

        intent.refresh_from_db()
        self.assertEqual(intent.stripe_checkout_session_id, 'cs_test_789')
        self.assertGreater(intent.modified, original_modified)

    def test_create_intent_with_terms_metadata(self):
        """Test creating intent with various terms_metadata values."""
        # Test with None (should work)
        intent1 = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug='test-slug-1',
            name='Test Enterprise 1',
            quantity=5,
            terms_metadata=None
        )
        self.assertIsNone(intent1.terms_metadata)

        # Test with empty dict (should work)
        intent2 = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user2),
            slug='test-slug-2',
            name='Test Enterprise 2',
            quantity=10,
            terms_metadata={}
        )
        self.assertEqual(intent2.terms_metadata, {})

        # Test with complex metadata
        complex_metadata = {
            'version': '2.1',
            'accepted_at': '2024-01-15T10:30:00Z',
            'user_agent': 'Mozilla/5.0...',
            'ip_address': '192.168.1.100',
            'accepted_sections': ['privacy', 'terms', 'cookies'],
            'preferences': {
                'marketing': True,
                'analytics': False
            }
        }
        user3 = UserFactory()
        intent3 = CheckoutIntent.create_intent(
            user=cast(AbstractUser, user3),
            slug='test-slug-3',
            name='Test Enterprise 3',
            quantity=15,
            terms_metadata=complex_metadata
        )
        self.assertEqual(intent3.terms_metadata, complex_metadata)

    def test_mark_as_paid_with_stripe_customer_id(self):
        """Test mark_as_paid with stripe_customer_id parameter."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Test marking as paid with both session_id and customer_id
        intent.mark_as_paid('cs_test_123', 'cus_test_456')
        self.assertEqual(intent.state, CheckoutIntentState.PAID)
        self.assertEqual(intent.stripe_checkout_session_id, 'cs_test_123')
        self.assertEqual(intent.stripe_customer_id, 'cus_test_456')

    def test_mark_as_paid_with_only_stripe_customer_id(self):
        """Test mark_as_paid with only stripe_customer_id parameter."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user2),
            slug='another-slug',
            name='Another Enterprise',
            quantity=7
        )

        # Test marking as paid with only customer_id
        intent.mark_as_paid(stripe_customer_id='cus_test_789')
        self.assertEqual(intent.state, CheckoutIntentState.PAID)
        self.assertIsNone(intent.stripe_checkout_session_id)
        self.assertEqual(intent.stripe_customer_id, 'cus_test_789')

    def test_mark_as_paid_idempotent_with_stripe_customer_id(self):
        """Test that mark_as_paid is idempotent when called with same stripe_customer_id."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Mark as paid first time
        intent.mark_as_paid('cs_test_123', 'cus_test_456')
        first_modified = intent.modified

        # Mark as paid again with same values - should be idempotent
        intent.mark_as_paid('cs_test_123', 'cus_test_456')
        self.assertEqual(intent.state, CheckoutIntentState.PAID)
        self.assertEqual(intent.stripe_checkout_session_id, 'cs_test_123')
        self.assertEqual(intent.stripe_customer_id, 'cus_test_456')
        # Modified time should have changed since we called save()
        self.assertGreater(intent.modified, first_modified)

    def test_mark_as_paid_different_stripe_customer_id_raises_error(self):
        """Test that mark_as_paid raises error when called with different stripe_customer_id."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        # Mark as paid first time
        intent.mark_as_paid(stripe_customer_id='cus_test_456')

        # Try to mark as paid with different customer_id - should raise ValueError
        with self.assertRaises(ValueError) as context:
            intent.mark_as_paid(stripe_customer_id='cus_test_different')

        self.assertIn('Cannot transition from PAID to PAID with a different stripe_customer_id', str(context.exception))

    def test_mark_as_paid_update_fields_includes_stripe_customer_id(self):
        """Test that save() includes stripe_customer_id in update_fields."""
        intent = CheckoutIntent.create_intent(
            user=cast(AbstractUser, self.user1),
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            quantity=self.basic_data['quantity']
        )

        with mock.patch.object(intent, 'save') as mock_save:
            intent.mark_as_paid(stripe_customer_id='cus_test_456')

            # Verify save was called with correct update_fields
            mock_save.assert_called_once_with(
                update_fields=['state', 'stripe_checkout_session_id', 'stripe_customer_id', 'modified']
            )

    def test_create_intent_patch_fields_do_not_unset(self):
        """
        Test that calling create_intent() without supplying slug, name, country, or terms_metadata
        does NOT unset those fields in the DB record (acts like PATCH).
        """
        user = UserFactory()
        initial_intent = CheckoutIntent.create_intent(
            user=user,
            quantity=11,
            slug=self.basic_data['enterprise_slug'],
            name=self.basic_data['enterprise_name'],
            country='JP',
            terms_metadata={'foo': 'bar'}
        )

        # Call create_intent() again, omitting slug, name, country, and terms_metadata
        updated_intent = CheckoutIntent.create_intent(
            user=user,
            quantity=22  # Only update quantity
            # No slug, name, country, or terms_metadata
        )

        initial_intent.refresh_from_db()

        # Make sure we only created one new intent record, not two.
        assert initial_intent == updated_intent
        # Should have updated quantity field only.
        self.assertEqual(updated_intent.quantity, 22)  # Updated
        # Old values should remain the same as they were omitted from the 2nd call.
        assert updated_intent.enterprise_slug == self.basic_data['enterprise_slug']
        assert updated_intent.enterprise_name == self.basic_data['enterprise_name']
        assert updated_intent.country == 'JP'
        assert updated_intent.terms_metadata == {'foo': 'bar'}

    def test_create_intent_blank_slug_and_name_success(self):
        """
        Test that calling create_intent() with both slug and name blank does not throw.

        This test case supports the use case of creating an intent without reserving,
        needed for the Plan Details page.
        """
        user = UserFactory()
        intent = CheckoutIntent.create_intent(
            user=user,
            quantity=self.basic_data['quantity'],
            slug=None,
            name=None,
        )
        intent.refresh_from_db()
        assert intent.enterprise_slug is None
        assert intent.enterprise_name is None
        assert intent.quantity == self.basic_data['quantity']

    @ddt.data(
        {'slug': 'present-slug', 'name': None},
        {'slug': None, 'name': 'Present Name'}
    )
    @ddt.unpack
    def test_create_intent_blank_slug_or_name_raises(self, slug, name):
        """
        Test that calling create_intent() with a non-blank slug and blank name, or vice-versa,
        raises a ValueError.
        """
        user = UserFactory()
        with self.assertRaises(ValueError) as exc:
            CheckoutIntent.create_intent(
                user=user,
                quantity=self.basic_data['quantity'],
                slug=slug,
                name=name,
            )
        self.assertIn("slug and name must either both be given or neither be given", str(exc.exception))

    def test_uuid_field_not_null_and_has_default(self):
        """
        Test that the uuid field is not nullable and automatically generates a UUID.
        """
        user = UserFactory()

        # Create an intent without explicitly setting uuid
        intent = CheckoutIntent.create_intent(
            user=user,
            slug='test-uuid-slug',
            name='Test UUID Enterprise',
            quantity=5
        )

        # Verify uuid was automatically generated
        self.assertIsNotNone(intent.uuid)
        self.assertEqual(len(str(intent.uuid)), 36)  # Standard UUID string length

        # Create another intent and verify it gets a different UUID
        user2 = UserFactory()
        intent2 = CheckoutIntent.create_intent(
            user=user2,
            slug='test-uuid-slug-2',
            name='Test UUID Enterprise 2',
            quantity=10
        )

        self.assertIsNotNone(intent2.uuid)
        self.assertNotEqual(intent.uuid, intent2.uuid)

        # Verify we can still manually set a UUID if needed
        custom_uuid = uuid4()
        intent3 = CheckoutIntent.objects.create(
            user=UserFactory(),
            enterprise_name="Manual UUID Enterprise",
            enterprise_slug="manual-uuid-enterprise",
            state=CheckoutIntentState.CREATED,
            quantity=15,
            expires_at=timezone.now() + timedelta(minutes=30),
            uuid=custom_uuid
        )
        self.assertEqual(intent3.uuid, custom_uuid)


class TestStripeEventSummary(TestCase):
    """Test cases for StripeEventSummary model and populate_with_summary_data method."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory()
        cls.checkout_intent = CheckoutIntent.objects.create(
            uuid=uuid4(),
            user=cls.user,
            enterprise_name='Test Enterprisee',
            enterprise_slug='test-enterprisee',
            stripe_customer_id='cus_test_456',
            quantity=10,
            expires_at=timezone.now() + timedelta(minutes=30),
        )

    def test_populate_with_summary_data_invoice_event(self):
        """Test populating summary from invoice.paid event with full invoice data."""
        # Create mock invoice event data
        invoice_event_data = {
            'id': 'evt_test_invoice',
            'type': 'invoice.paid',
            'created': 1700000000,
            'data': {
                'object': {
                    'object': 'invoice',
                    'id': 'in_test_123',
                    'subscription': 'sub_test_456',
                    'amount_paid': 2500,  # $25.00 in cents
                    'currency': 'usd',
                    'lines': {
                        'data': [
                            {
                                'quantity': 10,
                                'pricing': {
                                    'unit_amount_decimal': '250.0'  # $2.50 per unit
                                }
                            }
                        ]
                    },
                    'parent': {
                        'subscription_details': {
                            'subscription': 'sub_test_456'
                        }
                    }
                }
            }
        }

        # Create StripeEventData
        # The connected signal receiver will create the related summary record
        stripe_event_data = StripeEventData.objects.create(
            event_id='evt_test_invoice',
            event_type='invoice.paid',
            checkout_intent=self.checkout_intent,
            data=invoice_event_data
        )
        summary = stripe_event_data.summary
        summary.populate_with_summary_data()
        summary.save()

        # Verify invoice-specific fields are populated
        self.assertEqual(summary.event_id, 'evt_test_invoice')
        self.assertEqual(summary.event_type, 'invoice.paid')
        self.assertEqual(summary.checkout_intent, self.checkout_intent)
        self.assertEqual(summary.stripe_object_type, 'invoice')
        self.assertEqual(summary.stripe_invoice_id, 'in_test_123')
        self.assertEqual(summary.stripe_subscription_id, 'sub_test_456')
        self.assertEqual(summary.invoice_amount_paid, 2500)
        self.assertEqual(summary.invoice_currency, 'usd')
        self.assertEqual(summary.invoice_unit_amount, 250)
        self.assertEqual(summary.invoice_unit_amount_decimal, Decimal(250.0))
        self.assertEqual(summary.invoice_quantity, 10)

    def test_populate_with_summary_data_subscription_created_event(self):
        """Test populating summary from customer.subscription.created event."""
        # Create mock subscription event data
        subscription_event_data = {
            'id': 'evt_test_sub_created',
            'type': 'customer.subscription.created',
            'created': 1700000000,
            'data': {
                'object': {
                    'object': 'subscription',
                    'id': 'sub_test_789',
                    'status': 'active',
                    'currency': 'usd',
                    'items': {
                        'data': [
                            {
                                'current_period_start': 1609459200,  # 2021-01-01 00:00:00 UTC
                                'current_period_end': 1640995200,    # 2022-01-01 00:00:00 UTC
                            }
                        ]
                    }
                }
            }
        }

        # Create StripeEventData
        stripe_event_data = StripeEventData.objects.create(
            event_id='evt_test_sub_created',
            event_type='customer.subscription.created',
            checkout_intent=self.checkout_intent,
            data=subscription_event_data,
        )

        # Create and populate summary
        summary = StripeEventSummary(stripe_event_data=stripe_event_data)
        summary.populate_with_summary_data()

        # Verify subscription-specific fields are populated
        self.assertEqual(summary.event_id, 'evt_test_sub_created')
        self.assertEqual(summary.event_type, 'customer.subscription.created')
        self.assertEqual(summary.checkout_intent, self.checkout_intent)
        self.assertEqual(summary.stripe_object_type, 'subscription')
        self.assertEqual(summary.stripe_subscription_id, 'sub_test_789')
        self.assertEqual(summary.subscription_status, 'active')

        # Verify datetime conversion
        self.assertIsNotNone(summary.subscription_period_start)
        self.assertIsNotNone(summary.subscription_period_end)

        # Check that the converted dates are reasonable (2021-2022)
        self.assertEqual(summary.subscription_period_start.year, 2021)
        self.assertEqual(summary.subscription_period_end.year, 2022)

    def test_populate_with_summary_data_extracts_cancel_at(self):
        """Test that populate_with_summary_data correctly extracts subscription cancel_at."""
        # Create timestamp for cancel_at (14 days from now)
        cancel_at_timestamp = int((timezone.now() + timedelta(days=14)).timestamp())

        # Create mock subscription event data with cancel_at
        subscription_event_data = {
            'id': 'evt_test_sub_cancel_at',
            'type': 'customer.subscription.updated',
            'created': 1700000000,
            'data': {
                'object': {
                    'object': 'subscription',
                    'id': 'sub_test_cancel_123',
                    'status': 'trialing',
                    'currency': 'usd',
                    'cancel_at': cancel_at_timestamp,
                    'items': {
                        'data': [
                            {
                                'current_period_start': 1609459200,  # 2021-01-01 00:00:00 UTC
                                'current_period_end': 1640995200,    # 2022-01-01 00:00:00 UTC
                            }
                        ]
                    }
                }
            }
        }

        # Create StripeEventData
        stripe_event_data = StripeEventData.objects.create(
            event_id='evt_test_sub_cancel_at',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data=subscription_event_data,
        )

        # Create and populate summary
        summary = StripeEventSummary(stripe_event_data=stripe_event_data)
        summary.populate_with_summary_data()

        # Verify cancel_at is extracted and converted to datetime
        self.assertIsNotNone(summary.subscription_cancel_at)
        # Verify it's within a reasonable range (should be around 14 days from now)
        expected_cancel_at = timezone.now() + timedelta(days=14)
        time_diff = abs((summary.subscription_cancel_at - expected_cancel_at).total_seconds())
        self.assertLess(time_diff, 60, "cancel_at timestamp should be within 60 seconds of expected value")

    def test_populate_with_summary_data_with_subscription_plan_uuid(self):
        """Test that subscription_plan_uuid is extracted from related workflow."""
        # Create a workflow
        workflow = ProvisionNewCustomerWorkflowFactory()

        # Create checkout intent linked to the workflow
        checkout_intent = CheckoutIntent.create_intent(
            user=self.user,
            slug='test-enterprise-workflow',
            name='Test Enterprise Workflow',
            quantity=5
        )
        checkout_intent.workflow = workflow
        checkout_intent.save()

        # Create a subscription plan step with output containing subscription_plan_uuid
        trial_subscription_plan_uuid = uuid4()
        _ = GetCreateTrialSubscriptionPlanStep.objects.create(
            workflow_record_uuid=workflow.uuid,
            input_data={
                'title': 'Test Plan',
                'salesforce_opportunity_line_item': 'test-oli-123',
                'start_date': '2024-01-01T00:00:00Z',
                'expiration_date': '2025-01-01T00:00:00Z',
                'desired_num_licenses': 5,
                'product_id': 123,
            },
            output_data={
                'uuid': str(trial_subscription_plan_uuid),  # This is what we're testing
                'title': 'Test Plan',
                'salesforce_opportunity_line_item': 'test-oli-123',
                'created': '2024-01-01T00:00:00Z',
                'start_date': '2024-01-01T00:00:00Z',
                'expiration_date': '2025-01-01T00:00:00Z',
                'is_active': True,
                'is_current': True,
                'plan_type': 'Subscription',
                'enterprise_catalog_uuid': str(uuid4()),
                'product': 123,
                'desired_num_licenses': 5,
            }
        )

        first_paid_subscription_plan_uuid = uuid4()
        _ = GetCreateFirstPaidSubscriptionPlanStep.objects.create(
            workflow_record_uuid=workflow.uuid,
            input_data={
                'title': 'First Paid Plan',
                'salesforce_opportunity_line_item': None,
                'start_date': '2025-01-01T00:00:00Z',
                'expiration_date': '2026-01-01T00:00:00Z',
                'desired_num_licenses': 5,
                'product_id': 456,
            },
            output_data={
                'uuid': str(first_paid_subscription_plan_uuid),  # Should be IGNORED.
                'title': 'First Paid Plan',
                'salesforce_opportunity_line_item': None,
                'created': '2024-01-01T00:00:00Z',
                'start_date': '2025-01-01T00:00:00Z',
                'expiration_date': '2026-01-01T00:00:00Z',
                'is_active': True,
                'is_current': True,
                'plan_type': 'Subscription',
                'enterprise_catalog_uuid': str(uuid4()),
                'product': 456,
                'desired_num_licenses': 5,
            }
        )

        # Create mock subscription event data
        subscription_event_data = {
            'id': 'evt_test_with_plan_uuid',
            'type': 'customer.subscription.created',
            'created': 1700000000,
            'data': {
                'object': {
                    'object': 'subscription',
                    'id': 'sub_test_with_uuid',
                    'currency': 'usd',
                    'status': 'active',
                    'items': {
                        'data': [
                            {
                                'current_period_start': 1609459200,
                                'current_period_end': 1640995200,
                            }
                        ]
                    }
                }
            }
        }

        # Create StripeEventData linked to the checkout intent with workflow
        stripe_event_data = StripeEventData.objects.create(
            event_id='evt_test_with_plan_uuid',
            event_type='customer.subscription.created',
            checkout_intent=checkout_intent,
            data=subscription_event_data
        )

        # Create and populate summary
        summary = StripeEventSummary(stripe_event_data=stripe_event_data)
        summary.populate_with_summary_data()

        # Verify that trial_subscription_plan_uuid was extracted from the workflow
        self.assertEqual(summary.subscription_plan_uuid, trial_subscription_plan_uuid)
        self.assertEqual(summary.event_id, 'evt_test_with_plan_uuid')
        self.assertEqual(summary.event_type, 'customer.subscription.created')
        self.assertEqual(summary.checkout_intent, checkout_intent)
        self.assertEqual(summary.stripe_subscription_id, 'sub_test_with_uuid')
        self.assertEqual(summary.subscription_status, 'active')

    def test_checkout_intent_previous_summary(self):
        """
        Test CheckoutIntent.previous_summary() method returns most recent
        summary before given event.
        """
        # Create multiple StripeEventData and StripeEventSummary records with different timestamps
        event_times = [
            1609459200,  # 2021-01-01 00:00:00 UTC - earliest
            1609462800,  # 2021-01-01 01:00:00 UTC - middle
            1609466400,  # 2021-01-01 02:00:00 UTC - latest
            1609470000,  # 2021-01-01 03:00:00 UTC - current event (not in summaries)
        ]

        summaries = []
        for i, event_time in enumerate(event_times[:3]):  # Create 3 summaries
            # Create StripeEventData
            event_data = {
                'id': f'evt_test_{i}',
                'type': 'invoice.paid',
                'created': event_time,
                'data': {
                    'object': {
                        'object': 'invoice',
                        'id': f'in_test_{i}',
                        'amount_paid': 1000 + i,
                        'currency': 'usd',
                        'lines': {'data': []},
                        'parent': {'subscription_details': {'subscription': 'sub_test'}}
                    }
                }
            }

            # The post_save signal receiver automatically populates
            # a related StripeEventSummary.
            stripe_event_data = StripeEventData.objects.create(
                event_id=f'evt_test_{i}',
                event_type='invoice.paid',
                checkout_intent=self.checkout_intent,
                data=event_data
            )
            summary = stripe_event_data.summary
            summary.populate_with_summary_data()
            summary.save()
            summaries.append(summary)

        # Verify summaries have correct timestamps
        self.assertEqual(summaries[0].stripe_event_created_at.timestamp(), event_times[0])
        self.assertEqual(summaries[1].stripe_event_created_at.timestamp(), event_times[1])
        self.assertEqual(summaries[2].stripe_event_created_at.timestamp(), event_times[2])

        # Create a mock Stripe event with timestamp after all summaries
        current_event = mock.Mock(spec=stripe.Event)
        current_event.created = event_times[3]  # 2021-01-01 03:00:00 UTC

        # Test: previous_summary should return the most recent summary (index 2)
        previous = self.checkout_intent.previous_summary(current_event)
        self.assertEqual(previous, summaries[2])
        self.assertEqual(previous.event_id, 'evt_test_2')

        # Test: event between first and second summary should return first summary
        middle_event = mock.Mock(spec=stripe.Event)
        middle_event.created = event_times[1] - 1  # Just before second event
        previous = self.checkout_intent.previous_summary(middle_event)
        self.assertEqual(previous, summaries[0])

        # Test: event before all summaries should return None
        early_event = mock.Mock(spec=stripe.Event)
        early_event.created = event_times[0] - 1  # Before any summaries
        previous = self.checkout_intent.previous_summary(early_event)
        self.assertIsNone(previous)

        # Test: with different checkout intent should return None
        other_user = UserFactory()
        other_checkout_intent = CheckoutIntent.create_intent(
            user=other_user,
            slug='other-enterprise',
            name='Other Enterprise',
            quantity=5
        )
        previous = other_checkout_intent.previous_summary(current_event)
        self.assertIsNone(previous)

    def test_populate_with_summary_data_falls_back_to_stripe_event_data_created(self):
        """
        When the raw event payload lacks a top-level 'created' key,
        stripe_event_created_at should fall back to StripeEventData.created
        so the non-nullable field is always populated.
        """
        event_data_without_created = {
            'id': 'evt_test_no_created',
            'type': 'customer.subscription.created',
            # Note: no top-level 'created' key
            'data': {
                'object': {
                    'object': 'subscription',
                    'id': 'sub_test_no_created',
                    'status': 'active',
                    'currency': 'usd',
                    'items': {
                        'data': [
                            {
                                'current_period_start': 1609459200,
                                'current_period_end': 1640995200,
                            }
                        ]
                    }
                }
            }
        }

        stripe_event_data = StripeEventData.objects.create(
            event_id='evt_test_no_created',
            event_type='customer.subscription.created',
            checkout_intent=self.checkout_intent,
            data=event_data_without_created,
        )

        summary = StripeEventSummary(stripe_event_data=stripe_event_data)
        summary.populate_with_summary_data()

        self.assertIsNotNone(summary.stripe_event_created_at)
        self.assertEqual(summary.stripe_event_created_at, stripe_event_data.created)

    def _create_mock_stripe_event(self, event_type, event_data):
        """Helper to create a mock Stripe event."""
        mock_event = mock.MagicMock(spec=stripe.Event)
        created_at = timezone.now() - timedelta(seconds=randint(1, 30))
        mock_event.created = int(created_at.timestamp())
        mock_event.type = event_type
        numeric_id = str(randint(1, 100000)).zfill(6)
        mock_event.id = f'evt_test_{event_type.replace(".", "_")}_{numeric_id}'
        mock_event.data = mock.Mock()
        mock_event.data.object = AttrDict.wrap(event_data)
        return mock_event

    @mock.patch.object(stripe_api, 'stripe')
    def test_upcoming_invoice_amount_due(self, mock_stripe):
        """
        Test that the `upcoming_invoice_amount_due` value in the StripeEventSummary model is populated
        from the upcoming invoice
        """
        subscription_data_object = {
            'object': 'subscription',
            'id': 'sub_test_789',
            'status': 'active',
            'currency': 'usd',
            'customer': 'cus_test_456',
            'items': {
                'data': [
                    {
                        'object': 'subscription_item',
                        'current_period_start': 1609459200,  # 2021-01-01 00:00:00 UTC
                        'current_period_end': 1640995200,    # 2022-01-01 00:00:00 UTC
                    }
                ]
            },
            'metadata': {
                'checkout_intent_id': self.checkout_intent.id,
                'enterprise_customer_name': 'Test Enterprise',
                'enterprise_customer_slug': 'test-enterprise',
            }
        }
        subscription_event_data = {
            'id': 'evt_test_sub_created',
            'type': 'customer.subscription.created',
            'created': 1700000000,
            'data': {
                'object': subscription_data_object
            }
        }

        # Create subscription created stripe event
        mock_event = self._create_mock_stripe_event(
            "customer.subscription.created", subscription_data_object
        )

        # Create StripeEventData
        stripe_event_data = StripeEventData.objects.create(
            event_id=mock_event.id,
            event_type='customer.subscription.created',
            checkout_intent=self.checkout_intent,
            data=subscription_event_data
        )

        # Mock out the 'preview invoice' stripe call
        fake_invoice = {'amount_due': '200'}
        mock_stripe.Invoice.create_preview.return_value = fake_invoice

        # Dispatch creation event, which populates the upcoming_invoice_amount_due value
        StripeEventHandler.dispatch(mock_event)

        summary = StripeEventSummary.objects.get(event_id=stripe_event_data.event_id)
        self.assertEqual(summary.upcoming_invoice_amount_due, 200)


class TestCheckoutIntentStalledFulfillment(TestCase):
    """
    Tests for CheckoutIntent stalled fulfillment detection and handling.
    """

    def setUp(self):
        self.user = UserFactory()

    def _create_intent(self, state, slug_suffix='', **kwargs):
        """Helper to create a CheckoutIntent with minimal boilerplate."""
        defaults = {
            'user': kwargs.pop('user', self.user),
            'state': state,
            'enterprise_name': f'Test Enterprise{slug_suffix}',
            'enterprise_slug': f'test-enterprise{slug_suffix}',
            'quantity': 10,
            'expires_at': timezone.now() + timedelta(hours=1),
        }
        defaults.update(kwargs)
        return CheckoutIntent.objects.create(**defaults)

    def test_mark_stalled_fulfillment_intents_finds_stalled_intents(self):
        """Test class method finds and marks stalled intents."""
        intent = self._create_intent(CheckoutIntentState.PAID)
        CheckoutIntent.objects.filter(pk=intent.pk).update(
            modified=timezone.now() - timedelta(minutes=5)
        )

        count, uuids = CheckoutIntent.mark_stalled_fulfillment_intents(stalled_threshold_seconds=180)

        self.assertEqual(count, 1)
        self.assertEqual(len(uuids), 1)
        self.assertIn(str(intent.pk), uuids)

        intent.refresh_from_db()
        self.assertEqual(intent.state, CheckoutIntentState.ERRORED_FULFILLMENT_STALLED)
        self.assertIsNotNone(intent.last_provisioning_error)

    def test_mark_stalled_fulfillment_intents_ignores_non_paid_states(self):
        """Test that only PAID intents are marked as stalled."""
        created_intent = self._create_intent(CheckoutIntentState.CREATED, '-created')
        fulfilled_intent = self._create_intent(CheckoutIntentState.FULFILLED, '-fulfilled', user=UserFactory())
        paid_intent = self._create_intent(CheckoutIntentState.PAID, '-paid', user=UserFactory())

        CheckoutIntent.objects.filter(
            pk__in=[created_intent.pk, fulfilled_intent.pk, paid_intent.pk]
        ).update(modified=timezone.now() - timedelta(minutes=5))

        count, uuids = CheckoutIntent.mark_stalled_fulfillment_intents(stalled_threshold_seconds=180)

        self.assertEqual(count, 1)
        self.assertIn(str(paid_intent.pk), uuids)

        for intent, expected_state in [
            (created_intent, CheckoutIntentState.CREATED),
            (fulfilled_intent, CheckoutIntentState.FULFILLED),
            (paid_intent, CheckoutIntentState.ERRORED_FULFILLMENT_STALLED)
        ]:
            intent.refresh_from_db()
            self.assertEqual(intent.state, expected_state)


class TestSelfServiceSubscriptionRenewal(TestCase):
    """
    Tests for the SelfServiceSubscriptionRenewal model.
    """

    def setUp(self):
        self.user = UserFactory()
        self.checkout_intent = CheckoutIntent.create_intent(
            user=self.user,
            slug='test-enterprise',
            name='Test Enterprise',
            quantity=10
        )

    def tearDown(self):
        """Clean up test data."""
        SelfServiceSubscriptionRenewal.objects.all().delete()
        CheckoutIntent.objects.all().delete()

    def test_self_service_subscription_renewal_mark_as_processed(self):
        """Test that mark_as_processed sets processed_at timestamp."""
        # Create StripeEventData first
        event_data = StripeEventData.objects.create(
            event_id='evt_test_123',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'test': 'data', 'created': 1700000000}
        )

        # Create renewal record
        expected_renewal_id = 123
        renewal = SelfServiceSubscriptionRenewal.objects.create(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id,
            stripe_subscription_id='sub_test_123',
            stripe_event_data=event_data
        )

        # Verify initial state
        self.assertIsNone(renewal.processed_at)

        # Mark as processed
        before_processing = timezone.now()
        renewal.mark_as_processed()
        after_processing = timezone.now()

        # Verify processed_at was set
        self.assertIsNotNone(renewal.processed_at)
        self.assertGreaterEqual(renewal.processed_at, before_processing)
        self.assertLessEqual(renewal.processed_at, after_processing)

    def test_self_service_subscription_renewal_is_canceled_default_false(self):
        """Test that new renewal records default is_canceled to False."""
        event_data = StripeEventData.objects.create(
            event_id='evt_test_is_canceled_default',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'test': 'data', 'created': 1700000000}
        )

        renewal = SelfServiceSubscriptionRenewal.objects.create(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=999,
            stripe_subscription_id='sub_test_is_canceled_default',
            stripe_event_data=event_data,
        )

        self.assertFalse(renewal.is_canceled)

    def test_self_service_subscription_renewal_mark_as_processed_multiple_times(self):
        """Test that calling mark_as_processed multiple times updates the timestamp each time."""
        # Create StripeEventData first
        event_data = StripeEventData.objects.create(
            event_id='evt_test_456',
            event_type='customer.subscription.updated',
            checkout_intent=self.checkout_intent,
            data={'test': 'data', 'created': 1700000000}
        )

        expected_renewal_id = 456
        renewal = SelfServiceSubscriptionRenewal.objects.create(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id,
            stripe_subscription_id='sub_test_456',
            stripe_event_data=event_data
        )

        # Mark as processed first time
        renewal.mark_as_processed()
        first_processed_at = renewal.processed_at
        self.assertIsNotNone(first_processed_at)

        # Wait a small amount to ensure timestamp difference
        time.sleep(0.001)

        # Mark as processed second time
        renewal.mark_as_processed()
        second_processed_at = renewal.processed_at

        # Verify the timestamp was updated (current implementation behavior)
        self.assertIsNotNone(second_processed_at)
        self.assertGreaterEqual(second_processed_at, first_processed_at)

    def test_self_service_subscription_renewal_relationships(self):
        """Test that foreign key relationships work correctly."""
        # Create StripeEventData
        event_data = StripeEventDataFactory(
            checkout_intent=self.checkout_intent,
            event_type='customer.subscription.updated',
        )
        summary = event_data.summary

        # Create renewal record with relationships
        expected_renewal_id = 789
        renewal = SelfServiceSubscriptionRenewal.objects.create(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id,
            stripe_subscription_id=summary.stripe_subscription_id,
            stripe_event_data=event_data,
        )

        # Test forward relationships
        self.assertEqual(renewal.checkout_intent, self.checkout_intent)
        self.assertEqual(renewal.stripe_event_data, summary.stripe_event_data)

        # Test reverse relationship from checkout_intent
        related_renewals = self.checkout_intent.renewals.all()
        self.assertIn(renewal, related_renewals)

        # Test reverse relationship from event_data
        related_renewals_from_event = SelfServiceSubscriptionRenewal.objects.filter(
            stripe_event_data=event_data,
        )
        self.assertIn(renewal, related_renewals_from_event)

        # Test querying by related fields
        renewals_by_intent = SelfServiceSubscriptionRenewal.objects.filter(
            checkout_intent__enterprise_name='Test Enterprise'
        )
        self.assertIn(renewal, renewals_by_intent)

        renewals_by_event = SelfServiceSubscriptionRenewal.objects.filter(
            stripe_event_data__event_type='customer.subscription.updated'
        )
        self.assertIn(renewal, renewals_by_event)


class TestSspProduct(TestCase):
    """Tests for the SspProduct model."""

    def _make_product(self, **kwargs):
        """Build an unsaved SspProduct with sensible defaults."""
        defaults = {
            'slug': 'ai-academy-yearly',
            'stripe_price_lookup_key': 'ai_academy_yearly_price',
            'catalog_query_uuid': uuid4(),
            'catalog_query_id': randint(1, 1000000),
            'academy_uuid': uuid4(),
            'marketing_url': 'https://example.com/test-marketing',
        }
        defaults.update(kwargs)
        return SspProduct(**defaults)

    def test_str(self):
        """SspProduct string representation includes slug."""
        product = self._make_product(slug='teams-yearly')
        self.assertEqual(str(product), '<SspProduct slug=teams-yearly>')

    @mock.patch('enterprise_access.apps.customer_billing.models.get_cached_course_count')
    def test_course_count_fetches_by_catalog_query_uuid(self, mock_get_course_count):
        mock_get_course_count.return_value = 93
        product = self._make_product()
        self.assertEqual(product.course_count, 93)
        mock_get_course_count.assert_called_once_with(product.catalog_query_uuid)

    @mock.patch('enterprise_access.apps.customer_billing.models.get_cached_academy_data')
    def test_academy_properties_return_fields(self, mock_get_data):
        mock_get_data.return_value = {
            'title': 'AI Academy',
            'long_name': 'AI Academy for Enterprise Teams',
            'long_description': 'In-depth AI learning path',
            'short_description': 'Quick AI intro',
            'description': 'Learn AI',
            'marketing_url': 'https://example.com/ai',
            'thumbnail_url': 'https://example.com/ai.png',
            'tags': ['ai', 'ml'],
        }
        product = self._make_product()
        self.assertEqual(product.academy_title, 'AI Academy')
        self.assertEqual(product.academy_long_name, 'AI Academy for Enterprise Teams')
        self.assertEqual(product.academy_description, 'In-depth AI learning path')
        self.assertEqual(product.academy_marketing_url, 'https://example.com/ai')
        self.assertEqual(product.academy_thumbnail_url, 'https://example.com/ai.png')
        self.assertEqual(product.academy_tags, ['ai', 'ml'])

    @mock.patch('enterprise_access.apps.customer_billing.models.get_cached_academy_data')
    def test_academy_description_falls_back_from_long_to_short(self, mock_get_data):
        mock_get_data.return_value = {
            'title': 'AI Academy',
            'short_description': 'Quick AI intro',
            'description': 'Learn AI',
        }
        product = self._make_product()
        self.assertEqual(product.academy_description, 'Quick AI intro')

    @mock.patch('enterprise_access.apps.customer_billing.models.get_cached_academy_data')
    def test_academy_description_falls_back_to_description(self, mock_get_data):
        mock_get_data.return_value = {
            'title': 'AI Academy',
            'description': 'Learn AI',
        }
        product = self._make_product()
        self.assertEqual(product.academy_description, 'Learn AI')

    @mock.patch('enterprise_access.apps.customer_billing.models.get_cached_academy_data')
    def test_academy_long_name_falls_back_to_title(self, mock_get_data):
        mock_get_data.return_value = {
            'title': 'AI Academy',
            'long_description': 'In-depth AI learning path',
            'description': 'Learn AI',
        }
        product = self._make_product()
        self.assertEqual(product.academy_long_name, 'AI Academy')

    @mock.patch('enterprise_access.apps.customer_billing.models.get_cached_academy_data')
    def test_academy_properties_return_none_when_no_academy_uuid(self, mock_get_data):
        mock_get_data.return_value = None
        product = self._make_product(academy_uuid=None)
        self.assertIsNone(product.academy_title)
        self.assertIsNone(product.academy_long_name)
        self.assertIsNone(product.academy_description)
        self.assertIsNone(product.academy_marketing_url)
        self.assertIsNone(product.academy_thumbnail_url)
        self.assertIsNone(product.academy_tags)
        mock_get_data.assert_called_with(None)

    @mock.patch('enterprise_access.apps.customer_billing.models.get_cached_academy_data')
    def test_academy_properties_return_none_on_missing_key(self, mock_get_data):
        mock_get_data.return_value = {}  # empty dict — keys missing (falsy)
        product = self._make_product()
        self.assertIsNone(product.academy_title)
        self.assertIsNone(product.academy_description)
        self.assertIsNone(product.academy_tags)

    @mock.patch('enterprise_access.apps.customer_billing.models.get_cached_academy_data')
    def test_academy_description_payload_present_but_no_description_keys(self, mock_get_data):
        # payload exists (truthy) but description keys are missing or falsy
        mock_get_data.return_value = {'title': 'AI Academy'}
        product = self._make_product()
        self.assertIsNone(product.academy_description)

    @mock.patch('enterprise_access.apps.customer_billing.models.get_cached_academy_data')
    def test_academy_long_name_empty_string_falls_back_to_title(self, mock_get_data):
        # long_name present but empty should fall back to title
        mock_get_data.return_value = {
            'title': 'AI Academy',
            'long_name': '',
        }
        product = self._make_product()
        self.assertEqual(product.academy_long_name, 'AI Academy')

    @mock.patch('enterprise_access.apps.customer_billing.models.get_cached_academy_data')
    def test_academy_thumbnail_url_prefers_image(self, mock_get_data):
        mock_get_data.return_value = {
            'title': 'AI Academy',
            'image': 'https://example.com/ai-image.png',
            'thumbnail_url': 'https://example.com/ai-thumbnail.png',
        }
        product = self._make_product()
        self.assertEqual(product.academy_thumbnail_url, 'https://example.com/ai-image.png')

    def test_marketing_url_nullable(self):
        """marketing_url can be null."""
        product = SspProduct.objects.create(
            slug='test-nullable',
            stripe_price_lookup_key='test_nullable_key',
            catalog_query_uuid=uuid4(),
            is_active=True,
            marketing_url=None,
        )
        product.refresh_from_db()
        self.assertIsNone(product.marketing_url)

    def test_marketing_url_persists(self):
        """marketing_url round-trips through the database."""
        product = SspProduct.objects.create(
            slug='test-persist',
            stripe_price_lookup_key='test_persist_key',
            catalog_query_uuid=uuid4(),
            is_active=True,
            marketing_url='https://example.com/ai',
        )
        product.refresh_from_db()
        self.assertEqual(product.marketing_url, 'https://example.com/ai')


# (from, to, timestamp field, whether it is set afterwards): every change the transition table allows.
ALLOWED_TOP_UP_TRANSITIONS = [
    ('created', 'requires_action', 'paid_at', False),
    ('requires_action', 'created', 'paid_at', False),
    ('created', 'paid', 'paid_at', True),
    ('requires_action', 'paid', 'paid_at', True),
    ('created', 'expired', 'paid_at', False),
    ('requires_action', 'expired', 'paid_at', False),
    ('expired', 'paid', 'paid_at', True),
    ('paid', 'deposited', 'deposited_at', True),
    ('deposited', 'fulfilled', 'fulfilled_at', True),
    ('paid', 'errored_fulfillment', 'deposited_at', False),
    ('deposited', 'errored_fulfillment', 'fulfilled_at', False),
    ('errored_fulfillment', 'paid', 'deposited_at', False),
    ('errored_fulfillment', 'deposited', 'deposited_at', True),
    ('errored_fulfillment', 'refunded', 'fulfilled_at', False),
]


# (fields that differ from a valid 100,000-cent top-up with no discount, the field the error is reported on).
INVALID_NEW_TOP_UPS = [
    ({'discount_rate': Decimal('0.1000'), 'discount_cents': 10000}, 'charge_amount_cents'),
    ({'discount_cents': 0}, 'discount_cents'),
    ({'discount_rate': Decimal('0.1000')}, 'discount_cents'),
    ({'amount_cents': 49999, 'charge_amount_cents': 49999}, 'amount_cents'),
    ({'amount_cents': 2000001, 'charge_amount_cents': 2000001}, 'amount_cents'),
    ({'discount_rate': Decimal('0.1000'), 'discount_cents': -10000, 'charge_amount_cents': 110000}, 'discount_cents'),
    ({'discount_rate': Decimal('1.0000'), 'discount_cents': 100000, 'charge_amount_cents': 0}, 'discount_cents'),
    ({'discount_rate': Decimal('-0.1000'), 'discount_cents': 0}, 'discount_rate'),
    ({'discount_rate': Decimal('1.0000'), 'discount_cents': 0}, 'discount_rate'),
    # Fractional cents pass the arithmetic but would be truncated on save to 50001 - 6170 != 43830.
    (
        {'amount_cents': 50001, 'discount_rate': Decimal('0.1234'), 'discount_cents': Decimal('6170.1234'),
         'charge_amount_cents': Decimal('43830.8766')},
        'discount_cents',
    ),
    (
        {'amount_cents': 50001, 'discount_rate': Decimal('0.1234'), 'discount_cents': Decimal('6170.1234'),
         'charge_amount_cents': Decimal('43830.8766')},
        'charge_amount_cents',
    ),
    ({'amount_cents': Decimal('100000.5'), 'charge_amount_cents': Decimal('100000.5')}, 'amount_cents'),
    # Amounts must be ints: no decimals at all, even with a zero fractional part.
    ({'amount_cents': Decimal('100000')}, 'amount_cents'),
    ({'discount_rate': Decimal('0.1000'), 'discount_cents': Decimal('10000'), 'charge_amount_cents': 90000},
     'discount_cents'),
    ({'charge_amount_cents': 100000.0}, 'charge_amount_cents'),
    # The enterprise and subsidy must match the policy's, and the policy is required.
    ({'enterprise_customer_uuid': uuid4()}, 'enterprise_customer_uuid'),
    ({'subsidy_uuid': uuid4()}, 'subsidy_uuid'),
    ({'subsidy_access_policy': None}, 'subsidy_access_policy'),
    # A new top-up starts as a quote: later states and their timestamps come only from transition_to().
    *(({'state': state}, 'state') for state in ('paid', 'deposited', 'fulfilled')),
    *(
        ({field_name: datetime.fromisoformat('2026-10-01T00:00:00+00:00')}, field_name)
        for field_name in ('paid_at', 'deposited_at', 'fulfilled_at')
    ),
]

# Amounts that are saved as given: the charge is the full amount minus the discount, and the limits are inclusive.
VALID_NEW_TOP_UP_AMOUNTS = [
    {'discount_rate': Decimal('0.1000'), 'discount_cents': 10000, 'charge_amount_cents': 90000},
    {'discount_rate': Decimal('0.0000'), 'discount_cents': 0, 'charge_amount_cents': 100000},
    {'discount_rate': None, 'discount_cents': None, 'charge_amount_cents': 100000},
    {'amount_cents': 50000, 'charge_amount_cents': 50000},
    {'amount_cents': 2000000, 'charge_amount_cents': 2000000},
]


@ddt.ddt
class TestLearnerCreditTopUp(TestCase):
    """
    Tests for the LearnerCreditTopUp model and its state machine.
    """

    def setUp(self):
        super().setUp()
        self.policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory()
        self.user = UserFactory(lms_user_id=123)

    def _create_top_up(self, **kwargs):
        return LearnerCreditTopUpFactory(subsidy_access_policy=self.policy, initiated_by=self.user, **kwargs)

    def _save_top_up(self, **overrides):
        """ Save a 100,000-cent top-up with no discount through the model, with no other factory defaults. """
        fields = {
            'subsidy_access_policy': self.policy,
            'initiated_by': self.user,
            'stripe_customer_id': 'cus_test',
            'amount_cents': 100000,
            'charge_amount_cents': 100000,
            **overrides,
        }
        return LearnerCreditTopUp.objects.create(**fields)

    def _two_copies(self, **kwargs):
        """ Create a top-up and load it twice, as two requests or tasks would. """
        self._create_top_up(**kwargs)
        return LearnerCreditTopUp.objects.get(), LearnerCreditTopUp.objects.get()

    def _latest_history(self, top_up):
        return top_up.history.latest('history_id')

    def _assert_nothing_written(self, top_up, state, history_count):
        """ Assert the instance and the stored row are both still in ``state``, with no new history record. """
        self.assertEqual(top_up.state, state)
        top_up.refresh_from_db()
        self.assertEqual(top_up.state, state)
        self.assertEqual(top_up.history.count(), history_count)

    def _fail_post_save(self):
        """ Make every LearnerCreditTopUp save fail after its UPDATE, as a failing post_save receiver would. """
        def fail(**kwargs):
            raise RuntimeError('receiver failed')
        post_save.connect(fail, sender=LearnerCreditTopUp, weak=False, dispatch_uid='test-top-up-fail')
        self.addCleanup(post_save.disconnect, sender=LearnerCreditTopUp, dispatch_uid='test-top-up-fail')

    @freeze_time('2026-10-26 12:00:00')
    def test_new_top_up_is_an_unpaid_quote(self):
        top_up = self._save_top_up()

        top_up.refresh_from_db()
        self.assertEqual(top_up.state, LearnerCreditTopUpState.CREATED)
        self.assertEqual(top_up.subsidy_uuid, self.policy.subsidy_uuid)
        self.assertEqual(top_up.enterprise_customer_uuid, self.policy.enterprise_customer_uuid)
        self.assertEqual(top_up.initiated_by_lms_user_id, 123)
        self.assertEqual(top_up.currency, 'usd')
        self.assertIsNone(top_up.discount_rate)
        self.assertIsNone(top_up.discount_cents)
        self.assertEqual(top_up.expires_at - top_up.created, timedelta(minutes=30))

    def test_top_up_without_initiating_user(self):
        top_up = self._save_top_up(initiated_by=None)

        top_up.refresh_from_db()
        self.assertIsNone(top_up.initiated_by)
        self.assertIsNone(top_up.initiated_by_lms_user_id)

    @ddt.data('enterprise_customer_uuid', 'subsidy_uuid')
    def test_policy_fields_matching_the_policy_are_accepted(self, field_name):
        policy_value = getattr(self.policy, field_name)

        top_up = self._save_top_up(**{field_name: str(policy_value)})

        top_up.refresh_from_db()
        self.assertEqual(getattr(top_up, field_name), policy_value)

    @ddt.data(*VALID_NEW_TOP_UP_AMOUNTS)
    def test_valid_amounts_are_saved(self, amounts):
        top_up = self._save_top_up(**amounts)

        top_up.refresh_from_db()
        for field_name, value in amounts.items():
            self.assertEqual(getattr(top_up, field_name), value, field_name)

    @ddt.data(*INVALID_NEW_TOP_UPS)
    @ddt.unpack
    def test_invalid_new_top_up_is_rejected(self, overrides, error_field):
        with self.assertRaises(ValidationError) as ctx:
            self._save_top_up(**overrides)

        self.assertIn(error_field, ctx.exception.message_dict)
        self.assertFalse(LearnerCreditTopUp.objects.exists())

    def test_later_policy_change_does_not_block_the_top_up(self):
        """ subsidy_uuid is a copy taken at creation: a policy re-pointed later must not strand a paid top-up. """
        top_up = self._create_top_up(state='paid')
        original_subsidy_uuid = top_up.subsidy_uuid
        SubsidyAccessPolicy.objects.filter(uuid=self.policy.uuid).update(subsidy_uuid=uuid4())

        top_up = LearnerCreditTopUp.objects.get(uuid=top_up.uuid)
        self.assertTrue(top_up.transition_to('deposited'))

        top_up.refresh_from_db()
        self.assertEqual(top_up.state, LearnerCreditTopUpState.DEPOSITED)
        self.assertEqual(top_up.subsidy_uuid, original_subsidy_uuid)

    def test_existing_top_up_is_not_rechecked(self):
        """ A paid top-up must stay valid even if the allowed range is narrowed after it was quoted. """
        top_up = self._create_top_up(state='paid')

        with mock.patch('enterprise_access.apps.customer_billing.models.LEARNER_CREDIT_TOP_UP_MAX_AMOUNT_CENTS', 60000):
            # The narrower range is in force for new top-ups...
            with self.assertRaises(ValidationError):
                self._save_top_up()
            # ...but the existing one passes clean() (as when a form or admin validates it) and can still move.
            top_up.clean()
            self.assertTrue(top_up.transition_to('deposited'))

    @ddt.data(*ALLOWED_TOP_UP_TRANSITIONS)
    @ddt.unpack
    def test_allowed_state_change(self, from_state, to_state, timestamp_field, timestamp_is_set):
        top_up = self._create_top_up(state=from_state)

        self.assertTrue(top_up.transition_to(to_state))

        top_up.refresh_from_db()
        self.assertEqual(top_up.state, to_state)
        self.assertEqual(getattr(top_up, timestamp_field) is not None, timestamp_is_set)

    @ddt.data(
        ('created', 'deposited'),
        ('created', 'fulfilled'),
        ('paid', 'fulfilled'),
        ('paid', 'created'),
        ('deposited', 'paid'),
        ('errored_fulfillment', 'fulfilled'),
        ('fulfilled', 'refunded'),
        ('fulfilled', 'paid'),
        ('refunded', 'fulfilled'),
        ('expired', 'created'),
    )
    @ddt.unpack
    def test_disallowed_state_change(self, from_state, to_state):
        top_up = self._create_top_up(state=from_state)
        history_count = top_up.history.count()

        with self.assertRaises(InvalidLearnerCreditTopUpTransition):
            top_up.transition_to(to_state)

        self._assert_nothing_written(top_up, from_state, history_count)

    def test_transition_table_allows_exactly_the_listed_changes(self):
        """
        The table is the contract the money-movement code builds on: nothing beyond the listed changes.
        Staying in the same state is always allowed.
        """
        allowed = {(from_state, to_state) for from_state, to_state, _, _ in ALLOWED_TOP_UP_TRANSITIONS}
        self.assertEqual(set(ALLOWED_LEARNER_CREDIT_TOP_UP_STATE_TRANSITIONS), set(LearnerCreditTopUpState))
        for from_state in LearnerCreditTopUpState:
            for to_state in LearnerCreditTopUpState:
                self.assertEqual(
                    LearnerCreditTopUp.is_valid_state_transition(from_state, to_state),
                    from_state == to_state or (from_state, to_state) in allowed,
                    f'{from_state} -> {to_state}',
                )

    @ddt.data(
        ('processing', {}),  # not a state
        *(
            ('paid', {field_name: None})
            for field_name in ('state', 'paid_at', 'deposited_at', 'fulfilled_at', 'amount_cents',
                               'subsidy_access_policy_id')
        ),
    )
    @ddt.unpack
    def test_transition_with_bad_arguments_writes_nothing(self, new_state, fields):
        """ An unknown state, or fields naming the state, a state timestamp or a quote field, raise ValueError. """
        top_up = self._create_top_up()
        history_count = top_up.history.count()

        with self.assertRaises(ValueError):
            top_up.transition_to(new_state, **fields)

        self._assert_nothing_written(top_up, LearnerCreditTopUpState.CREATED, history_count)
        self.assertIsNone(top_up.paid_at)

    @ddt.data({}, {'last_error': 'deposit failed'})
    def test_moving_to_current_state_does_nothing(self, fields):
        """ No write, no history record, and fields are not saved: a warning names any that differ. """
        paid_at = timezone.now() - timedelta(hours=1)
        top_up = self._create_top_up(state='paid', paid_at=paid_at)
        history_count = top_up.history.count()

        logs_context = self.assertLogs if fields else self.assertNoLogs
        with logs_context('enterprise_access.apps.customer_billing.models', level='WARNING') as logs:
            self.assertFalse(top_up.transition_to('paid', **fields))

        if fields:
            self.assertIn('last_error', logs.output[0])
        self._assert_nothing_written(top_up, LearnerCreditTopUpState.PAID, history_count)
        self.assertEqual(top_up.paid_at, paid_at)
        self.assertIsNone(top_up.last_error)

    def test_rerun_keeps_the_first_timestamp(self):
        paid_at = timezone.now() - timedelta(hours=1)
        top_up = self._create_top_up(state='errored_fulfillment', paid_at=paid_at)

        top_up.transition_to('paid')

        top_up.refresh_from_db()
        self.assertEqual(top_up.state, LearnerCreditTopUpState.PAID)
        self.assertEqual(top_up.paid_at, paid_at)

    def test_full_path_is_kept_in_history_one_record_per_change(self):
        """ Each transition saves the state, its fields and its timestamp in one write. """
        top_up = self._create_top_up()

        top_up.transition_to('paid', stripe_payment_intent_id='pi_1', payment_method_type='card')
        paid_record = self._latest_history(top_up)
        top_up.transition_to('deposited')
        top_up.transition_to('fulfilled')

        self.assertEqual(
            list(top_up.history.order_by('history_id').values_list('state', flat=True)),
            ['created', 'paid', 'deposited', 'fulfilled'],
        )
        self.assertEqual(paid_record.state, LearnerCreditTopUpState.PAID)
        self.assertEqual(paid_record.stripe_payment_intent_id, 'pi_1')
        self.assertEqual(paid_record.payment_method_type, 'card')
        self.assertIsNotNone(paid_record.paid_at)
        top_up.refresh_from_db()
        self.assertLessEqual(top_up.paid_at, top_up.deposited_at)
        self.assertLessEqual(top_up.deposited_at, top_up.fulfilled_at)

    def test_stale_copy_cannot_fulfill_twice_or_undo_fulfillment(self):
        """ transition_to() checks the stored state, so a copy loaded before another write can't act on it. """
        current, stale = self._two_copies(state='deposited')
        self.assertTrue(current.transition_to('fulfilled'))
        history_count = current.history.count()

        self.assertFalse(stale.transition_to('fulfilled'))
        self.assertEqual(stale.fulfilled_at, current.fulfilled_at)
        with self.assertRaises(InvalidLearnerCreditTopUpTransition):
            stale.transition_to('errored_fulfillment')

        self._assert_nothing_written(current, LearnerCreditTopUpState.FULFILLED, history_count)

    def test_stale_copy_records_the_stored_values_in_history(self):
        current, stale = self._two_copies(state='paid')
        deposit_uuid = uuid4()
        current.transition_to('deposited', deposit_uuid=deposit_uuid)
        current.transition_to('errored_fulfillment')

        self.assertTrue(stale.transition_to('deposited'))

        latest = self._latest_history(stale)
        self.assertEqual(latest.state, LearnerCreditTopUpState.DEPOSITED)
        self.assertEqual(latest.deposit_uuid, deposit_uuid)

    def test_partial_save_of_stale_copy_records_stored_values_in_history(self):
        """ The history record is the stored row plus this change, not the stale copy the save was called on. """
        current, stale = self._two_copies()
        current.transition_to('paid', stripe_payment_intent_id='pi_1')
        synced_at = timezone.now()
        stale.salesforce_synced_at = synced_at

        stale.save(update_fields=['salesforce_synced_at'])

        latest = self._latest_history(stale)
        self.assertEqual(latest.state, LearnerCreditTopUpState.PAID)
        self.assertEqual(latest.stripe_payment_intent_id, 'pi_1')
        self.assertEqual(latest.paid_at, current.paid_at)
        self.assertEqual(latest.salesforce_synced_at, synced_at)
        self.assertEqual(stale.state, LearnerCreditTopUpState.PAID)

    def test_partial_save_does_not_record_unsaved_edits(self):
        top_up = self._create_top_up()
        top_up.amount_cents = 999
        top_up.last_error = 'deposit failed'

        top_up.save(update_fields=['last_error'])

        latest = self._latest_history(top_up)
        self.assertEqual(latest.amount_cents, 100000)
        self.assertEqual(latest.last_error, 'deposit failed')
        self.assertEqual(top_up.amount_cents, 100000)

    @ddt.data(
        ('transition_to', 'reused_payment_intent'),
        ('transition_to', 'failing_receiver'),
        ('save', 'reused_payment_intent'),
        ('save', 'failing_receiver'),
    )
    @ddt.unpack
    def test_failed_write_writes_nothing_and_restores_the_instance(self, method, failure):
        """
        Whether a unique-index violation or a failure after the UPDATE (a post_save receiver) stops the write,
        nothing is written and the instance holds the stored values.
        """
        self._create_top_up(stripe_payment_intent_id='pi_1')
        top_up = self._create_top_up()
        modified = top_up.modified
        history_count = top_up.history.count()
        if failure == 'failing_receiver':
            self._fail_post_save()
            payment_intent_id, error = 'pi_2', RuntimeError
        else:
            payment_intent_id, error = 'pi_1', IntegrityError

        with self.assertRaises(error):
            if method == 'transition_to':
                top_up.transition_to('paid', stripe_payment_intent_id=payment_intent_id)
            else:
                top_up.stripe_payment_intent_id = payment_intent_id
                top_up.save(update_fields=['stripe_payment_intent_id'])

        self.assertIsNone(top_up.stripe_payment_intent_id)
        self.assertIsNone(top_up.paid_at)
        self.assertEqual(top_up.modified, modified)
        self._assert_nothing_written(top_up, LearnerCreditTopUpState.CREATED, history_count)
        self.assertIsNone(top_up.stripe_payment_intent_id)

    @ddt.data(
        None, 'state', 'paid_at', 'amount_cents', 'discount_cents', 'charge_amount_cents', 'subsidy_uuid',
        'subsidy_access_policy_id', 'enterprise_customer_uuid', 'stripe_customer_id',
    )
    def test_save_of_existing_top_up_is_refused(self, field_name):
        """
        An existing top-up is saved only with update_fields, which can't name the state, a state timestamp or a
        quote field. A full save (field_name None) of a stale copy would write its old state back.
        """
        top_up = self._create_top_up()
        top_up.last_error = 'error'

        with self.assertRaises(ValueError):
            top_up.save(update_fields=None if field_name is None else [field_name])

        top_up.refresh_from_db()
        self.assertIsNone(top_up.last_error)

    @ddt.data((['salesforce_synced_at'], True), ([], False))
    @ddt.unpack
    def test_partial_save_writes_only_update_fields(self, update_fields, is_saved):
        """ As in Django, an empty update_fields saves nothing: modified isn't written on its own. """
        top_up = self._create_top_up()
        modified = top_up.modified
        synced_at = timezone.now()
        top_up.salesforce_synced_at = synced_at

        with freeze_time(synced_at + timedelta(minutes=1)):
            top_up.save(update_fields=update_fields)

        top_up.refresh_from_db()
        self.assertEqual(top_up.salesforce_synced_at, synced_at if is_saved else None)
        self.assertEqual(top_up.modified, synced_at + timedelta(minutes=1) if is_saved else modified)

    def test_payment_intent_belongs_to_one_top_up(self):
        self._create_top_up(stripe_payment_intent_id='pi_1')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._create_top_up(stripe_payment_intent_id='pi_1')

        self._create_top_up(stripe_payment_intent_id=None)
        self._create_top_up(stripe_payment_intent_id=None)
        self.assertEqual(LearnerCreditTopUp.objects.filter(stripe_payment_intent_id__isnull=True).count(), 2)

    def test_budget_with_a_top_up_cannot_be_deleted(self):
        top_up = self._create_top_up()

        # SubsidyAccessPolicy.delete() is a soft delete; a queryset delete is a hard one.
        with self.assertRaises(ProtectedError):
            SubsidyAccessPolicy.objects.filter(uuid=self.policy.uuid).delete()

        self.assertTrue(SubsidyAccessPolicy.objects.filter(uuid=self.policy.uuid).exists())
        self.assertTrue(LearnerCreditTopUp.objects.filter(uuid=top_up.uuid).exists())

    def test_retiring_the_initiating_user_keeps_the_record(self):
        top_up = self._create_top_up()

        self.user.delete()

        top_up.refresh_from_db()
        self.assertIsNone(top_up.initiated_by)
        self.assertEqual(top_up.initiated_by_lms_user_id, 123)

    def test_str(self):
        top_up = self._create_top_up(state='paid')

        self.assertEqual(
            str(top_up),
            f'<LearnerCreditTopUp uuid={top_up.uuid}, subsidy_access_policy={self.policy.pk}, '
            'state=paid, amount_cents=100000>',
        )

    def test_indexes(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, LearnerCreditTopUp._meta.db_table)

        indexed_columns = {
            tuple(constraint['columns'])
            for constraint in constraints.values()
            if constraint['index'] or constraint['unique']
        }
        for columns in (
            ('enterprise_customer_uuid',),
            ('subsidy_uuid',),
            ('state',),
            ('stripe_customer_id',),
            ('stripe_payment_intent_id',),
            ('subsidy_access_policy_id', 'state'),
        ):
            self.assertIn(columns, indexed_columns)


class TestEnterpriseStripeCustomer(TestCase):
    """
    Tests for the EnterpriseStripeCustomer model (ENT-12509).
    """

    def test_one_row_per_enterprise_but_stripe_customers_can_be_shared(self):
        """
        An enterprise can't be mapped twice, but two enterprises can share a Stripe customer: checkout reuses the
        Stripe customer that has the admin's email.
        """
        mapping = EnterpriseStripeCustomerFactory(stripe_customer_id='cus_shared')
        EnterpriseStripeCustomerFactory(stripe_customer_id='cus_shared')

        with self.assertRaises(IntegrityError), transaction.atomic():
            EnterpriseStripeCustomerFactory(enterprise_customer_uuid=mapping.enterprise_customer_uuid)

        self.assertEqual(EnterpriseStripeCustomer.objects.filter(stripe_customer_id='cus_shared').count(), 2)
        self.assertEqual(
            str(mapping),
            f'<EnterpriseStripeCustomer enterprise_customer_uuid={mapping.enterprise_customer_uuid}, '
            'stripe_customer_id=cus_shared>',
        )
