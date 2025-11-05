"""
Management command to detect and mark CheckoutIntent records that have been
stuck in 'paid' state for too long, indicating stalled fulfillment.
"""
import logging

from django.core.management.base import BaseCommand

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

        logger.info(
            '%sStarting mark_stalled_checkout_intents command with threshold=%s seconds',
            mode_label,
            threshold_seconds,
        )

        if dry_run:
            # In dry-run mode, just find and log stalled intents
            stalled_intents, updated_uuids, _ = CheckoutIntent.find_stalled_fulfillment_intents(
                stalled_threshold_seconds=threshold_seconds,
                do_logging=True,
            )
            updated_count = len(stalled_intents)
        else:
            # Actually mark the stalled intents
            updated_count, updated_uuids = CheckoutIntent.mark_stalled_fulfillment_intents(
                stalled_threshold_seconds=threshold_seconds,
            )

        if updated_count == 0:
            logger.info('%sNo stalled CheckoutIntent records found', mode_label)
        else:
            if dry_run:
                logger.info('%sFound %s stalled CheckoutIntent record(s)', mode_label, updated_count)
            else:
                logger.info(
                    'Successfully marked %s CheckoutIntent record(s) as errored_fulfillment_stalled',
                    updated_count,
                )
                for intent_id in updated_uuids:
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
