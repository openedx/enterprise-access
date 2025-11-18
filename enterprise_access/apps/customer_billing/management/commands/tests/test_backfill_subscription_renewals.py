"""
Tests for the backfill_subscription_renewals management command.
"""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.models import CheckoutIntent, SelfServiceSubscriptionRenewal
from enterprise_access.apps.customer_billing.tests.factories import CheckoutIntentFactory
from enterprise_access.apps.provisioning.models import GetCreateSubscriptionPlanRenewalStep
from enterprise_access.apps.provisioning.tests.factories import ProvisionNewCustomerWorkflowFactory


class TestBackfillSubscriptionRenewalsCommand(TestCase):
    """Tests for the backfill_subscription_renewals management command."""

    def setUp(self):
        self.user = UserFactory()

    def tearDown(self):
        """Clean up test data."""
        SelfServiceSubscriptionRenewal.objects.all().delete()
        CheckoutIntent.objects.all().delete()

    def test_backfill_finds_missing_renewals(self):
        """Test that the command finds and creates missing renewal records."""
        # Create a workflow with a completed renewal step but no tracking record
        workflow = ProvisionNewCustomerWorkflowFactory()
        checkout_intent = CheckoutIntentFactory(user=self.user, workflow=workflow)

        # Create a completed renewal step
        renewal_step = GetCreateSubscriptionPlanRenewalStep.objects.create(
            workflow_record_uuid=workflow.uuid,
            input_data={
                'title': 'Test Renewal',
                'salesforce_opportunity_line_item': 'test-oli-123',
                'start_date': '2025-01-01T00:00:00Z',
                'expiration_date': '2026-01-01T00:00:00Z',
                'desired_num_licenses': 5,
            },
            output_data={
                'id': 123,
                'title': 'Test Renewal',
                'created': '2024-01-15T10:30:00Z',
                'start_date': '2025-01-01T00:00:00Z',
                'expiration_date': '2026-01-01T00:00:00Z',
            },
            completed_at=workflow.created  # Mark as completed
        )

        # Verify no renewal record exists initially
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 0)

        # Run the command
        out = StringIO()
        call_command('backfill_subscription_renewals', stdout=out)

        # Verify renewal record was created
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 1)
        
        renewal_record = SelfServiceSubscriptionRenewal.objects.first()
        self.assertEqual(renewal_record.checkout_intent, checkout_intent)
        import uuid
        expected_renewal_id = uuid.UUID('00000000-0000-0000-0000-000000000123')
        self.assertEqual(renewal_record.subscription_plan_renewal_id, expected_renewal_id)
        self.assertEqual(renewal_record.stripe_subscription_id, '')
        self.assertIsNone(renewal_record.processed_at)

        # Verify command output
        output = out.getvalue()
        self.assertIn('Found 1 workflows with missing renewal records', output)
        self.assertIn('Successfully created 1 renewal records', output)

    def test_backfill_multiple_workflows(self):
        """Test backfilling multiple workflows with missing renewals."""
        renewal_data = []
        
        # Create multiple workflows with completed renewal steps
        for i in range(3):
            workflow = ProvisionNewCustomerWorkflowFactory()
            checkout_intent = CheckoutIntentFactory(
                user=UserFactory(), 
                workflow=workflow,
                enterprise_slug=f'enterprise-{i}',
                enterprise_name=f'Enterprise {i}'
            )

            renewal_step = GetCreateSubscriptionPlanRenewalStep.objects.create(
                workflow_record_uuid=workflow.uuid,
                input_data={'title': f'Renewal {i}'},
                output_data={
                    'id': 100 + i,
                    'title': f'Renewal {i}',
                    'created': '2024-01-15T10:30:00Z',
                },
                completed_at=workflow.created
            )
            
            renewal_data.append((checkout_intent, 100 + i))

        # Verify no renewal records exist initially
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 0)

        # Run the command
        out = StringIO()
        call_command('backfill_subscription_renewals', stdout=out)

        # Verify all renewal records were created
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 3)
        
        for i, (checkout_intent, renewal_id) in enumerate(renewal_data):
            import uuid
            expected_renewal_id = uuid.UUID(f'00000000-0000-0000-0000-{100 + i:012d}')
            renewal_record = SelfServiceSubscriptionRenewal.objects.get(
                checkout_intent=checkout_intent,
                subscription_plan_renewal_id=expected_renewal_id
            )
            self.assertIsNone(renewal_record.processed_at)

        # Verify command output
        output = out.getvalue()
        self.assertIn('Found 3 workflows with missing renewal records', output)
        self.assertIn('Successfully created 3 renewal records', output)

    def test_backfill_dry_run_mode(self):
        """Test that dry run mode reports but doesn't create records."""
        # Create a workflow with completed renewal step
        workflow = ProvisionNewCustomerWorkflowFactory()
        checkout_intent = CheckoutIntentFactory(user=self.user, workflow=workflow)

        GetCreateSubscriptionPlanRenewalStep.objects.create(
            workflow_record_uuid=workflow.uuid,
            input_data={'title': 'Test Renewal'},
            output_data={
                'id': 456,
                'title': 'Test Renewal',
                'created': '2024-01-15T10:30:00Z',
            },
            completed_at=workflow.created
        )

        # Verify no renewal record exists initially
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 0)

        # Run the command in dry run mode
        out = StringIO()
        call_command('backfill_subscription_renewals', '--dry-run', stdout=out)

        # Verify no renewal record was created (dry run)
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 0)

        # Verify command output shows what would be done
        output = out.getvalue()
        self.assertIn('DRY RUN MODE', output)
        self.assertIn('Found 1 workflows with missing renewal records', output)
        self.assertIn('Would create renewal records', output)

    def test_backfill_handles_errors_gracefully(self):
        """Test that the command handles errors gracefully and continues processing."""
        # Create a workflow with completed renewal step
        workflow1 = ProvisionNewCustomerWorkflowFactory()
        checkout_intent1 = CheckoutIntentFactory(
            user=self.user, 
            workflow=workflow1,
            enterprise_slug='good-enterprise'
        )

        GetCreateSubscriptionPlanRenewalStep.objects.create(
            workflow_record_uuid=workflow1.uuid,
            input_data={'title': 'Good Renewal'},
            output_data={
                'id': 111,
                'title': 'Good Renewal',
                'created': '2024-01-15T10:30:00Z',
            },
            completed_at=workflow1.created
        )

        # Create another workflow but delete its checkout intent to simulate error
        workflow2 = ProvisionNewCustomerWorkflowFactory()
        checkout_intent2 = CheckoutIntentFactory(
            user=UserFactory(), 
            workflow=workflow2,
            enterprise_slug='bad-enterprise'
        )
        checkout_intent2_id = checkout_intent2.id
        checkout_intent2.delete()  # This will cause an error during backfill

        GetCreateSubscriptionPlanRenewalStep.objects.create(
            workflow_record_uuid=workflow2.uuid,
            input_data={'title': 'Bad Renewal'},
            output_data={
                'id': 222,
                'title': 'Bad Renewal',
                'created': '2024-01-15T10:30:00Z',
            },
            completed_at=workflow2.created
        )

        # Verify no renewal records exist initially
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 0)

        # Run the command
        out = StringIO()
        err = StringIO()
        call_command('backfill_subscription_renewals', stdout=out, stderr=err)

        # Verify the good record was created despite the error
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 1)
        renewal_record = SelfServiceSubscriptionRenewal.objects.first()
        self.assertEqual(renewal_record.checkout_intent, checkout_intent1)
        import uuid
        expected_renewal_id = uuid.UUID('00000000-0000-0000-0000-000000000111')
        self.assertEqual(renewal_record.subscription_plan_renewal_id, expected_renewal_id)

        # Verify error was logged but processing continued
        output = out.getvalue()
        error_output = err.getvalue()
        self.assertIn('Found 2 workflows with missing renewal records', output)
        self.assertIn('Successfully created 1 renewal records', output)
        self.assertIn('Failed to create 1 renewal records', output)

    def test_backfill_no_missing_renewals(self):
        """Test command behavior when no missing renewals are found."""
        # Create a workflow with renewal step and existing tracking record
        workflow = ProvisionNewCustomerWorkflowFactory()
        checkout_intent = CheckoutIntentFactory(user=self.user, workflow=workflow)

        GetCreateSubscriptionPlanRenewalStep.objects.create(
            workflow_record_uuid=workflow.uuid,
            input_data={'title': 'Test Renewal'},
            output_data={
                'id': 789,
                'title': 'Test Renewal',
                'created': '2024-01-15T10:30:00Z',
            },
            completed_at=workflow.created
        )

        # Create existing renewal record
        import uuid
        expected_renewal_id = uuid.UUID('00000000-0000-0000-0000-000000000789')
        SelfServiceSubscriptionRenewal.objects.create(
            checkout_intent=checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id,
            stripe_subscription_id='sub_existing_123'
        )

        # Run the command
        out = StringIO()
        call_command('backfill_subscription_renewals', stdout=out)

        # Verify no additional renewal records were created
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 1)

        # Verify command output
        output = out.getvalue()
        self.assertIn('Found 0 workflows with missing renewal records', output)
        self.assertIn('No backfill needed', output)

    def test_backfill_only_processes_completed_steps(self):
        """Test that only completed renewal steps are processed."""
        # Create workflow with uncompleted renewal step
        workflow = ProvisionNewCustomerWorkflowFactory()
        checkout_intent = CheckoutIntentFactory(user=self.user, workflow=workflow)

        GetCreateSubscriptionPlanRenewalStep.objects.create(
            workflow_record_uuid=workflow.uuid,
            input_data={'title': 'Incomplete Renewal'},
            output_data={},  # No output data since not completed
            # No completed_at timestamp - step is not completed
        )

        # Verify no renewal records exist initially
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 0)

        # Run the command
        out = StringIO()
        call_command('backfill_subscription_renewals', stdout=out)

        # Verify no renewal record was created for uncompleted step
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.count(), 0)

        # Verify command output
        output = out.getvalue()
        self.assertIn('Found 0 workflows with missing renewal records', output)