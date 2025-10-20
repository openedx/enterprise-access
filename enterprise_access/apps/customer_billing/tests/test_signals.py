"""
Tests for the ``enterprise_access.customer_billing.signals`` module.
"""
from decimal import Decimal

from django.test import TestCase

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.models import CheckoutIntent, StripeEventData, StripeEventSummary


class TestStripeEventDataSignals(TestCase):
    """Test cases for StripeEventData signal handlers."""

    def setUp(self):
        self.user = UserFactory()
        self.checkout_intent = CheckoutIntent.create_intent(
            user=self.user,
            slug='test-enterprise',
            name='Test Enterprise',
            quantity=10
        )

    def test_create_stripe_event_summary_on_create(self):
        """Test that StripeEventSummary is automatically created when StripeEventData is created."""
        # Mock event data for an invoice
        invoice_event_data = {
            'id': 'evt_test_auto_create',
            'type': 'invoice.paid',
            'data': {
                'object': {
                    'object': 'invoice',
                    'id': 'in_test_auto',
                    'subscription': 'sub_test_auto',
                    'amount_paid': 1500,
                    'currency': 'usd',
                    'lines': {
                        'data': [
                            {
                                'quantity': 5,
                                'pricing': {
                                    'unit_amount_decimal': '300.0'
                                }
                            }
                        ]
                    },
                    'parent': {
                        'subscription_details': {
                            'subscription': 'sub_test_auto'
                        }
                    }
                }
            }
        }

        # Verify no summary exists initially
        self.assertEqual(StripeEventSummary.objects.count(), 0)

        # Create StripeEventData - this should trigger the signal
        stripe_event_data = StripeEventData.objects.create(
            event_id='evt_test_auto_create',
            event_type='invoice.paid',
            checkout_intent=self.checkout_intent,
            data=invoice_event_data
        )

        # Verify that StripeEventSummary was automatically created
        self.assertEqual(StripeEventSummary.objects.count(), 1)

        summary = StripeEventSummary.objects.get(stripe_event_data=stripe_event_data)

        # Verify the summary was populated correctly
        self.assertEqual(summary.event_id, 'evt_test_auto_create')
        self.assertEqual(summary.event_type, 'invoice.paid')
        self.assertEqual(summary.checkout_intent, self.checkout_intent)
        self.assertEqual(summary.stripe_object_type, 'invoice')
        self.assertEqual(summary.stripe_invoice_id, 'in_test_auto')
        self.assertEqual(summary.stripe_subscription_id, 'sub_test_auto')
        self.assertEqual(summary.invoice_amount_paid, 1500)
        self.assertEqual(summary.invoice_currency, 'usd')
        self.assertEqual(summary.invoice_unit_amount_decimal, Decimal(300.0))
        self.assertEqual(summary.invoice_quantity, 5)

    def test_update_stripe_event_summary_on_update(self):
        """Test that StripeEventSummary is updated when StripeEventData is updated."""
        # Create initial StripeEventData
        initial_event_data = {
            'id': 'evt_test_update',
            'type': 'invoice.paid',
            'data': {
                'object': {
                    'object': 'invoice',
                    'id': 'in_test_update',
                    'subscription': 'sub_test_update',
                    'amount_paid': 1000,
                    'currency': 'usd',
                    'lines': {
                        'data': [
                            {
                                'quantity': 4,
                                'pricing': {
                                    'unit_amount_decimal': '250.0'
                                }
                            }
                        ]
                    },
                    'parent': {
                        'subscription_details': {
                            'subscription': 'sub_test_update'
                        }
                    }
                }
            }
        }

        stripe_event_data = StripeEventData.objects.create(
            event_id='evt_test_update',
            event_type='invoice.paid',
            checkout_intent=self.checkout_intent,
            data=initial_event_data
        )

        # Verify initial summary was created
        summary = StripeEventSummary.objects.get(stripe_event_data=stripe_event_data)
        self.assertEqual(summary.invoice_amount_paid, 1000)
        self.assertEqual(summary.invoice_quantity, 4)

        # Update the StripeEventData with new data
        updated_event_data = {
            'id': 'evt_test_update',
            'type': 'invoice.paid',
            'data': {
                'object': {
                    'object': 'invoice',
                    'id': 'in_test_update',
                    'subscription': 'sub_test_update',
                    'amount_paid': 2000,  # Updated amount
                    'currency': 'usd',
                    'lines': {
                        'data': [
                            {
                                'quantity': 8,  # Updated quantity
                                'pricing': {
                                    'unit_amount_decimal': '250.0'
                                }
                            }
                        ]
                    },
                    'parent': {
                        'subscription_details': {
                            'subscription': 'sub_test_update'
                        }
                    }
                }
            }
        }

        stripe_event_data.data = updated_event_data
        stripe_event_data.save()  # This should trigger the signal to update summary

        # Verify the summary was updated
        summary.refresh_from_db()
        self.assertEqual(summary.invoice_amount_paid, 2000)
        self.assertEqual(summary.invoice_quantity, 8)
