"""
Signal handlers for subsidy_access_policy app.
"""
import logging

from django.dispatch import receiver
from openedx_events.enterprise.signals import ENTERPRISE_GROUP_DELETED

from enterprise_access.apps.subsidy_access_policy.models import PolicyGroupAssociation

logger = logging.getLogger(__name__)


@receiver(ENTERPRISE_GROUP_DELETED)
def handle_enterprise_group_deleted(**kwargs):
    """
    OEP-49 event handler to update assignment status for reversed transaction.
    """
    logger.info('Received ENTERPRISE_GROUP_DELETED signal with data: %s', kwargs)
    group_uuid = kwargs.get('enterprise_group').uuid if 'enterprise_group' in kwargs else None
    if not group_uuid:
        logger.warning('ENTERPRISE_GROUP_DELETED signal received without a valid group UUID.')
        return
    deletions = PolicyGroupAssociation.cascade_delete_for_group_uuid(group_uuid)
    logger.info('PolicyGroupAssociation records deleted: %s', deletions)
