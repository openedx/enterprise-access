"""
Management command to automatically expire assignment records and then send email to learners.
"""

import datetime
import logging

from django.core.management.base import BaseCommand
from django.core.paginator import Paginator

from enterprise_access.apps.content_assignments.api import expire_assignment
from enterprise_access.apps.content_assignments.constants import LearnerContentAssignmentStateChoices
from enterprise_access.apps.content_assignments.content_metadata_api import get_content_metadata_for_assignments
from enterprise_access.apps.content_assignments.models import AssignmentConfiguration

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Automatically expire certain assignment records and then send a cancellation email to learners.
    Also removes PII from some assignments under certain conditions.
    See: ``docs/decisions/0016_automatic_expiration.rst`` for more details.
    """
    help = (
        'Spin off celery tasks to automatically expire assignment records and then send email to learners'
    )

    def add_arguments(self, parser):
        """
        Entry point to add arguments.
        """
        parser.add_argument(
            '--dry-run',
            action='store_true',
            dest='dry_run',
            default=False,
            help='Dry Run, print log messages without spawning the celery tasks.',
        )

    @staticmethod
    def to_datetime(value):
        """
        Return a datetime object of `value` if it is a str.
        """
        if isinstance(value, str):
            return datetime.datetime.strptime(
                value,
                "%Y-%m-%dT%H:%M:%SZ"
            ).replace(
                tzinfo=datetime.timezone.utc
            )

        return value

    def handle(self, *args, **options):
        """
        Performs the command by retrieving expirable assignments, determining whether they should be
        expired, and then expiring them if so.
        """
        dry_run = options['dry_run']

        for assignment_configuration in AssignmentConfiguration.objects.filter(active=True):
            subsidy_access_policy = assignment_configuration.subsidy_access_policy
            enterprise_catalog_uuid = subsidy_access_policy.catalog_uuid

            message = (
                '[AUTOMATICALLY_EXPIRE_ASSIGNMENTS] Assignment Configuration. UUID: [%s], '
                'Policy: [%s], Catalog: [%s], Enterprise: [%s], dry_run [%s]',
            )
            logger.info(
                message,
                assignment_configuration.uuid,
                subsidy_access_policy.uuid,
                enterprise_catalog_uuid,
                assignment_configuration.enterprise_customer_uuid,
                dry_run,
            )

            assignments_to_possibly_expire = assignment_configuration.assignments.filter(
                state__in=LearnerContentAssignmentStateChoices.EXPIRABLE_STATES,
            ).order_by('created')

            paginator = Paginator(assignments_to_possibly_expire, 100)
            for page_number in paginator.page_range:
                assignments = paginator.page(page_number)

                content_metadata_for_assignments = get_content_metadata_for_assignments(
                    enterprise_catalog_uuid,
                    assignments
                )

                for assignment in assignments:
                    content_metadata = content_metadata_for_assignments.get(assignment.content_key, {})
                    expire_assignment(
                        assignment,
                        content_metadata,
                        modify_assignment=not dry_run,
                    )
