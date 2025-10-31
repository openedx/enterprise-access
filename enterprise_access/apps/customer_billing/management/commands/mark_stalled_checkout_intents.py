"""
Management command to detect and mark CheckoutIntent records that have been
stuck in 'paid' state for too long, indicating stalled fulfillment.
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from enterprise_access.apps.customer_billing.constants import CheckoutIntentState
from enterprise_access.apps.customer_billing.models import CheckoutIntent

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Command to detect and transition CheckoutIntent records that have been
    stuck in 'paid' state for a configurable duration.

    When a CheckoutIntent transitions to 'paid' state but fulfillment fails
    without proper error handling, the intent can remain stuck indefinitely.
    This command detects such cases and transitions them to
    'errored_fulfillment_stalled' to trigger alerts and display error UI.

    Usage:
        ./manage.py mark_stalled_checkout_intents
        ./manage.py mark_stalled_checkout_intents --threshold-seconds=300
        ./manage.py mark_stalled_checkout_intents --dry-run
    """

    help = (
        'Detect and mark CheckoutIntent records stuck in paid state as '
        'errored_fulfillment_stalled'
    )

    def add_arguments(self, parser):
        """
        Add command-line arguments.
        """
        parser.add_argument(
            '--threshold-seconds',
            type=int,
            default=180,
            help=(
                'Number of seconds a CheckoutIntent must be in paid state '
                'before being considered stalled. Default: 180 (3 minutes). '
                'This accounts for exponential backoff in Salesforce API calls '
                'and the provisioning workflow. Should be long enough to avoid '
                'false positives but short enough for timely error display.'
            ),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            dest='dry_run',
            default=False,
            help='Show what would be updated without actually updating records.',
        )

    def handle(self, *args, **options):
        """
        Find and mark stalled CheckoutIntent records.
        """
        threshold_seconds = options['threshold_seconds']
        dry_run = options['dry_run']

        mode_label = '[DRY RUN] ' if dry_run else ''

        self.stdout.write(
            f'{mode_label}Checking for CheckoutIntent records stuck in paid state '
            f'for more than {threshold_seconds} seconds...'
        )
        logger.info(
            '%sStarting mark_stalled_checkout_intents command with threshold=%s seconds',
            mode_label,
            threshold_seconds,
        )

        if dry_run:
            # Query stalled intents without updating
            threshold_time = timezone.now() - timedelta(seconds=threshold_seconds)
            stalled_intents = CheckoutIntent.objects.filter(
                state=CheckoutIntentState.PAID,
                modified__lte=threshold_time,
            ).order_by('modified')

            count = stalled_intents.count()

            if count == 0:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'{mode_label}No stalled CheckoutIntent records found.'
                    )
                )
                logger.info('%sNo stalled CheckoutIntent records found', mode_label)
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f'{mode_label}Found {count} stalled CheckoutIntent record(s):'
                    )
                )
                for intent in stalled_intents:
                    time_stalled = (timezone.now() - intent.modified).total_seconds()
                    self.stdout.write(
                        f'  - ID: {intent.pk}, '
                        f'User: {intent.user.email if intent.user else "N/A"}, '
                        f'Enterprise: {intent.enterprise_name or intent.enterprise_slug}, '
                        f'Time stalled: {int(time_stalled)}s, '
                        f'Last modified: {intent.modified.isoformat()}'
                    )
                    logger.info(
                        '%sWould mark CheckoutIntent %s as stalled (stalled for %s seconds)',
                        mode_label,
                        intent.pk,
                        int(time_stalled),
                    )
        else:
            # Actually update records
            updated_count, updated_uuids = CheckoutIntent.mark_stalled_fulfillment_intents(
                stalled_threshold_seconds=threshold_seconds
            )

            if updated_count == 0:
                self.stdout.write(
                    self.style.SUCCESS(
                        'No stalled CheckoutIntent records found.'
                    )
                )
                logger.info('No stalled CheckoutIntent records found')
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Successfully marked {updated_count} CheckoutIntent record(s) '
                        f'as errored_fulfillment_stalled'
                    )
                )
                for intent_id in updated_uuids:
                    self.stdout.write(f'  - Updated CheckoutIntent: {intent_id}')
                    logger.info(
                        'Marked CheckoutIntent %s as errored_fulfillment_stalled',
                        intent_id,
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f'{mode_label}Command completed successfully'
            )
        )
        logger.info('%smark_stalled_checkout_intents command completed', mode_label)
