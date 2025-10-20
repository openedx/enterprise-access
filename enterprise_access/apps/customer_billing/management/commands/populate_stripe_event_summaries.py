"""
Management command to populate StripeEventSummary records from existing StripeEventData.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from enterprise_access.apps.customer_billing.models import StripeEventData, StripeEventSummary


class Command(BaseCommand):
    """
    Command to backpopulate StripeEventSummary records from existing StripeEventData.

    This command creates normalized summary records for existing Stripe events,
    extracting key fields for easier querying and API access.
    """
    help = 'Populate StripeEventSummary records from existing StripeEventData'

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
            '--event-type',
            type=str,
            help='Only process events of this specific type (e.g., "invoice.paid")',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Recreate summary records even if they already exist',
        )

    def _execute_dry_run(self, queryset, total_count):
        """
        Helper to execute a sample of records for a dry run.
        """
        self.stdout.write(self.style.WARNING('DRY RUN - Would process:'))

        # Show sample of records that would be processed
        sample_records = queryset[:min(10, total_count)]
        for event_data in sample_records:
            checkout_intent_info = ""
            if event_data.checkout_intent:
                checkout_intent_info = f", CheckoutIntent: {event_data.checkout_intent.id}"

            self.stdout.write(
                f"  - Event: {event_data.event_id} ({event_data.event_type})"
                f"{checkout_intent_info}"
            )

        if total_count > 10:
            self.stdout.write(f"  ... and {total_count - 10} more")

    def _process_summary_batch(self, queryset, index, batch_size, force):
        """
        Helper to process a batch of records from the given queryset.
        """
        batch_queryset = queryset[index:index + batch_size]
        batch_records = list(batch_queryset)
        created_count, updated_count, error_count = (0, 0, 0)

        with transaction.atomic():
            for event_data in batch_records:
                try:
                    if force and hasattr(event_data, 'summary'):
                        # Update existing summary
                        summary = event_data.summary
                        summary.populate_with_summary_data()
                        summary.save()
                        updated_count += 1

                        self.stdout.write(
                            f'Updated summary for event {event_data.event_id}',
                            ending='\r'
                        )
                    else:
                        # Create new summary
                        summary = StripeEventSummary(stripe_event_data=event_data)
                        summary.populate_with_summary_data()
                        summary.save()
                        created_count += 1

                        self.stdout.write(
                            f'Created summary for event {event_data.event_id}',
                            ending='\r'
                        )

                except Exception as e:  # pylint: disable=broad-exception-caught
                    error_count += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f'Error processing event {event_data.event_id}: {str(e)}'
                        )
                    )
        return created_count, updated_count, error_count

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        dry_run = options['dry_run']
        event_type = options.get('event_type')
        force = options['force']

        # Validate batch size
        if batch_size < 1 or batch_size > 1000:
            raise CommandError('Batch size must be between 1 and 1000')

        # Build queryset
        queryset = StripeEventData.objects.all()

        if event_type:
            queryset = queryset.filter(event_type=event_type)
            self.stdout.write(f'Filtering by event type: {event_type}')

        if not force:
            # Only process records without existing summaries
            queryset = queryset.filter(summary__isnull=True)

        total_count = queryset.count()

        if total_count == 0:
            self.stdout.write(self.style.WARNING('No StripeEventData records found to process'))
            return

        self.stdout.write(f'Found {total_count} StripeEventData records to process')

        if dry_run:
            self._execute_dry_run(queryset, total_count)
            return

        created_count, updated_count, error_count = (0, 0, 0)
        self.stdout.write(f'Processing {total_count} records in batches of {batch_size}...')

        # Process in batches to avoid memory issues
        for index in range(0, total_count, batch_size):
            created_count, updated_count, error_count = self._process_summary_batch(
                queryset, index, batch_size, force,
            )

            # Progress update
            processed = min(index + batch_size, total_count)
            self.stdout.write(
                f'\nProcessed {processed}/{total_count} records '
                f'(Created: {created_count}, Updated: {updated_count}, Errors: {error_count})'
            )

        # Final summary
        self.stdout.write('\nProcessing complete:')
        if created_count > 0:
            self.stdout.write(
                self.style.SUCCESS(f'  Successfully created: {created_count} summary records')
            )
        if updated_count > 0:
            self.stdout.write(
                self.style.SUCCESS(f'  Successfully updated: {updated_count} summary records')
            )
        if error_count > 0:
            self.stdout.write(
                self.style.WARNING(f'  Errors encountered: {error_count}')
            )

        # Verify results
        total_summaries = StripeEventSummary.objects.count()
        self.stdout.write(f'Total StripeEventSummary records in database: {total_summaries}')
