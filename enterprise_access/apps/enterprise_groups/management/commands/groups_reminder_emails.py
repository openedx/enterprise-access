"""
Django management command for sending reminder emails to pending users to accept groups invitation.
"""

import logging

from django.core.management import BaseCommand

from enterprise_access.apps.api_client.enterprise_catalog_client import EnterpriseCatalogApiClient
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.enterprise_groups.tasks import send_group_reminder_emails
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
        lms_client = LmsApiClient()
        enterprise_catalog_client = EnterpriseCatalogApiClient()
        policy_group_associations = PolicyGroupAssociation.objects.all()
        for policy_group_association in policy_group_associations:
            pecu_email_properties = []
            enterprise_group_uuid = policy_group_association.enterprise_group_uuid
            enterprise_customer_uuid = policy_group_association.subsidy_access_policy.enterprise_customer_uuid
            pending_enterprise_customer_users = (
                lms_client.get_pending_enterprise_group_memberships(
                    enterprise_group_uuid
                )
            )
            enterprise_customer_data = (
                lms_client.get_enterprise_customer_data(enterprise_customer_uuid)
            )
            subsidy_expiration_datetime = (
                policy_group_association.subsidy_access_policy.subsidy_expiration_datetime
            )
            catalog_uuid = policy_group_association.subsidy_access_policy.catalog_uuid
            catalog_count = enterprise_catalog_client.catalog_content_metadata(
                catalog_uuid
            )['count']

            for pending_enterprise_customer_user in pending_enterprise_customer_users:
                pending_enterprise_customer_user["subsidy_expiration_datetime"] = (
                    subsidy_expiration_datetime
                )
                pending_enterprise_customer_user["catalog_count"] = catalog_count
                pending_enterprise_customer_user["enterprise_customer_name"] = enterprise_customer_data["name"]
                pecu_email_properties.append(pending_enterprise_customer_user)
            send_group_reminder_emails.delay(pecu_email_properties)
