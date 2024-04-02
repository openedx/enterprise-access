"""
Signal handlers for content_assignments app.
"""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from openedx_events.enterprise.signals import SUBSIDY_REDEMPTION_REVERSED

from enterprise_access.apps.content_assignments.models import LearnerContentAssignment
from enterprise_access.apps.core.models import User
from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy
from enterprise_access.utils import localized_utcnow

from .constants import LearnerContentAssignmentStateChoices

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


@receiver(SUBSIDY_REDEMPTION_REVERSED)
def update_assignment_status_for_reversed_transaction(**kwargs):
    """
    OEP-49 event handler to update assignment status for reversed transaction.
    """
    redemption = kwargs.get('redemption')
    subsidy_access_uuid = redemption.subsidy_identifier
    content_key = redemption.content_key
    lms_user_id = redemption.lms_user_id

    try:
        policy = SubsidyAccessPolicy.objects.get(uuid=subsidy_access_uuid)
        assignment_to_update = policy.get_assignment(lms_user_id, content_key)
    except SubsidyAccessPolicy.DoesNotExist:
        logger.error(
            f'Unable to access policy {subsidy_access_uuid} for content_key {content_key} and {lms_user_id}'
        )
    if assignment_to_update and assignment_to_update.state in LearnerContentAssignmentStateChoices.REVERSIBLE_STATES:
        assignment_to_update.state = LearnerContentAssignmentStateChoices.REVERSED
        assignment_to_update.reversed_at = localized_utcnow()
        assignment_to_update.save()
        logger.info(
            f'Content assignment {assignment_to_update.uuid} for content_key {content_key} and \
            {lms_user_id} reversed.'
        )
