"""
Management command to send admins email with new subsidy requests.
"""

import logging
from time import sleep

from django.core.management.base import BaseCommand

from enterprise_access.apps.subsidy_request.models import SubsidyRequestCustomerConfiguration
from enterprise_access.apps.subsidy_request.tasks import send_admins_email_with_new_requests_task

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    This command is intended to run as frequently as specified in a crontab. The window of time used
    to select requests to include in the email starts from the time this is last run to present.
    The last run time is stored in the
    last_remind_date field on the SubsidyRequestCustomerConfiguration model. This gives us 100%
    coverage of requests to email about, and also
    gives us fault tolerance if this ever fails. We can simply rerun this
    and to capture all subsidy requests that haven't been included in an email yet.

    This DOESN'T give us fine grained control of different enterprises wanting different
    email frequencies, but that's not part of the MVP. If and when we would want that, we
    can simply give config models a field that holds how frequently admins want to be notified
    and then update the cronjob to run more frequently (a "higher resolution") to let us
    include all use cases.
    """
    help = (
        'Spin off celery tasks to send enterprise admins an email that lists requests '
        'that were created since this was last run.'
    )
    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            action='store',
            dest='batch_size',
            default=25,
            help='How many tasks to kick start before sleeping.',
            type=int,
        )
        parser.add_argument(
            '--sleep-duration',
            action='store',
            dest='sleep_duration',
            default=5,
            help='How long to sleep between batches.',
            type=int,
        )

    def _we_should_sleep(self, task_number, batch_size):
        if not task_number % batch_size:
            return True
        return False

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        sleep_duration = options['sleep_duration']

        enterprise_customer_uuids = SubsidyRequestCustomerConfiguration.objects.filter(
            subsidy_requests_enabled=True
        ).values_list(
            'enterprise_customer_uuid',
            flat=True,
        )

        for task_number, enterprise_customer_uuid in enumerate(enterprise_customer_uuids):
            send_admins_email_with_new_requests_task.delay(enterprise_customer_uuid)

            if self._we_should_sleep(task_number + 1, batch_size):
                sleep(sleep_duration)
