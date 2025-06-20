"""
Management command to clean up expired enterprise slug reservations.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from enterprise_access.apps.customer_billing.models import EnterpriseSlugReservation


class Command(BaseCommand):
    """
    Command to help periodically clean up enterprise slug reservation records.
    """
    help = 'Clean up expired enterprise slug reservations'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if dry_run:
            expired_reservations = EnterpriseSlugReservation.objects.filter(
                expires_at__lte=timezone.now()
            )
            count = expired_reservations.count()
            self.stdout.write(
                self.style.WARNING(f'Would delete {count} expired reservations')
            )
            for reservation in expired_reservations:
                self.stdout.write(f'  - {reservation}')
        else:
            deleted_count = EnterpriseSlugReservation.cleanup_expired()
            self.stdout.write(
                self.style.SUCCESS(f'Successfully cleaned up {deleted_count} expired reservations')
            )
