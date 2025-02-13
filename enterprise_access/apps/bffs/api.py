"""
API methods for retrieving data from downstream services in the bffs app.
"""
import logging

from django.conf import settings
from edx_django_utils.cache import TieredCache

from enterprise_access.apps.api_client.license_manager_client import LicenseManagerUserApiClient
from enterprise_access.apps.api_client.lms_client import LmsApiClient, LmsUserApiClient
from enterprise_access.cache_utils import request_cache, versioned_cache_key

logger = logging.getLogger(__name__)

REQUEST_CACHE_NAMESPACE = 'subsidy_access_policy'

CACHE_MISS = object()


def enterprise_customer_users_cache_key(username):
    return versioned_cache_key('get_subsidy_learners_aggregate_data', username)


def enterprise_customer_cache_key(enterprise_customer_slug, enterprise_customer_uuid):
    return versioned_cache_key('enterprise_customer', enterprise_customer_slug, enterprise_customer_uuid)


def subscription_licenses_cache_key(enterprise_customer_uuid, lms_user_id):
    return versioned_cache_key('get_subscription_licenses_for_learner', enterprise_customer_uuid, lms_user_id)


def default_enterprise_enrollment_intentions_learner_status_cache_key(enterprise_customer_uuid, lms_user_id):
    return versioned_cache_key(
        'get_default_enterprise_enrollment_intentions_learner_status',
        enterprise_customer_uuid,
        lms_user_id
    )


def enterprise_course_enrollments_cache_key(enterprise_customer_uuid, lms_user_id):
    return versioned_cache_key('get_enterprise_course_enrollments', enterprise_customer_uuid, lms_user_id)


def get_and_cache_enterprise_customer_users(request, **kwargs):
    """
    Retrieves and caches enterprise learner data.
    """
    username = request.user.username
    cache_key = enterprise_customer_users_cache_key(username)
    cached_response = request_cache(namespace=REQUEST_CACHE_NAMESPACE).get_cached_response(cache_key)
    if cached_response.is_found:
        logger.info(
            f'enterprise_customer_users cache hit for username {username}'
        )
        return cached_response.value

    client = LmsUserApiClient(request)
    response_payload = client.get_enterprise_customers_for_user(
        username=username,
        **kwargs,
    )
    logger.info(
        'Fetched enterprise customer user for username %s',
        username,
    )
    request_cache(namespace=REQUEST_CACHE_NAMESPACE).set(cache_key, response_payload)
    return response_payload


def get_and_cache_enterprise_customer(
    enterprise_customer_slug=None,
    enterprise_customer_uuid=None,
    timeout=settings.ENTERPRISE_USER_RECORD_CACHE_TIMEOUT,
):
    """
    Retrieves and caches enterprise customer data.
    """
    cache_key = enterprise_customer_cache_key(
        enterprise_customer_slug=enterprise_customer_slug,
        enterprise_customer_uuid=enterprise_customer_uuid,
    )

    cached_response = TieredCache.get_cached_response(cache_key)
    if cached_response.is_found:
        logger.info(
            f'enterprise_customer cache hit for enterprise_customer_slug {enterprise_customer_slug} '
            f'and/or enterprise_customer_uuid {enterprise_customer_uuid}'
        )
        return cached_response.value

    response_payload = LmsApiClient().get_enterprise_customer_data(
        enterprise_customer_uuid=enterprise_customer_uuid,
        enterprise_customer_slug=enterprise_customer_slug,
    )
    TieredCache.set_all_tiers(cache_key, response_payload, timeout)
    return response_payload


def get_and_cache_subscription_licenses_for_learner(
    request,
    enterprise_customer_uuid,
    timeout=settings.SUBSCRIPTION_LICENSES_LEARNER_CACHE_TIMEOUT,
    **kwargs
):
    """
    Retrieves and caches subscription licenses for a learner.
    """
    cache_key = subscription_licenses_cache_key(enterprise_customer_uuid, request.user.id)
    cached_response = TieredCache.get_cached_response(cache_key)
    if cached_response.is_found:
        logger.info(
            f'subscription_licenses cache hit for enterprise_customer_uuid {enterprise_customer_uuid}'
        )
        return cached_response.value

    client = LicenseManagerUserApiClient(request)
    response_payload = client.get_subscription_licenses_for_learner(
        enterprise_customer_uuid=enterprise_customer_uuid,
        **kwargs,
    )
    TieredCache.set_all_tiers(cache_key, response_payload, timeout)
    return response_payload


def get_and_cache_default_enterprise_enrollment_intentions_learner_status(
    request,
    enterprise_customer_uuid,
    timeout=settings.DEFAULT_ENTERPRISE_ENROLLMENT_INTENTIONS_CACHE_TIMEOUT,
):
    """
    Retrieves and caches default enterprise enrollment intentions for a learner.
    This function does not cache this data if it includes any enrollable intentions,
    because we don't want to re-attempt enrollment realization on the second of consecutive requests.
    """
    cache_key = default_enterprise_enrollment_intentions_learner_status_cache_key(
        enterprise_customer_uuid,
        request.user.id,
    )
    cached_response = TieredCache.get_cached_response(cache_key)
    if cached_response.is_found:
        logger.info(
            f'default_enterprise_enrollment_intentions cache hit '
            f'for enterprise_customer_uuid {enterprise_customer_uuid}'
        )
        return cached_response.value

    client = LmsUserApiClient(request)
    response_payload = client.get_default_enterprise_enrollment_intentions_learner_status(
        enterprise_customer_uuid=enterprise_customer_uuid,
    )

    # Don't set in the cache if there are enrollable intentions
    if statuses := response_payload.get('enrollment_statuses', {}):
        needs_enrollment = statuses.get('needs_enrollment', {})
        if needs_enrollment.get('enrollable', []):
            return response_payload

    TieredCache.set_all_tiers(cache_key, response_payload, timeout)
    return response_payload


def get_and_cache_enterprise_course_enrollments(
    request,
    enterprise_customer_uuid,
    timeout=settings.ENTERPRISE_COURSE_ENROLLMENTS_CACHE_TIMEOUT,
    **kwargs
):
    """
    Retrieves and caches enterprise course enrollments for a learner.
    """
    cache_key = enterprise_course_enrollments_cache_key(enterprise_customer_uuid, request.user.id)
    cached_response = TieredCache.get_cached_response(cache_key)
    if cached_response.is_found:
        logger.info(
            f'enterprise_course_enrollments cache hit for enterprise_customer_uuid {enterprise_customer_uuid}'
        )
        return cached_response.value

    client = LmsUserApiClient(request)
    response_payload = client.get_enterprise_course_enrollments(
        enterprise_customer_uuid=enterprise_customer_uuid,
        **kwargs,
    )
    TieredCache.set_all_tiers(cache_key, response_payload, timeout)
    return response_payload


def invalidate_default_enterprise_enrollment_intentions_learner_status_cache(enterprise_customer_uuid, lms_user_id):
    """
    Invalidates the default enterprise enrollment intentions cache for a learner.
    """
    cache_key = default_enterprise_enrollment_intentions_learner_status_cache_key(
        enterprise_customer_uuid,
        lms_user_id,
    )
    TieredCache.delete_all_tiers(cache_key)


def invalidate_enterprise_course_enrollments_cache(enterprise_customer_uuid, lms_user_id):
    """
    Invalidates the enterprise course enrollments cache for a learner.
    """
    cache_key = enterprise_course_enrollments_cache_key(enterprise_customer_uuid, lms_user_id)
    TieredCache.delete_all_tiers(cache_key)


def invalidate_subscription_licenses_cache(enterprise_customer_uuid, lms_user_id):
    """
    Invalidates the subscription licenses cache for a learner.
    """
    cache_key = subscription_licenses_cache_key(enterprise_customer_uuid, lms_user_id)
    TieredCache.delete_all_tiers(cache_key)


def _get_active_enterprise_customer(enterprise_customer_users):
    """
    Get the active enterprise customer user from the list of enterprise customer users.
    """
    active_enterprise_customer_user = next(
        (
            enterprise_customer_user
            for enterprise_customer_user in enterprise_customer_users
            if enterprise_customer_user.get('active', False)
        ),
        None
    )
    if active_enterprise_customer_user:
        return active_enterprise_customer_user.get('enterprise_customer')
    return None


def _get_staff_enterprise_customer(
    user,
    enterprise_customer_slug=None,
    enterprise_customer_uuid=None,
):
    """
    Retrieve enterprise customer metadata from `enterprise-customer` LMS API endpoint
    if there is no enterprise customer user for the request enterprise and the user is staff.
    """
    has_enterprise_customer_slug_or_uuid = enterprise_customer_slug or enterprise_customer_uuid
    if has_enterprise_customer_slug_or_uuid and user.is_staff:
        try:
            staff_enterprise_customer = get_and_cache_enterprise_customer(
                enterprise_customer_uuid=enterprise_customer_uuid,
                enterprise_customer_slug=enterprise_customer_slug,
            )
            return staff_enterprise_customer
        except Exception as exc:
            raise Exception('Error retrieving enterprise customer data') from exc
    return None


def _determine_enterprise_customer_for_display(
    enterprise_customer_slug,
    enterprise_customer_uuid,
    active_enterprise_customer=None,
    requested_enterprise_customer=None,
    staff_enterprise_customer=None,
):
    """
    Determine the enterprise customer user for display.

    Returns:
        tuple(Dict, boolean): The enterprise customer user for display, and a boolean to determine
        whether to update the active enterprise customer to the return value.
    """

    if not enterprise_customer_slug and not enterprise_customer_uuid:
        # No enterprise customer specified in the request, so return the active enterprise customer
        return active_enterprise_customer, False

    # If the requested enterprise does not match the active enterprise customer user's slug/uuid
    # and there is a linked enterprise customer user for the requested enterprise, return the
    # linked enterprise customer. By returning true, we are updating the current active enterprise
    # customer to the requested_enterprise_customer
    request_matches_active_enterprise_customer = _request_matches_active_enterprise_customer(
        active_enterprise_customer=active_enterprise_customer,
        enterprise_customer_slug=enterprise_customer_slug,
        enterprise_customer_uuid=enterprise_customer_uuid,
    )
    if not request_matches_active_enterprise_customer and requested_enterprise_customer:
        return requested_enterprise_customer, True

    # If the request user is staff and the requested enterprise does not match the active enterprise
    # customer user's slug/uuid, return the staff-enterprise customer.
    if staff_enterprise_customer:
        return staff_enterprise_customer, False

    # Otherwise, return the active enterprise customer.
    return active_enterprise_customer, False


def _request_matches_active_enterprise_customer(
    enterprise_customer_slug,
    enterprise_customer_uuid,
    active_enterprise_customer
):
    """
    Check if the request matches the active enterprise customer.
    """
    slug_matches_active_enterprise_customer = (
        active_enterprise_customer and active_enterprise_customer.get('slug') == enterprise_customer_slug
    )
    uuid_matches_active_enterprise_customer = (
        active_enterprise_customer and active_enterprise_customer.get('uuid') == enterprise_customer_uuid
    )
    return (
        slug_matches_active_enterprise_customer or uuid_matches_active_enterprise_customer
    )


def _enterprise_customer_matches_slug_or_uuid(
    enterprise_customer_slug,
    enterprise_customer_uuid,
    enterprise_customer,
):
    """
    Check if the enterprise customer matches the slug or UUID.
    Args:
        enterprise_customer: The enterprise customer data.
    Returns:
        True if the enterprise customer matches the slug or UUID, otherwise False.
    """
    if not enterprise_customer:
        return False

    return (
        enterprise_customer.get('slug') == enterprise_customer_slug or
        enterprise_customer.get('uuid') == enterprise_customer_uuid
    )


def transform_enterprise_customer_users_data(data, request, enterprise_customer_slug, enterprise_customer_uuid):
    """
    Transforms enterprise learner data.
    """
    enterprise_customer_users = data.get('results', [])
    active_enterprise_customer = _get_active_enterprise_customer(enterprise_customer_users)
    enterprise_customer_user_for_requested_customer = next(
        (
            enterprise_customer_user
            for enterprise_customer_user in enterprise_customer_users
            if _enterprise_customer_matches_slug_or_uuid(
                enterprise_customer=enterprise_customer_user.get('enterprise_customer'),
                enterprise_customer_slug=enterprise_customer_slug,
                enterprise_customer_uuid=enterprise_customer_uuid,
            )
        ),
        None
    )

    # If no enterprise customer user is found for the requested customer (i.e., request user not explicitly
    # linked), but the request user is staff, attempt to retrieve enterprise customer metadata from the
    # `/enterprise-customer/` LMS API endpoint instead.
    if not enterprise_customer_user_for_requested_customer:
        staff_enterprise_customer = _get_staff_enterprise_customer(
            user=request.user,
            enterprise_customer_slug=enterprise_customer_slug,
            enterprise_customer_uuid=enterprise_customer_uuid,
        )
    else:
        staff_enterprise_customer = None

    # Determine the enterprise customer user to use for the request.
    requested_enterprise_customer = (
        enterprise_customer_user_for_requested_customer.get('enterprise_customer')
        if enterprise_customer_user_for_requested_customer else None
    )
    enterprise_customer, should_update_active_enterprise_customer_user = _determine_enterprise_customer_for_display(
        enterprise_customer_slug=enterprise_customer_slug,
        enterprise_customer_uuid=enterprise_customer_uuid,
        active_enterprise_customer=active_enterprise_customer,
        requested_enterprise_customer=requested_enterprise_customer,
        staff_enterprise_customer=staff_enterprise_customer,
    )

    return {
        'enterprise_customer': enterprise_customer,
        'active_enterprise_customer': active_enterprise_customer,
        'all_linked_enterprise_customer_users': enterprise_customer_users,
        'staff_enterprise_customer': staff_enterprise_customer,
        'should_update_active_enterprise_customer_user': should_update_active_enterprise_customer_user,
    }
