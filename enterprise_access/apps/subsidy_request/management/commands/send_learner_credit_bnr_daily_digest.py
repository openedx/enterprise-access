"""
Management command to send daily Browse & Request learner credit digest emails to enterprise admins.

Simplified version: run once per day (e.g., via cron). It scans all BNR-enabled policies and
queues a digest task for each policy that has one or more REQUESTED learner credit requests
(open requests, regardless of creation date). Supports a --dry-run mode.
"""
import logging

from django.core.management.base import BaseCommand

from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy
from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates
from enterprise_access.apps.subsidy_request.models import LearnerCreditRequest
from enterprise_access.apps.subsidy_request.tasks import send_learner_credit_bnr_admins_email_with_new_requests_task

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Django management command to enqueue daily Browse & Request learner credit digest tasks.

    Scans active, non-retired policies with an active learner credit request config and enqueues
    one Celery task per policy that has at least one open (REQUESTED) learner credit request.
    Supports an optional dry-run mode for visibility without enqueuing tasks.
    """

    help = ('Queue celery tasks that send daily digest emails for Browse & Request learner credit '
            'requests per BNR-enabled policy (simple mode).')

    LOCK_KEY_TEMPLATE = 'bnr-lc-digest-{date}'
    LOCK_TIMEOUT_SECONDS = 2 * 60 * 60  # 2 hours

    def add_arguments(self, parser):  # noqa: D401 - intentionally left minimal
        parser.add_argument(
            '--dry-run',
            action='store_true',
            dest='dry_run',
            help='Show which tasks would be enqueued without sending them.'
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run')

        policies_qs = SubsidyAccessPolicy.objects.filter(
            active=True,
            retired=False,
            learner_credit_request_config__isnull=False,
            learner_credit_request_config__active=True,
        ).select_related('learner_credit_request_config')

        total_policies = 0
        policies_with_requests = 0
        tasks_enqueued = 0

        for policy in policies_qs.iterator():
            total_policies += 1
            config = policy.learner_credit_request_config
            if not config:
                continue

            num_open_requests = LearnerCreditRequest.objects.filter(
                learner_credit_request_config=config,
                enterprise_customer_uuid=policy.enterprise_customer_uuid,
                state=SubsidyRequestStates.REQUESTED,
            ).count()
            if num_open_requests == 0:
                continue

            policies_with_requests += 1
            if dry_run:
                logger.info('[DRY RUN] Policy %s enterprise %s would enqueue digest task (%s open requests).',
                            policy.uuid, policy.enterprise_customer_uuid, num_open_requests)
                continue

            logger.info(
                'Policy %s enterprise %s has %s open learner credit requests. Enqueuing digest task.',
                policy.uuid, policy.enterprise_customer_uuid, num_open_requests
            )
            send_learner_credit_bnr_admins_email_with_new_requests_task.delay(
                str(policy.uuid),
                str(config.uuid),
                str(policy.enterprise_customer_uuid),
            )
            tasks_enqueued += 1

        summary = (
            f"BNR daily digest summary: scanned={total_policies} policies, "
            f"with_requests={policies_with_requests}, tasks_enqueued={tasks_enqueued}, dry_run={dry_run}"
        )
        logger.info(summary)
        self.stdout.write(self.style.SUCCESS(summary))
        return 0
