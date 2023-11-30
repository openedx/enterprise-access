"""
Management command to automatically expire assignment records and then send email to learners.
"""

import logging

from django.core.management.base import BaseCommand
from django.core.paginator import Paginator
from django.utils.timezone import now, timedelta

from enterprise_access.apps.content_assignments.constants import (
    NUM_DAYS_BEFORE_AUTO_CANCELLATION,
    AssignmentAutomaticExpiredReason,
    LearnerContentAssignmentStateChoices
)
from enterprise_access.apps.content_assignments.content_metadata_api import get_content_metadata_for_assignments
from enterprise_access.apps.content_assignments.models import AssignmentConfiguration
from enterprise_access.apps.content_assignments.tasks import send_assignment_automatically_expired_email

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Automatically expire assignment records and then send email to learners
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

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        log_prefix = '[AUTOMATICALLY_EXPIRE_ASSIGNMENTS]'
        if dry_run:
            log_prefix = '[DRY_RUN]'

        for assignment_configuration in AssignmentConfiguration.objects.filter(active=True):
            subsidy_access_policy = assignment_configuration.subsidy_access_policy
            enterprise_catalog_uuid = subsidy_access_policy.catalog_uuid
            subsidy_expiration_datetime = subsidy_access_policy.subsidy_expiration_datetime

            logger.info(
                '%s Processing Assignment Configuration. UUID: [%s], Policy: [%s], Catalog: [%s], Enterprise: [%s]',
                log_prefix,
                assignment_configuration.uuid,
                subsidy_access_policy.uuid,
                enterprise_catalog_uuid,
                assignment_configuration.enterprise_customer_uuid
            )

            allocated_assignments = assignment_configuration.assignments.filter(
                state=LearnerContentAssignmentStateChoices.ALLOCATED
            )

            paginator = Paginator(allocated_assignments, 100)
            for page_number in paginator.page_range:
                assignments = paginator.page(page_number)

                content_metadata_for_assignments = get_content_metadata_for_assignments(
                    enterprise_catalog_uuid,
                    assignments
                )

                for assignment in assignments:
                    content_metadata = content_metadata_for_assignments.get(assignment.content_key, {})
                    enrollment_end_date = content_metadata.get('normalized_metadata', {}).get('enroll_by_date')

                    logger.info(
                        '%s AssignmentUUID: [%s], ContentKey: [%s], AssignmentExpiry: [%s], EnrollmentEnd: [%s], SubsidyExpiry: [%s]',  # nopep8 pylint: disable=line-too-long
                        log_prefix,
                        assignment.uuid,
                        assignment.content_key,
                        assignment.created + timedelta(days=NUM_DAYS_BEFORE_AUTO_CANCELLATION),
                        enrollment_end_date,
                        subsidy_expiration_datetime
                    )

                    expired_assignment_uuid = None
                    assignment_expiry_reason = None
                    current_date = now()

                    if current_date > (assignment.created + timedelta(days=NUM_DAYS_BEFORE_AUTO_CANCELLATION)):
                        expired_assignment_uuid = assignment.uuid
                        assignment_expiry_reason = AssignmentAutomaticExpiredReason.NIENTY_DAYS_PASSED
                    elif enrollment_end_date and enrollment_end_date < current_date:
                        expired_assignment_uuid = assignment.uuid
                        assignment_expiry_reason = AssignmentAutomaticExpiredReason.ENROLLMENT_DATE_PASSED
                    elif subsidy_expiration_datetime and subsidy_expiration_datetime < current_date:
                        expired_assignment_uuid = assignment.uuid
                        assignment_expiry_reason = AssignmentAutomaticExpiredReason.SUBSIDY_EXPIRED

                    if expired_assignment_uuid:
                        logger.info(
                            '%s Assignment Expired. AssignmentConfigUUID: [%s], AssignmentUUID: [%s], Reason: [%s]',
                            log_prefix,
                            assignment_configuration.uuid,
                            expired_assignment_uuid,
                            assignment_expiry_reason
                        )

                        if not dry_run:
                            assignment.state = LearnerContentAssignmentStateChoices.CANCELLED
                            assignment.save()
                            send_assignment_automatically_expired_email.delay(expired_assignment_uuid)
