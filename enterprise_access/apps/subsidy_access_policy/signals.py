"""
Signal handlers for subsidy_access_policy app.
"""
import logging

from django.dispatch import receiver
from openedx_events.enterprise.data import EnterpriseGroup
from openedx_events.enterprise.signals import ENTERPRISE_GROUP_DELETED

from enterprise_access.apps.subsidy_access_policy.models import PolicyGroupAssociation

logger = logging.getLogger(__name__)


@receiver(ENTERPRISE_GROUP_DELETED)
def handle_enterprise_group_deleted(**kwargs):
    """
    OEP-49 event handler to update assignment status for reversed transaction.
    """
    logger.info('Received ENTERPRISE_GROUP_DELETED signal with data: %s', kwargs)
    group = kwargs.get('enterprise_group')
    if not group or not isinstance(group, EnterpriseGroup):
        logger.error('ENTERPRISE_GROUP_DELETED signal missing or invalid enterprise_group: %s', kwargs)
        raise ValueError('Missing or invalid enterprise_group in signal')

    group_uuid = group.uuid

    deletions = PolicyGroupAssociation.cascade_delete_for_group_uuid(group_uuid)
    logger.info('PolicyGroupAssociation records deleted: %s', deletions)
