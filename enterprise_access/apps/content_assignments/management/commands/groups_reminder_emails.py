"""
Django management command for sending reminder emails to pending users to accept groups invitation.
"""

import logging
from datetime import datetime, timedelta

from django.core.management import BaseCommand
from django.db.models import Q
from enterprise_access.apps.api.tasks import send_group_reminder_emails
from enterprise_access.constants import (
    BRAZE_GROUPS_EMAIL_CAMPAIGNS,
)

LOGGER = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Django management command for sending reminder emails to pending users that have been added to a group.

    This command sends reminder emails to learners at the 5, 25, 50, 65, and 85 day mark before the 90
    day purge date.

    """
    help = "Send auto reminder emails to pending enterprise customer users that have been added to a group."

    def handle(self, *args, **options):
        """
        Command's entry point.
        """
        LOGGER.info("starting send_groups_reminder_email task.")
        
        current_date = datetime.today()
        day_5_pending_enterprise_customer_in_q = Q(created_at__date=current_date - timedelta(days=BRAZE_GROUPS_EMAIL_CAMPAIGNS.AUTO_REMINDER.FIRST_REMINDER.DAY))
        day_25_pending_enterprise_customer_in_q = Q(created_at_date=current_date - timedelta(days=BRAZE_GROUPS_EMAIL_CAMPAIGNS.AUTO_REMINDER.SECOND_REMINDER.DAY))
        day_50_pending_enterprise_customer_in_q =  Q(created_at_date=current_date - timedelta(days=BRAZE_GROUPS_EMAIL_CAMPAIGNS.AUTO_REMINDER.THIRD_REMINDER.DAY))
        day_65_pending_enterprise_customer_in_q =  Q(created_at_date=current_date - timedelta(days=BRAZE_GROUPS_EMAIL_CAMPAIGNS.AUTO_REMINDER.FOURTH_REMINDER.DAY))
        day_85_pending_enterprise_customer_in_q =  Q(created_at_date=current_date - timedelta(days=BRAZE_GROUPS_EMAIL_CAMPAIGNS.AUTO_REMINDER.FINAL_REMINDER.DAY))
        pending_user_memberships_in_q = Q(enterprise_customer_user__isnull=True)

        # task to kick off reminder emails:
        # 1. make request to edx-enterprise to get group memberships with pending ecu's
        # which includes the enterprise customer data. 
        # 2. in edx-enterprise, do we create an endpoint and pass down the list of pending ecu's.
        pending_user_memberships = EnterpriseGroupMembership.objects.filter(
            pending_user_memberships_in_q & (
                day_5_pending_enterprise_customer_in_q |
                day_25_pending_enterprise_customer_in_q | 
                day_50_pending_enterprise_customer_in_q |
                day_65_pending_enterprise_customer_in_q |
                day_85_pending_enterprise_customer_in_q
            )
        )

        for pending_user_membership in pending_user_memberships:
            send_group_reminder_emails.delay(pending_user_membership)
