"""
Management command to backfill SelfServiceSubscriptionRenewal records from existing provisioning workflows.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from enterprise_access.apps.customer_billing.models import (
    CheckoutIntent,
    SelfServiceSubscriptionRenewal,
    StripeEventSummary
)
from enterprise_access.apps.provisioning.models import (
    GetCreateSubscriptionPlanRenewalStep,
    ProvisionNewCustomerWorkflow
)


class Command(BaseCommand):
    """
    Command to backfill SelfServiceSubscriptionRenewal records from existing provisioning workflows.

    This command finds completed provisioning workflows that have subscription plan renewals
    but no corresponding SelfServiceSubscriptionRenewal tracking records, and creates them.
    """
    help = 'Backfill SelfServiceSubscriptionRenewal records from existing provisioning workflows'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Number of records to process in each batch (default: 100)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be processed without actually creating records',
        )
        parser.add_argument(
            '--workflow-uuid',
            type=str,
            help='Only process a specific workflow UUID',
        )

    def handle(self, *args, **options):
        """
        Execute the backfill command.
        """
        batch_size = options['batch_size']
        dry_run = options['dry_run']
        workflow_uuid = options['workflow_uuid']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No records will be created'))

        # Find completed provisioning workflows that have subscription renewals
        workflows_queryset = ProvisionNewCustomerWorkflow.objects.filter(
            succeeded_at__isnull=False,
        ).select_related('checkoutintent')

        if workflow_uuid:
            workflows_queryset = workflows_queryset.filter(uuid=workflow_uuid)

        total_workflows = workflows_queryset.count()
        self.stdout.write(f'Found {total_workflows} completed provisioning workflows')

        created_count = 0
        skipped_count = 0
        error_count = 0

        # Process workflows in batches
        for i in range(0, total_workflows, batch_size):
            batch_workflows = workflows_queryset[i:i + batch_size]

            for workflow in batch_workflows:
                try:
                    result = self._handle_workflow(workflow, dry_run)
                    if result == 'created':
                        created_count += 1
                    elif result == 'skipped':
                        skipped_count += 1
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    error_count += 1
                    self.stderr.write(
                        self.style.ERROR(
                            f'Error processing workflow {workflow.uuid}: {exc}'
                        )
                    )

            # Progress update
            processed = min(i + batch_size, total_workflows)
            self.stdout.write(f'Processed {processed}/{total_workflows} workflows...')

        # Summary
        self.stdout.write(
            self.style.SUCCESS(
                f'\nBackfill complete! Created: {created_count}, '
                f'Skipped: {skipped_count}, Errors: {error_count}'
            )
        )

    def _handle_workflow(self, workflow: ProvisionNewCustomerWorkflow, dry_run: bool) -> str:
        """
        Process a single workflow to create missing SelfServiceSubscriptionRenewal records.

        Returns:
            str: 'created', 'skipped', or raises exception
        """
        # Check if this workflow has a linked CheckoutIntent
        try:
            checkout_intent = workflow.checkoutintent
        except CheckoutIntent.DoesNotExist:
            return 'skipped'

        # Find the renewal step for this workflow
        renewal_step = GetCreateSubscriptionPlanRenewalStep.objects.filter(
            workflow_record_uuid=workflow.uuid
        ).first()

        if not renewal_step or not renewal_step.output_object:
            return 'skipped'

        renewal_id = renewal_step.output_object.id

        # Check if SelfServiceSubscriptionRenewal already exists
        existing_renewal = SelfServiceSubscriptionRenewal.objects.filter(
            checkout_intent=checkout_intent,
            subscription_plan_renewal_id=renewal_id
        ).first()

        if existing_renewal:
            return 'skipped'

        if dry_run:
            self.stdout.write(
                f'Would create SelfServiceSubscriptionRenewal for '
                f'checkout_intent {checkout_intent.id}, renewal {renewal_id}'
            )
            return 'created'

        stripe_subscription_id = None
        latest_summary = StripeEventSummary.get_latest_for_checkout_intent(
            checkout_intent,
            stripe_subscription_id__isnull=False,
        )
        if latest_summary:
            stripe_subscription_id = latest_summary.stripe_subscription_id

        with transaction.atomic():
            SelfServiceSubscriptionRenewal.objects.create(
                checkout_intent=checkout_intent,
                subscription_plan_renewal_id=renewal_id,
                stripe_subscription_id=stripe_subscription_id,
            )

        self.stdout.write(
            f'Created SelfServiceSubscriptionRenewal for '
            f'checkout_intent {checkout_intent.id}, renewal {renewal_id}'
        )
        return 'created'
