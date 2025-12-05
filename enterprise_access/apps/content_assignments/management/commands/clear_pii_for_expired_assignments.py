"""
Management command to clear PII from expired assignment records.
"""

import logging

from django.core.management.base import BaseCommand

from enterprise_access.apps.content_assignments.tasks import clear_pii_for_expired_assignments

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Clear PII from assignments that have expired due to the 90-day timeout.

    PII is only cleared for assignments that:
    - Are in EXPIRED state
    - Have not already had PII cleared
    - Have had a successful expiration email sent
    - Expired due to NINETY_DAYS_PASSED reason (not enrollment deadline or subsidy expiration)

    See: ``docs/decisions/0016_automatic_expiration.rst`` and
         ``docs/decisions/0035-separate-clear-pii-task.md`` for more details.
    """
    help = 'Clear PII from expired assignment records that have had expiration emails sent'

    def add_arguments(self, parser):
        """
        Entry point to add arguments.
        """
        parser.add_argument(
            '--dry-run',
            action='store_true',
            dest='dry_run',
            default=False,
            help='Dry Run, print log messages without actually clearing PII.',
        )

    def handle(self, *args, **options):
        """
        Performs the command by calling the clear_pii_for_expired_assignments task synchronously.
        """
        dry_run = options['dry_run']

        logger.info(
            '[CLEAR_PII_FOR_EXPIRED_ASSIGNMENTS] Starting management command. dry_run=%s',
            dry_run
        )

        # Call the task synchronously (not via .delay()) for management command execution
        result = clear_pii_for_expired_assignments(dry_run=dry_run)

        self.stdout.write(
            self.style.SUCCESS(
                f'[CLEAR_PII_FOR_EXPIRED_ASSIGNMENTS] Completed. '
                f'Cleared: {result["cleared_count"]}, '
                f'Skipped: {result["skipped_count"]}, '
                f'Dry run: {result["dry_run"]}'
            )
        )

        if result['assignment_uuids_cleared']:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Assignment UUIDs with PII cleared: {result["assignment_uuids_cleared"]}'
                )
            )
