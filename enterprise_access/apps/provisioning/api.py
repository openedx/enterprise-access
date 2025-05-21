"""
Python API for provisioning operations.
"""
import logging
from operator import itemgetter

from ..api_client.license_manager_client import LicenseManagerApiClient
from ..api_client.lms_client import LmsApiClient

logger = logging.getLogger(__name__)


def get_or_create_enterprise_customer(*, name, slug, country, **kwargs):
    """
    Get or creates an enterprise customer with the provided arguments.
    """
    client = LmsApiClient()
    existing_customer = client.get_enterprise_customer_data(enterprise_customer_slug=slug)
    if existing_customer:
        _warn_if_fields_mismatch(existing_customer, name, slug, country)
        logger.info('Provisioning: enterprise_customer slug %s already exists', slug)
        return existing_customer

    created_customer = client.create_enterprise_customer(
        name=name, slug=slug, country=country, **kwargs,
    )
    logger.info('Provisioning: created enterprise customer with slug %s', slug)
    return created_customer


def _warn_if_fields_mismatch(existing_customer, name, slug, country):
    """
    Logs a warning if specific requested fields for the customer don't match the existing customer record.
    """
    for field_name, requested_value in [('name', name), ('country', country)]:
        if (existing_value := existing_customer.get(field_name)) != requested_value:
            logger.warning(
                'Provisioning: existing customer with slug %s had field %s with requested value %s, '
                'but existing value was %s',
                slug, field_name, requested_value, existing_value,
            )


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
        result = client.create_enterprise_admin_user(enterprise_customer_uuid, user_email)
        created_admins.append(result)
        logger.info(
            'Provisioning: created admin %s for customer %s',
            user_email,
            enterprise_customer_uuid,
        )

    existing_admin_result = [{'user_email': email} for email in existing_admin_emails]
    existing_pending_admin_result = [{'user_email': email} for email in existing_pending_admin_emails]
    return {
        'created_admins': sorted(created_admins, key=itemgetter('user_email')),
        'existing_admins': sorted(
            existing_pending_admin_result + existing_admin_result,
            key=itemgetter('user_email'),
        ),
    }


def get_or_create_enterprise_catalog(enterprise_customer_uuid, catalog_title, catalog_query_id, **kwargs):
    """
    Get or creates an enterprise catalog with the provided arguments.
    """
    client = LmsApiClient()
    existing_catalogs = client.get_enterprise_catalogs(
        enterprise_customer_uuid=enterprise_customer_uuid,
        catalog_query_id=catalog_query_id,
    )
    if existing_catalogs:
        matching_catalog = existing_catalogs[0]
        logger.info('Provisioning: enterprise catalog with uuid %s already exists', matching_catalog.get('uuid'))
        return matching_catalog

    created_catalog = client.create_enterprise_catalog(
        enterprise_customer_uuid=enterprise_customer_uuid,
        catalog_title=catalog_title,
        catalog_query_id=catalog_query_id,
    )
    logger.info('Provisioning: created enterprise catalog with uuid %s', created_catalog.get('uuid'))
    return created_catalog


def get_or_create_customer_agreement(enterprise_customer_uuid, customer_slug, default_catalog_uuid=None, **kwargs):
    """
    Get or create a customer agreement record from the license-manager service.
    """
    client = LicenseManagerApiClient()
    existing_agreement = client.get_customer_agreement(enterprise_customer_uuid)
    if existing_agreement:
        logger.info('Provisioning: customer agreement with uuid %s already_exists', existing_agreement.get('uuid'))
        return existing_agreement

    created_agreement = client.create_customer_agreement(
        enterprise_customer_uuid,
        customer_slug,
        default_catalog_uuid=default_catalog_uuid,
        **kwargs,
    )
    logger.info('Provisioning: created customer agreement with uuid %s', created_agreement.get('uuid'))
    return created_agreement


def get_or_create_subscription_plan(
    customer_agreement_uuid, existing_subscription_list, plan_title, catalog_uuid, opp_line_item,
    start_date, expiration_date, desired_num_licenses, product_id, **kwargs
):
    """
    Get or create a new subscription plan, provided an existing customer agreement dictionary.
    """
    matching_subscription = next((
        _sub for _sub in existing_subscription_list
        if _sub.get('salesforce_opportunity_line_item') == opp_line_item
    ), None)
    if matching_subscription:
        logger.info(
            'Provisioning: subscription plan with uuid %s and salesforce_opportunity_line_item %s already exists',
            matching_subscription['uuid'], matching_subscription['salesforce_opportunity_line_item']
        )
        return matching_subscription

    client = LicenseManagerApiClient()
    created_subscription = client.create_subscription_plan(
        customer_agreement_uuid=customer_agreement_uuid,
        title=plan_title,
        salesforce_opportunity_line_item=opp_line_item,
        start_date=start_date,
        expiration_date=expiration_date,
        desired_num_licenses=desired_num_licenses,
        enterprise_catalog_uuid=catalog_uuid,
        product_id=product_id,
        **kwargs,
    )
    logger.info(
        'Provisioning: created new subscription plan with uuid %s and salesforce_opportunity_line_item %s',
        created_subscription['uuid'], created_subscription['salesforce_opportunity_line_item'],
    )
    return created_subscription
