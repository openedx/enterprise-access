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

    def _write(self, msg):
        self.stdout.write(msg)

    def _write_warning(self, msg):
        self._write(self.style.WARNING(msg))

    def _write_error(self, msg):
        self.stderr.write(self.style.ERROR(msg))

    def _write_success(self, msg):
        self._write(self.style.SUCCESS(msg))

    def handle(self, *args, **options):
        """
        Execute the backfill command.
        """
        batch_size = options['batch_size']
        dry_run = options['dry_run']

        if dry_run:
            self._write_warning('DRY RUN MODE - No records will be created')

        # Start with successful GetCreateSubscriptionPlanRenewalSteps
        steps_queryset = GetCreateSubscriptionPlanRenewalStep.objects.filter(
            succeeded_at__isnull=False,
            output_data__isnull=False,
            workflow_record_uuid__isnull=False,
        )

        total_steps = steps_queryset.count()
        self._write(f'Found {total_steps} successful subscription renewal steps')

        created_count = 0
        skipped_count = 0
        error_count = 0

        for i in range(0, total_steps, batch_size):
            batch_steps = steps_queryset[i:i + batch_size]

            for step in batch_steps:
                try:
                    result = self._handle_renewal_step(step, dry_run)
                    if result == 'created':
                        created_count += 1
                    elif result == 'skipped':
                        skipped_count += 1
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    error_count += 1
                    self._write_error(f'Error processing renewal step {step.uuid}: {exc}')

            # Progress update
            processed = min(i + batch_size, total_steps)
            self._write(f'Processed {processed}/{total_steps} renewal steps...')

        self._write_success(
            f'\nBackfill complete! Created: {created_count}, '
            f'Skipped: {skipped_count}, Errors: {error_count}'
        )

    def _handle_renewal_step(self, step: GetCreateSubscriptionPlanRenewalStep, dry_run: bool) -> str:
        """
        Process a single renewal step to create missing SelfServiceSubscriptionRenewal records.

        Returns:
            str: 'created', 'skipped', or raises exception
        """
        # Find the related workflow for this step
        try:
            workflow = ProvisionNewCustomerWorkflow.objects.get(uuid=step.workflow_record_uuid)
        except ProvisionNewCustomerWorkflow.DoesNotExist:
            return 'skipped'

        # Check if there's a checkout intent on the related workflow
        try:
            checkout_intent = workflow.checkoutintent
        except CheckoutIntent.DoesNotExist:
            return 'skipped'

        renewal_id = step.output_object.id

        # Check if SelfServiceSubscriptionRenewal already exists for this checkout intent
        existing_renewal = SelfServiceSubscriptionRenewal.objects.filter(
            checkout_intent=checkout_intent,
            subscription_plan_renewal_id=renewal_id
        ).first()

        if existing_renewal:
            return 'skipped'

        if dry_run:
            self._write(
                f'Would create SelfServiceSubscriptionRenewal for '
                f'checkout_intent {checkout_intent.id}, renewal {renewal_id}'
            )
            return 'created'

        # Get the latest summary and then create a SelfServiceSubscriptionRenewal record
        latest_summary = StripeEventSummary.get_latest_for_checkout_intent(
            checkout_intent,
            stripe_subscription_id__isnull=False,
            stripe_event_data__isnull=False
        )
        if not latest_summary:
            raise Exception(f'No summary for checkout intent {checkout_intent}')

        with transaction.atomic():
            SelfServiceSubscriptionRenewal.objects.create(
                checkout_intent=checkout_intent,
                subscription_plan_renewal_id=renewal_id,
                stripe_event_data=latest_summary.stripe_event_data,
                stripe_subscription_id=latest_summary.stripe_subscription_id,
            )

        self._write(
            f'Created SelfServiceSubscriptionRenewal for '
            f'checkout_intent {checkout_intent.id}, renewal {renewal_id}'
        )
        return 'created'
