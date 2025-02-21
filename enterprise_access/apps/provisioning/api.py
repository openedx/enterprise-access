"""
Python API for provisioning operations.
"""
import logging

from ..api_client.lms_client import LmsApiClient

logger = logging.getLogger(__name__)


def get_or_create_enterprise_customer(*, name, slug, country, **kwargs):
    """
    Get or creates an enterprise customer with the provided arguments.
    """
    client = LmsApiClient()
    existing_customer = client.get_enterprise_customer_data(enterprise_customer_slug=slug)
    if existing_customer:
        logger.info('Provisioning: enterprise_customer slug %s already exists', slug)
        return existing_customer

    created_customer = client.create_enterprise_customer(
        name=name, slug=slug, country=country, **kwargs,
    )
    logger.info('Provisioning: created enterprise customer with slug %s', slug)


def get_or_create_enterprise_admin_users(enterprise_customer_uuid, user_emails):
    """
    Creates pending admin records from the given ``user_email`` for the customer
    identified by ``enterprise_customer_uuid``.
    """
    client = LmsApiClient()
    existing_admins = client.get_enterprise_admin_users(enterprise_customer_uuid)
    existing_admin_emails = {record['email'] for record in existing_admins}
    logger.info(
        'Provisioning: customer %s has existing admin emails %s',
        enterprise_customer_uuid,
        existing_admin_emails,
    )

    existing_pending_admins = client.get_enterprise_pending_admin_users(enterprise_customer_uuid)
    existing_pending_admin_emails = {record['user_email'] for record in existing_pending_admins}
    logger.info(
        'Provisioning: customer %s has existing pending admin emails %s',
        enterprise_customer_uuid,
        existing_pending_admin_emails,
    )

    user_emails_to_create = list(
        (set(user_emails) - existing_admin_emails) - existing_pending_admin_emails
    )

    created_admins = []
    for user_email in user_emails_to_create:
        created_admins.append(
            client.create_enterprise_admin_user(enterprise_customer_uuid, user_email)
        )
        logger.info(
            'Provisioning: created admin %s for customer %s',
            user_email,
            enterprise_customer_uuid,
        )

    return created_admins + existing_pending_admins
