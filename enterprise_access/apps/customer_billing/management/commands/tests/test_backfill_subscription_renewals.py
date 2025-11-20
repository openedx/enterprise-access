"""
Tests for the backfill_subscription_renewals management command.
"""
import uuid
from io import StringIO
from unittest import mock
from uuid import uuid4

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.models import CheckoutIntent, SelfServiceSubscriptionRenewal
from enterprise_access.apps.customer_billing.tests.factories import (
    CheckoutIntentFactory,
    StripeEventDataFactory,
    StripeEventSummaryFactory
)
from enterprise_access.apps.provisioning.models import GetCreateSubscriptionPlanRenewalStep
from enterprise_access.apps.provisioning.tests.factories import ProvisionNewCustomerWorkflowFactory


class TestBackfillSubscriptionRenewalsCommand(TestCase):
    """Tests for the backfill_subscription_renewals management command."""

    RENEWAL_BOILERPLATE = {
        'prior_subscription_plan': uuid4(),
        'renewed_subscription_plan': uuid4(),
        'number_of_licenses': 15,
        'effective_date': timezone.now(),
        'renewed_expiration_date': timezone.now(),
    }

    def setUp(self):
        self.user = UserFactory()

    def tearDown(self):
        """Clean up test data."""
        SelfServiceSubscriptionRenewal.objects.all().delete()
        CheckoutIntent.objects.all().delete()

    def _create_renewal_step(self, workflow, output_data=None, **model_kwargs):
        """ Helper to create a renewal workflow step. """
        return GetCreateSubscriptionPlanRenewalStep.objects.create(
            workflow_record_uuid=workflow.uuid,
            input_data={},
            output_data={
                'id': 123,
                'title': 'Test Renewal',
                'created': '2024-01-15T10:30:00Z',
                'start_date': '2025-01-01T00:00:00Z',
                'expiration_date': '2026-01-01T00:00:00Z',
                **self.RENEWAL_BOILERPLATE,
                **(output_data or {}),
            },
            succeeded_at=workflow.created,
            **model_kwargs,
        )

    def test_backfill_finds_missing_renewals(self):
        """Test that the command finds and creates missing renewal records."""
        # Create a workflow with a completed renewal step but no tracking record
        workflow = ProvisionNewCustomerWorkflowFactory()
        checkout_intent = CheckoutIntentFactory(user=self.user, workflow=workflow)
        event_data = StripeEventDataFactory.create(checkout_intent=checkout_intent)
        summary = event_data.summary
        summary.subscription_status = 'trialing',
        summary.stripe_subscription_id = 'sub_test_789'
        summary.save()

        renewal_step = self._create_renewal_step(workflow)

        # Verify no renewal record exists initially
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 0)

        # Run the command
        out = StringIO()
        call_command('backfill_subscription_renewals', stdout=out)

        # Verify renewal record was created
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 1)

        renewal_record = SelfServiceSubscriptionRenewal.objects.first()
        self.assertEqual(renewal_record.checkout_intent, checkout_intent)
        self.assertEqual(renewal_record.subscription_plan_renewal_id, 123)
        self.assertEqual(renewal_record.stripe_subscription_id, summary.stripe_subscription_id)
        self.assertIsNone(renewal_record.processed_at)

    def test_backfill_dry_run_mode(self):
        """Test that dry run mode reports but doesn't create records."""
        # Create a workflow with completed renewal step
        workflow = ProvisionNewCustomerWorkflowFactory()
        checkout_intent = CheckoutIntentFactory(user=self.user, workflow=workflow)
        summary = StripeEventSummaryFactory(
            checkout_intent=checkout_intent,
            subscription_status='trialing',
            stripe_subscription_id='sub_test_789'
        )

        renewal_step = self._create_renewal_step(workflow)

        # Verify no renewal record exists initially
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 0)

        # Run the command in dry run mode
        out = StringIO()
        call_command('backfill_subscription_renewals', '--dry-run', stdout=out)

        # Verify no renewal record was created (dry run)
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 0)

    def test_backfill_handles_errors_gracefully(self):
        """Test that the command handles errors gracefully."""
        # Create a workflow with completed renewal step, but no summary, which should raise an error
        workflow = ProvisionNewCustomerWorkflowFactory()
        checkout_intent = CheckoutIntentFactory(
            user=self.user,
            workflow=workflow,
            enterprise_slug='good-enterprise'
        )

        self._create_renewal_step(workflow)

        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 0)

        out = StringIO()
        err = StringIO()
        call_command('backfill_subscription_renewals', stdout=out, stderr=err)

        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 0)

        # Verify error was logged but processing continued
        error_output = err.getvalue()
        self.assertIn('Error processing renewal step', error_output)

    def test_backfill_no_missing_renewals(self):
        """Test command behavior when no missing renewals are found."""
        # Create a workflow with renewal step and existing tracking record
        workflow = ProvisionNewCustomerWorkflowFactory()
        checkout_intent = CheckoutIntentFactory(user=self.user, workflow=workflow)
        summary = StripeEventSummaryFactory(
            checkout_intent=checkout_intent,
            subscription_status='trialing',
            stripe_subscription_id='sub_test_789'
        )

        renewal_step = self._create_renewal_step(workflow)

        expected_renewal_id = 789
        SelfServiceSubscriptionRenewal.objects.create(
            checkout_intent=checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id,
            stripe_subscription_id='sub_existing_123',
            stripe_event_data=summary.stripe_event_data,
        )

        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 1)

        # Run the command
        out = StringIO()
        call_command('backfill_subscription_renewals', stdout=out)

        # Verify no additional renewal records were created
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 1)

    def test_backfill_only_processes_completed_steps(self):
        """Test that only completed renewal steps are processed."""
        # Create workflow with uncompleted renewal step
        workflow = ProvisionNewCustomerWorkflowFactory()
        checkout_intent = CheckoutIntentFactory(user=self.user, workflow=workflow)

        GetCreateSubscriptionPlanRenewalStep.objects.create(
            workflow_record_uuid=workflow.uuid,
            input_data={'title': 'Incomplete Renewal'},
            succeeded_at=None,
        )

        # Verify no renewal records exist initially
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 0)

        # Run the command
        out = StringIO()
        call_command('backfill_subscription_renewals', stdout=out)

        # Verify no renewal record was created for uncompleted step
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 0)
