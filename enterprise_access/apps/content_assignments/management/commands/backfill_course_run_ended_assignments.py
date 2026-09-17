"""
Backfill active assignments whose known course runs have already ended.
"""

import logging

from django.core.management.base import BaseCommand

from enterprise_access.apps.content_assignments.api import expire_assignment
from enterprise_access.apps.content_assignments.constants import (
    AssignmentAutomaticExpiredReason,
    LearnerContentAssignmentStateChoices,
)
from enterprise_access.apps.content_assignments.content_metadata_api import get_content_metadata_for_assignments
from enterprise_access.apps.content_assignments.models import AssignmentConfiguration
from enterprise_access.utils import (
    _get_catalog_agnostic_content_metadata_for_assignment,
    get_automatic_expiration_date_and_reason,
    localized_utcnow,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Backfill assignments that should already be expired because all known course runs have ended.
    """

    help = 'Backfill assignments whose known course runs have all ended.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Report would-be expirations without updating assignment state.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        matched_count = 0
        expired_count = 0

        for assignment_configuration in AssignmentConfiguration.objects.filter(active=True):
            subsidy_access_policy = assignment_configuration.subsidy_access_policy
            enterprise_catalog_uuid = subsidy_access_policy.catalog_uuid

            assignments = assignment_configuration.assignments.filter(
                state__in=LearnerContentAssignmentStateChoices.EXPIRABLE_STATES,
            ).order_by('created')

            metadata_by_key = get_content_metadata_for_assignments(enterprise_catalog_uuid, assignments)
            for assignment in assignments:
                content_metadata = metadata_by_key.get(assignment.content_key, {})
                if not content_metadata:
                    content_metadata = _get_catalog_agnostic_content_metadata_for_assignment(assignment)

                automatic_expiration = get_automatic_expiration_date_and_reason(assignment, content_metadata)
                expiration_reason = automatic_expiration.get('reason')
                expiration_date = automatic_expiration.get('date')

                if expiration_reason != AssignmentAutomaticExpiredReason.COURSE_RUN_ENDED:
                    continue
                if expiration_date is None or expiration_date > localized_utcnow():
                    continue

                matched_count += 1
                if dry_run:
                    self.stdout.write(
                        f'Would expire assignment {assignment.uuid} due to {expiration_reason} on {expiration_date}'
                    )
                    continue

                expire_assignment(assignment, content_metadata, modify_assignment=True)
                expired_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Backfill complete. Found {matched_count} assignments to expire; expired {expired_count}.'
            )
        )
