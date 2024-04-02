"""
Django management command for sending reminder emails to pending users to accept groups invitation.
"""

import logging
from datetime import datetime, timedelta

from django.core.management import BaseCommand
from django.conf import settings
from django.db.models import Q
from enterprise_access.apps.subsidy_access_policy.tasks import send_group_reminder_emails
from enterprise_access.apps.subsidy_access_policy.constants import (
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_FINAL_REMINDER_DAY,
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_FIRST_REMINDER_DAY,
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_FOURTH_REMINDER_DAY,
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_SECOND_REMINDER_DAY,
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_THIRD_REMINDER_DAY,
    DAYS_TO_PURGE_PII
)
from enterprise_access.apps.content_assignments.content_metadata_api import (
    is_date_n_days_from_now,
)
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.subsidy_access_policy.models import PolicyGroupAssociation
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
        import pdb
        pdb.set_trace()
        lms_client = LmsApiClient()
        emails_to_send = []
        # sift through policy/group associations
        policy_group_associations = PolicyGroupAssociation.objects.all()
        # request group info for the customer data
        # request get_learners endpoint to get learners to send reminders to
        # task to kick off reminder emails:
        # 1. make request to edx-enterprise to get group memberships with pending ecu's
        # which includes the enterprise customer data. 
        # 2. in edx-enterprise, do we create an endpoint and pass down the list of pending ecu's.
        for policy_group_association in policy_group_associations:
            enterprise_group_uuid = policy_group_association.enterprise_group_uuid
            pending_enterprise_customer_users = lms_client.get_enterprise_group_memberships(enterprise_group_uuid)
            subsidy_expiration = policy_group_association.subsidy_access_policy.subsidy_expiration_datetime

            for pending_enterprise_customer_user in pending_enterprise_customer_users:
                # "Accepted: March 25, 2024".partition(': ')[2] would get back  March 25, 2024
                recent_action_time = pending_enterprise_customer_user['recent_action'].partition(': ')[2]
                pending_enterprise_customer_user['subsidy_expiration'] = subsidy_expiration
                emails_to_send.append(pending_enterprise_customer_user)
                current_date = datetime.today().strftime('%B %d, %Y')
                # if recent_action_time 
                send_group_reminder_emails.delay(pending_enterprise_customer_user)


        # # a = "Accepted: March 25, 2024".partition(': ')[2]

        # for pending_user_membership in pending_user_memberships:
        #     send_group_reminder_emails.delay(pending_user_membership)
