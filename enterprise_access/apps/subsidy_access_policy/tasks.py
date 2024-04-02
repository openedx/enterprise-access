"""
Celery tasks for Enterprise Access API.
"""
from datetime import datetime, timedelta
import logging

from celery import shared_task
from django.conf import settings

from enterprise_access.apps.api_client.braze_client import BrazeApiClient, ENTERPRISE_BRAZE_ALIAS_LABEL

from enterprise_access.apps.enterprise_groups.constants import (
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_FINAL_REMINDER_DAY,
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_FIRST_REMINDER_DAY,
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_FOURTH_REMINDER_DAY,
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_SECOND_REMINDER_DAY,
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_THIRD_REMINDER_DAY,
    DAYS_TO_PURGE_PII
)
from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy
from enterprise_access.tasks import LoggedTaskWithRetry

logger = logging.getLogger(__name__)


def get_braze_campaign_properties(membership_created_time, enterprise_customer_name, content_metadata, subsidy_end_date):
    """
    Helper function to return braze campaign id and properties based on days passed since group membership invite
    """
    current_date = datetime.today()
    invitation_end_date = membership_created_time + timedelta(days=DAYS_TO_PURGE_PII)

    if current_date - datetime.strptime(membership_created_time, "%d %b %Y") == timedelta(days=BRAZE_GROUPS_EMAIL_CAMPAIGNS_FIRST_REMINDER_DAY):
        return {
            'braze_campaign_id': settings.BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_5_CAMPAIGN,
            # the trigger properties are different depending on the reminder email
            'braze_trigger_properties': {
                'enterprise_customer': enterprise_customer_name,
                'catalog_content_count': len(content_metadata),
                'invitation_end_date': invitation_end_date,
                'subsidy_end_date': subsidy_end_date
            }
        }

    if current_date - datetime.strptime(membership_created_time, "%d %b %Y") == timedelta(days=BRAZE_GROUPS_EMAIL_CAMPAIGNS_SECOND_REMINDER_DAY):
        return {
            'braze_campaign_id': settings.BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_25_CAMPAIGN,
            'braze_trigger_properties': {
                'catalog_content_count': len(content_metadata),
                'invitation_end_date': invitation_end_date,
                'subsidy_end_date': subsidy_end_date
            }
        }
    
    if current_date - datetime.strptime(membership_created_time, "%d %b %Y") == timedelta(days=BRAZE_GROUPS_EMAIL_CAMPAIGNS_THIRD_REMINDER_DAY):
        return {
            'braze_campaign_id': settings.BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_50_CAMPAIGN,
            'braze_trigger_properties': {
                'catalog_content_count': len(content_metadata),
                'invitation_end_date': invitation_end_date,
                'subsidy_end_date': subsidy_end_date
            }
        }
    
    if current_date - datetime.strptime(membership_created_time, "%d %b %Y") == timedelta(days=BRAZE_GROUPS_EMAIL_CAMPAIGNS_FOURTH_REMINDER_DAY):
        return {
            'braze_campaign_id': settings.BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_65_CAMPAIGN,
            'braze_trigger_properties': {
                'enterprise_customer': enterprise_customer_name,
                'catalog_content_count': len(content_metadata),
                'invitation_end_date': invitation_end_date,
                'subsidy_end_date': subsidy_end_date
            }
        }

    if current_date - datetime.strptime(membership_created_time, "%d %b %Y") == timedelta(days=BRAZE_GROUPS_EMAIL_CAMPAIGNS_FINAL_REMINDER_DAY):
        return {
            'braze_campaign_id': settings.BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_85_CAMPAIGN,
            'braze_trigger_properties': {
                'catalog_content_count': len(content_metadata),
                'invitation_end_date': invitation_end_date,
                'subsidy_end_date': subsidy_end_date
            }
        }

    return None


@shared_task(base=LoggedTaskWithRetry)
def send_group_reminder_emails(pending_enterprise_user):
    """
    Send braze reminder emails to pending learners who have not accepted invitation
    to a group membership.

    Arguments:
        * pending_group_membership (dict): an instance of EnterpriseGroupMembership
    """

    braze_client_instance = BrazeApiClient()
    braze_trigger_properties = {}
    enterprise_customer_name = pending_enterprise_user.enterprise_customer_name
    braze_trigger_properties['enterprise_customer_name'] = enterprise_customer_name
    pecu_email = pending_enterprise_user.user_email

    recipient = braze_client_instance.create_recipient_no_external_id(
        pecu_email,
    )
    # We need an alias record to exist in Braze before
    # sending to any previously-unidentified users.
    braze_client_instance.create_braze_alias(
        [pecu_email],
        ENTERPRISE_BRAZE_ALIAS_LABEL,
    )

    # need to get content_metadata for the number of courses and budget end date
    content_metadata = []
    subsidy_end_date = pending_enterprise_user.subsidy_end_date
   
    braze_properties = get_braze_campaign_properties(pending_enterprise_user.recent_action, enterprise_customer_name, content_metadata, subsidy_end_date)
    braze_client_instance.send_campaign_message(
        braze_properties.braze_campaign_id,
        recipients=[recipient],
        trigger_properties=braze_trigger_properties,
    )