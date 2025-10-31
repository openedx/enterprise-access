"""
Python API for provisioning operations.
"""
import logging
from operator import itemgetter

from rest_framework import status

from ..api_client.exceptions import APIClientException
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
    Creates admin records from the given ``user_email`` for the customer
    identified by ``enterprise_customer_uuid``.
    If a user record corresponding to the provided email(s) does not exist,
    we attempt to create a pending enterprise admin record for that email.
    """
    client = LmsApiClient()
    existing_admins = client.get_enterprise_admin_users(enterprise_customer_uuid)
    existing_admin_emails = {record['email'] for record in existing_admins}
    logger.info(
        'Provisioning: customer %s has existing admin emails %s',
        enterprise_customer_uuid,
        existing_admin_emails,
    )

    user_emails_to_create = list(
        set(user_emails) - existing_admin_emails
    )

    created_admins = []
    for user_email in user_emails_to_create:
        try:
            result = client.create_enterprise_admin_user(enterprise_customer_uuid, user_email)
            # The endpoint to create real admins doesn't currently return the email address in the
            # response payload. Since we know it succeeds, just add the requested email to the created list.
            # Structure it as a dict in case we need to add additional fields from the response payload later.
            created_admins.append({'user_email': user_email})
            logger.info(
                'Provisioning: created admin %s for customer %s', user_email, enterprise_customer_uuid,
            )
        except APIClientException as exc:
            if exc.__cause__.response.status_code == status.HTTP_404_NOT_FOUND:  # pylint: disable=no-member
                result = _try_create_pending_admin(client, enterprise_customer_uuid, user_email)
                if result:
                    created_admins.append({'user_email': user_email})
                    logger.info(
                        'Provisioning: created pending admin %s for customer %s',
                        user_email, enterprise_customer_uuid,
                    )

    existing_admin_result = [{'user_email': email} for email in existing_admin_emails]

    return {
        'created_admins': sorted(created_admins, key=itemgetter('user_email')),
        'existing_admins': sorted(existing_admin_result, key=itemgetter('user_email')),
    }


def _try_create_pending_admin(client, enterprise_customer_uuid, user_email):
    """
    Helper to safely attempt creation of a *pending* admin user.
    """
    try:
        result = client.create_enterprise_pending_admin_user(
            enterprise_customer_uuid, user_email,
        )
        return result
    except APIClientException as exc:
        logger.warning(
            'Provisioning: could not create concrete or pending admin %s for customer %s because %s',
            user_email,
            enterprise_customer_uuid,
            exc,
        )
        return None


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
    customer_agreement_uuid: str,
    existing_subscription_list: list[dict],
    plan_title: str,
    catalog_uuid: str | None,
    opp_line_item: str | None,
    start_date: str,
    expiration_date: str,
    desired_num_licenses: int,
    product_id: int | None,
    **kwargs
):
    """
    Get or create a new subscription plan, provided an existing customer agreement dictionary.
    """
    matching_subscription = next((
        _sub for _sub in existing_subscription_list
        # Intentionally treat None == None as "matching".
        if _sub.get('salesforce_opportunity_line_item') == opp_line_item
    ), None)
    if matching_subscription:
        logger.info(
            'Provisioning: subscription plan with uuid %s and salesforce_opportunity_line_item %s already exists',
            matching_subscription['uuid'], matching_subscription['salesforce_opportunity_line_item']
        )
        if not opp_line_item:
            logger.info(
                "Provisioning: Existing subscription plan found with null salesforce_opportunity_line_item.  "
                "This is normal as long as it has a reasonable start date.  "
                "New plan start date: %s",
                matching_subscription['start_date'],
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
        (
            'Provisioning: created new subscription plan with '
            'uuid %s and salesforce_opportunity_line_item %s and product_id %s'
        ),
        created_subscription['uuid'],
        created_subscription.get('salesforce_opportunity_line_item'),
        created_subscription.get('product'),
    )
    return created_subscription


def get_or_create_subscription_plan_renewal(
    prior_subscription_plan_uuid: str,
    renewed_subscription_plan_uuid: str,
    salesforce_opportunity_line_item_id: str | None,
    effective_date: str,
    renewed_expiration_date: str,
    number_of_licenses: int,
    **kwargs
) -> dict:
    """
    Get or create a new subscription plan renewal.
    """
    client = LicenseManagerApiClient()
    created_renewal = client.create_subscription_plan_renewal(
        prior_subscription_plan_uuid=prior_subscription_plan_uuid,
        renewed_subscription_plan_uuid=renewed_subscription_plan_uuid,
        salesforce_opportunity_line_item_id=salesforce_opportunity_line_item_id,
        effective_date=effective_date,
        renewed_expiration_date=renewed_expiration_date,
        number_of_licenses=number_of_licenses,
        # Disable conventional batch processing. Self-Service Purchasing feature has its
        # own processes for triggering renewal.
        exempt_from_batch_processing=True,
        **kwargs,
    )
    logger.info(
        (
            'Provisioning: created new renewal plan with '
            'id=%s and '
            'salesforce_opportunity_line_item_id=%s and '
            'prior_subscription_plan_uuid=%s and '
            'renewed_subscription_plan_uuid=%s'
        ),
        created_renewal['id'],
        created_renewal['salesforce_opportunity_id'],
        created_renewal['prior_subscription_plan_uuid'],
        created_renewal['renewed_subscription_plan_uuid'],
    )
    return created_renewal
