"""
Management command to clean up expired checkout intents.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from enterprise_access.apps.customer_billing.constants import CheckoutIntentState
from enterprise_access.apps.customer_billing.models import CheckoutIntent


class Command(BaseCommand):
    """
    Command to help periodically clean up expired checkout intent records.

    This ensures that enterprise slugs and names reserved during the checkout
    process are released when the reservation period expires.
    """
    help = 'Clean up expired checkout intents by marking them as EXPIRED'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without actually updating',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # Get all expired intents that are still in CREATED state
        expired_intents = CheckoutIntent.objects.filter(
            state=CheckoutIntentState.CREATED,
            expires_at__lte=timezone.now()
        )

        count = expired_intents.count()

        if dry_run:
            self.stdout.write(
                self.style.WARNING(f'Would update {count} expired checkout intents to EXPIRED state')
            )

            # Show details in dry-run mode
            if count > 0:
                self.stdout.write(self.style.WARNING('Would update:'))
                for intent in expired_intents[:50]:  # Limit output to avoid flooding
                    self.stdout.write(
                        f"  - ID: {intent.id}, User: {intent.user.email}, "
                        f"Slug: {intent.enterprise_slug}, Expired: {intent.expires_at}"
                    )

                if count > 50:
                    self.stdout.write(f"  ... and {count - 50} more")
        else:
            if count > 0:
                self.stdout.write(f"Processing {count} expired checkout intents...")
                CheckoutIntent.cleanup_expired()
                self.stdout.write(
                    self.style.SUCCESS(f'Successfully updated {count} expired checkout intents to EXPIRED state')
                )
            else:
                self.stdout.write('No expired checkout intents found')
