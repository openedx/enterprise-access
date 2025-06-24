"""
Signal handlers for subsidy_access_policy app.
"""
import logging

from django.dispatch import receiver
from openedx_events.enterprise.signals import ENTERPRISE_GROUP_DELETED


logger = logging.getLogger(__name__)

@receiver(ENTERPRISE_GROUP_DELETED)
def handle_enterprise_group_deleted(**kwargs):
    """
    OEP-49 event handler to update assignment status for reversed transaction.
    """
    logger.info('Received ENTERPRISE_GROUP_DELETED signal with data: %s', kwargs)
