"""
Management command to nudge learners with accepted assignments about upcoming course start dates.

This management command is designed to run on a cron schedule of ONCE per day. It looks for any accepted assignments
which correspond to courses that start `days_before_course_start_date` days from now and sends those learners a nudge
email.

Supply `--days_before_course_start_date` to control the notification lead time (default: 30 days).
"""

import datetime
import logging

from django.core.management.base import BaseCommand
from django.core.paginator import Paginator

from enterprise_access.apps.content_assignments.constants import LearnerContentAssignmentStateChoices
from enterprise_access.apps.content_assignments.content_metadata_api import (
    get_content_metadata_for_assignments,
    is_date_n_days_from_now,
    parse_datetime_string
)
from enterprise_access.apps.content_assignments.models import AssignmentConfiguration
from enterprise_access.apps.content_assignments.tasks import send_exec_ed_enrollment_warmer
from enterprise_access.utils import get_normalized_metadata_for_assignment

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Management command body (see module docstring).
    """
    help = (
        'Spin off celery tasks to automatically send a braze email to '
        'remind learners about an upcoming accepted assignment a certain number '
        'of days in advanced determined by the "days_before_course_start_date" argument'
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
        parser.add_argument(
            '--days_before_course_start_date',
            type=int,
            dest='days_before_course_start_date',
            default=30,
            metavar='NUM_DAYS',
            help='The amount of days before the course start date to send a nudge email through braze',
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
        dry_run = options['dry_run']
        days_before_course_start_date = options['days_before_course_start_date']

        for assignment_configuration in AssignmentConfiguration.objects.filter(active=True):
            if not hasattr(assignment_configuration, 'subsidy_access_policy'):
                logger.info(
                    "Skipping nudge for AssignmentConfiguration: [%s], no subsidy_access_policy found",
                    assignment_configuration.uuid,
                )
                continue

            subsidy_access_policy = assignment_configuration.subsidy_access_policy

            if not hasattr(subsidy_access_policy, 'catalog_uuid'):
                logger.info(
                    "Skipping nudge for AssignmentConfiguration: [%s], no catalog_uuid found",
                    assignment_configuration.uuid,
                )
                continue

            enterprise_catalog_uuid = subsidy_access_policy.catalog_uuid

            message = (
                '[AUTOMATICALLY_REMIND_ACCEPTED_ASSIGNMENTS_1] Assignment Configuration. UUID: [%s], '
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

            accepted_assignments = assignment_configuration.assignments.filter(
                state=LearnerContentAssignmentStateChoices.ACCEPTED
            )

            if not accepted_assignments.exists():
                logger.info(
                    "Skipping nudge for AssignmentConfiguration: [%s], no accepted assignments found",
                    assignment_configuration.uuid,
                )
                continue

            paginator = Paginator(accepted_assignments, 100)
            for page_number in paginator.page_range:
                assignments = paginator.page(page_number)

                content_metadata_for_assignments = get_content_metadata_for_assignments(
                    enterprise_catalog_uuid,
                    assignments
                )

                for assignment in assignments:
                    if not assignment.preferred_course_run_key:
                        logger.info(
                            'Skipping nudge emails for legacy assignment [%s] due to missing preferred_course_run_key.',
                            assignment.uuid,
                        )
                        continue
                    metadata_for_assignment = content_metadata_for_assignments.get(assignment.content_key, {})
                    content_metadata = metadata_for_assignment.get('content_metadata')
                    if not content_metadata:
                        logger.info(
                            'Skipping nudge emails for assignment [%s] due to missing content metadata for key [%s].',
                            assignment.uuid,
                            assignment.content_key,
                        )
                        continue
                    course_type = content_metadata.get('course_type')
                    if course_type != 'executive-education-2u':
                        logger.info(
                            (
                                'Skipping nudge emails for assignment [%s] due to course_type ([%s]) not being equal '
                                'to "executive-education-2u".'
                            ),
                            assignment.uuid,
                            course_type,
                        )
                        continue

                    normalized_metadata = get_normalized_metadata_for_assignment(metadata_for_assignment)
                    start_date = normalized_metadata.get('start_date')

                    # Determine if the date from today + days_before_course_state_date is
                    # equal to the date of the start date
                    # If they are equal, then send the nudge email, otherwise continue
                    datetime_start_date = parse_datetime_string(start_date, set_to_utc=True)
                    can_send_nudge_notification_in_advance = (
                        is_date_n_days_from_now(
                            target_datetime=datetime_start_date,
                            num_days=days_before_course_start_date
                        )
                        if start_date is not None
                        else False
                    )
                    if not can_send_nudge_notification_in_advance:
                        logger.info(
                            (
                                'Skipping nudge emails for assignment [%s] due to current date not being exactly '
                                '%s days before the start date of the preferred course run (%s).'
                            ),
                            assignment.uuid,
                            days_before_course_start_date,
                            start_date,
                        )
                        continue

                    message = (
                        '[AUTOMATICALLY_REMIND_ACCEPTED_ASSIGNMENTS_2]  assignment_configuration_uuid: [%s], '
                        'start_date: [%s], datetime_start_date: [%s], '
                        'days_before_course_start_date: [%s], can_send_nudge_notification_in_advance: [%s], '
                        'course_type: [%s], dry_run [%s]'
                    )
                    logger.info(
                        message,
                        assignment_configuration.uuid,
                        start_date,
                        datetime_start_date,
                        days_before_course_start_date,
                        can_send_nudge_notification_in_advance,
                        course_type,
                        dry_run,
                    )
                    if not dry_run:
                        send_exec_ed_enrollment_warmer.delay(assignment.uuid, days_before_course_start_date)
