"""
Signal handlers for content_assignments app.
"""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from openedx_events.enterprise.signals import LEDGER_TRANSACTION_REVERSED, ENTERPRISE_GROUP_DELETED

from enterprise_access.apps.content_assignments.constants import LearnerContentAssignmentStateChoices
from enterprise_access.apps.content_assignments.models import LearnerContentAssignment
from enterprise_access.apps.core.models import User

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def update_assignment_lms_user_id_from_user_email(sender, **kwargs):  # pylint: disable=unused-argument
    """
    Post save hook to update assignment lms_user_id from core user records.
    """
    user = kwargs['instance']
    if user.lms_user_id:
        assignments_to_update = LearnerContentAssignment.objects.filter(
            learner_email__iexact=user.email,
            lms_user_id=None,
        )

        # Update multiple assignments in a history-safe way.
        for assignment in assignments_to_update:
            assignment.lms_user_id = user.lms_user_id
        num_assignments_updated = LearnerContentAssignment.bulk_update(assignments_to_update, ['lms_user_id'])

        # Intentionally not logging PII (email).
        if len(assignments_to_update) > 0:
            logger.info(
                f'Set lms_user_id={user.lms_user_id} on {num_assignments_updated} assignments for User.id={user.id}'
            )


@receiver(LEDGER_TRANSACTION_REVERSED)
def update_assignment_status_for_reversed_transaction(**kwargs):
    """
    OEP-49 event handler to update assignment status for reversed transaction.
    """
    ledger_transaction = kwargs.get('ledger_transaction')
    transaction_uuid = ledger_transaction.uuid

    try:
        assignment_to_update = LearnerContentAssignment.objects.get(transaction_uuid=transaction_uuid)
    except LearnerContentAssignment.DoesNotExist:
        logger.info(f'No LearnerContentAssignment exists with transaction uuid: {transaction_uuid}')
        return

    if assignment_to_update.state in LearnerContentAssignmentStateChoices.REVERSIBLE_STATES:
        assignment_to_update.state = LearnerContentAssignmentStateChoices.REVERSED
        assignment_to_update.reversed_at = timezone.now()
        assignment_to_update.save()
        assignment_to_update.add_successful_reversal_action()
        logger.info(
            f'LearnerContentAssignment {assignment_to_update.uuid} reversed.'
        )
    else:
        logger.warning(
            f'Cannot reverse LearnerContentAssignment {assignment_to_update.uuid} '
            f'because its state is {assignment_to_update.state}'
        )
