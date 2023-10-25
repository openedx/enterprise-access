"""
Signal handlers for content_assignments app.
"""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

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
            learner_email=user.email,
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
