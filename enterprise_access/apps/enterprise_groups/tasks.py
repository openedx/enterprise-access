"""
Celery tasks for Enterprise Access API.
"""

import logging
from datetime import datetime, timedelta

from braze.exceptions import BrazeClientError
from celery import shared_task
from django.conf import settings

from enterprise_access.apps.api_client.braze_client import ENTERPRISE_BRAZE_ALIAS_LABEL, BrazeApiClient
from enterprise_access.apps.enterprise_groups.constants import (
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_FINAL_REMINDER_DAY,
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_FIRST_REMINDER_DAY,
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_FOURTH_REMINDER_DAY,
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_SECOND_REMINDER_DAY,
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_THIRD_REMINDER_DAY,
    DAYS_TO_PURGE_PII
)
from enterprise_access.tasks import LoggedTaskWithRetry

logger = logging.getLogger(__name__)


def get_braze_campaign_properties(
    recent_action, enterprise_customer_name, catalog_count, subsidy_expiration_datetime
):
    """
    Helper function to return braze campaign id and properties based on days passed since group membership invite
    """
    recent_action_time = recent_action.partition(": ")[2]
    current_date = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
    invitation_end_date = (datetime.strptime(recent_action_time, "%B %d, %Y") +
                           timedelta(days=DAYS_TO_PURGE_PII)).strftime("%B %d, %Y")
    subsidy_expiration_date = datetime.strptime(subsidy_expiration_datetime, '%Y-%m-%dT%H:%M:%SZ').strftime("%B %d, %Y")
    logger.info('get_braze_campaign_properties_1: recent_action_time {%s}, '
                'current_date {%s}, invitation_end_date {%s}, catalog_count {%s}, subsidy_expiration_datetime {%s}',
                recent_action_time,
                current_date,
                invitation_end_date,
                catalog_count,
                subsidy_expiration_date,)
    if settings.BRAZE_GROUP_EMAIL_FORCE_REMIND_ALL_PENDING_LEARNERS or current_date - timedelta(
        days=BRAZE_GROUPS_EMAIL_CAMPAIGNS_FIRST_REMINDER_DAY
    ) == datetime.strptime(recent_action_time, "%B %d, %Y"):
        logger.info('get_braze_campaign_properties_2: properties for reminder day {%s} for enterprise customer {%s}',
                    BRAZE_GROUPS_EMAIL_CAMPAIGNS_FIRST_REMINDER_DAY,
                    enterprise_customer_name)
        return {
            "braze_campaign_id": settings.BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_5_CAMPAIGN,
            # the trigger properties are different depending on the reminder email
            "braze_trigger_properties": {
                "enterprise_customer": enterprise_customer_name,
                "catalog_content_count": catalog_count,
                "invitation_end_date": invitation_end_date,
                "subsidy_expiration_datetime": subsidy_expiration_date,
            },
        }

    if settings.BRAZE_GROUP_EMAIL_FORCE_REMIND_ALL_PENDING_LEARNERS or current_date - timedelta(
        days=BRAZE_GROUPS_EMAIL_CAMPAIGNS_SECOND_REMINDER_DAY
    ) == datetime.strptime(recent_action_time, "%B %d, %Y"):
        logger.info('get_braze_campaign_properties_3: properties for reminder day {%s} for enterprise customer {%s}',
                    BRAZE_GROUPS_EMAIL_CAMPAIGNS_SECOND_REMINDER_DAY,
                    enterprise_customer_name)
        return {
            "braze_campaign_id": settings.BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_25_CAMPAIGN,
            "braze_trigger_properties": {
                "catalog_content_count": catalog_count,
                "invitation_end_date": invitation_end_date,
                "subsidy_expiration_datetime": subsidy_expiration_date,
            },
        }

    if settings.BRAZE_GROUP_EMAIL_FORCE_REMIND_ALL_PENDING_LEARNERS or current_date - timedelta(
        days=BRAZE_GROUPS_EMAIL_CAMPAIGNS_THIRD_REMINDER_DAY
    ) == datetime.strptime(recent_action_time, "%B %d, %Y"):
        logger.info('get_braze_campaign_properties_4: properties for reminder day {%s} for enterprise customer {%s}',
                    BRAZE_GROUPS_EMAIL_CAMPAIGNS_THIRD_REMINDER_DAY,
                    enterprise_customer_name)
        return {
            "braze_campaign_id": settings.BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_50_CAMPAIGN,
            "braze_trigger_properties": {
                "catalog_content_count": catalog_count,
                "invitation_end_date": invitation_end_date,
                "subsidy_expiration_datetime": subsidy_expiration_date,
            },
        }

    if settings.BRAZE_GROUP_EMAIL_FORCE_REMIND_ALL_PENDING_LEARNERS or current_date - timedelta(
        days=BRAZE_GROUPS_EMAIL_CAMPAIGNS_FOURTH_REMINDER_DAY
    ) == datetime.strptime(recent_action_time, "%B %d, %Y"):
        logger.info('get_braze_campaign_properties_5: properties for reminder day {%s} for enterprise customer {%s}',
                    BRAZE_GROUPS_EMAIL_CAMPAIGNS_FOURTH_REMINDER_DAY,
                    enterprise_customer_name)
        return {
            "braze_campaign_id": settings.BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_65_CAMPAIGN,
            "braze_trigger_properties": {
                "enterprise_customer": enterprise_customer_name,
                "catalog_content_count": catalog_count,
                "invitation_end_date": invitation_end_date,
                "subsidy_expiration_datetime": subsidy_expiration_date,
            },
        }

    if settings.BRAZE_GROUP_EMAIL_FORCE_REMIND_ALL_PENDING_LEARNERS or current_date - timedelta(
        days=BRAZE_GROUPS_EMAIL_CAMPAIGNS_FINAL_REMINDER_DAY
    ) == datetime.strptime(recent_action_time, "%B %d, %Y"):
        logger.info('get_braze_campaign_properties_6: properties for reminder day {%s} for enterprise customer {%s}',
                    BRAZE_GROUPS_EMAIL_CAMPAIGNS_FINAL_REMINDER_DAY,
                    enterprise_customer_name)
        return {
            "braze_campaign_id": settings.BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_85_CAMPAIGN,
            "braze_trigger_properties": {
                "catalog_content_count": catalog_count,
                "invitation_end_date": invitation_end_date,
                "subsidy_expiration_datetime": subsidy_expiration_date,
            },
        }

    return None


@shared_task(base=LoggedTaskWithRetry)
def send_group_reminder_emails(pending_enterprise_users):
    """
    Send braze reminder emails to pending learners who have not accepted invitation
    to a group membership.

    Arguments:
        * pending_enterprise_users (list)
    """
    braze_client_instance = BrazeApiClient()
    for pending_enterprise_user in pending_enterprise_users:
        pecu_email = pending_enterprise_user["user_email"]

        recipient = braze_client_instance.create_recipient_no_external_id(
            pecu_email,
        )
        # We need an alias record to exist in Braze `before`
        # sending to any previously-unidentified users.
        braze_client_instance.create_braze_alias(
            [pecu_email],
            ENTERPRISE_BRAZE_ALIAS_LABEL,
        )

        braze_properties = get_braze_campaign_properties(
            pending_enterprise_user["recent_action"],
            pending_enterprise_user["enterprise_customer_name"],
            pending_enterprise_user["catalog_count"],
            pending_enterprise_user["subsidy_expiration_datetime"],
        )
        logger.info(f'get_braze_properties: {braze_properties} for recipient: {recipient}')
        try:
            logger.info(f'Sending braze campaign group reminder email to {recipient}.')
            braze_client_instance.send_campaign_message(
                braze_properties["braze_campaign_id"],
                recipients=[recipient],
                trigger_properties=braze_properties["braze_trigger_properties"],
            )
            logger.info(f'success: sent reminder email {braze_properties["braze_trigger_properties"]}')
        except BrazeClientError as exc:
            message = (
                "Groups learner reminder email could not be sent "
                f"to {recipient} with braze properties {braze_properties}."
            )
            logger.exception(message)
            raise exc
