"""
Celery tasks for Enterprise Access API.
"""
from datetime import datetime, timedelta
import logging

from celery import shared_task
from django.conf import settings

from enterprise_access.apps.api_client.enterprise_catalog_client import EnterpriseCatalogApiClient
from enterprise_access.apps.api.serializers import CouponCodeRequestSerializer, LicenseRequestSerializer
from enterprise_access.apps.api_client.braze_client import BrazeApiClient, ENTERPRISE_BRAZE_ALIAS_LABEL
from enterprise_access.apps.api_client.ecommerce_client import EcommerceApiClient
from enterprise_access.apps.api_client.license_manager_client import LicenseManagerApiClient
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.core.models import User
from enterprise_access.apps.events.signals import COUPON_CODE_REQUEST_APPROVED
from enterprise_access.apps.events.utils import send_coupon_code_request_event_to_event_bus
from enterprise_access.apps.subsidy_request.constants import (
    SUBSIDY_TYPE_CHANGE_DECLINATION,
    SegmentEvents,
    SubsidyRequestStates,
    SubsidyTypeChoices
)
from enterprise_access.apps.subsidy_request.models import CouponCodeRequest, LicenseRequest
from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy
from enterprise_access.apps.track.segment import track_event
from enterprise_access.tasks import LoggedTaskWithRetry
from enterprise_access.utils import get_subsidy_model

logger = logging.getLogger(__name__)


def _get_serializer_by_subsidy_type(subsidy_type):
    """
    Returns serializer for LicenseRequest or CouponCodeRequest.
    """
    if subsidy_type == SubsidyTypeChoices.LICENSE:
        return LicenseRequestSerializer
    if subsidy_type == SubsidyTypeChoices.COUPON:
        return CouponCodeRequestSerializer
    return None


@shared_task(base=LoggedTaskWithRetry)
def decline_enterprise_subsidy_requests_task(subsidy_request_uuids, subsidy_type):
    """
    Decline all subsidy requests of the given type for the enterprise customer.
    """

    subsidy_model = get_subsidy_model(subsidy_type)
    serializer = _get_serializer_by_subsidy_type(subsidy_type)
    event_name = SegmentEvents.SUBSIDY_REQUEST_DECLINED[subsidy_type]
    subsidy_requests = subsidy_model.objects.filter(uuid__in=subsidy_request_uuids).select_related('user', 'reviewer')

    # Why I don't used subsidy_requests.update() #
    # When you run .update() on a queryset, you "lose" the objects, because by
    # nature of them being updated in the DB (update runs raw SQL),
    # they no longer are returned by the original
    # queryset. To make sure we send out notifications for the exact objects we are
    # declining here, I've opted to use a save() in a for-loop (which the django
    # docs even recommend in some cases).
    for subsidy_request in subsidy_requests:
        logger.info(f'Declining subsidy {subsidy_request} because subsidy type changed on Configuration.')
        subsidy_request.state = SubsidyRequestStates.DECLINED
        subsidy_request.decline_reason = SUBSIDY_TYPE_CHANGE_DECLINATION
        subsidy_request.save()
        track_event(
            lms_user_id=subsidy_request.user.lms_user_id,
            event_name=event_name,
            properties=serializer(subsidy_request).data
        )


@shared_task(base=LoggedTaskWithRetry)
def send_notification_email_for_request(
    subsidy_request_uuid,
    braze_campaign_id,
    subsidy_type,
    braze_trigger_properties=None,
):
    """
    Send email via braze for the subsidy_request

    Args:
        subsidy_request_uuid: (string) the subsidy request uuid
        braze_campaign_id: (string) the braze campaign uuid
        subsidy_type: (string) the type of subsidy (i.e. 'coupon')
        braze_trigger_properties: (dict) dictionary where keys are names of properties that
            match variable names in a braze template, and values are the strings
            you wish to appear in the braze email template where the key (name)
            is found.
    """
    if braze_trigger_properties is None:
        braze_trigger_properties = {}

    braze_client_instance = BrazeApiClient()

    subsidy_model = get_subsidy_model(subsidy_type)

    try:
        subsidy_request = subsidy_model.objects.get(uuid=subsidy_request_uuid)
    except subsidy_model.DoesNotExist:
        logger.warning(f'{subsidy_type} request with uuid: {subsidy_request_uuid} does not exist.')
        return

    lms_client = LmsApiClient()

    user = subsidy_request.user
    recipient = braze_client_instance.create_recipient(user_email=user.email, lms_user_id=user.lms_user_id)
    enterprise_customer_data = lms_client.get_enterprise_customer_data(subsidy_request.enterprise_customer_uuid)

    admin_emails = [user['email'] for user in enterprise_customer_data['admin_users']]
    braze_trigger_properties['contact_admin_link'] = braze_client_instance.generate_mailto_link(admin_emails)

    enterprise_slug = enterprise_customer_data['slug']
    course_about_page_url = '{}/{}/course/{}'.format(
        settings.ENTERPRISE_LEARNER_PORTAL_URL,
        enterprise_slug,
        subsidy_request.course_id
    )
    braze_trigger_properties['course_about_page_url'] = course_about_page_url
    braze_trigger_properties['course_title'] = subsidy_request.course_title

    logger.info(f'Sending braze campaign message for subsidy request {subsidy_request}')
    braze_client_instance.send_campaign_message(
        braze_campaign_id,
        recipients=[recipient],
        trigger_properties=braze_trigger_properties,
    )


@shared_task(base=LoggedTaskWithRetry)
def assign_licenses_task(license_request_uuids, subscription_uuid):
    """
    Call License Manager API to assign licenses for the given license requests.

    Args:
        license_request_uuids (list of UUID): list of license request UUIDs to assign licenses for
        subscription_uuid (UUID): the UUID of the subscription to assign licenses from

    Returns:
        A dict representing license assignment results in the form of:
            {
                'license_request_uuids' (list of UUID): license request UUIDs that were processed,
                'assigned_licenses' (dict): dict containing licenses assigned keyed by lms_user_ids,
                'subscription_uuid' (UUID): the UUID of the subscription that licenses were assigned from
            }
    """

    license_requests = LicenseRequest.objects.filter(
        uuid__in=license_request_uuids,
        state__in=[SubsidyRequestStates.PENDING, SubsidyRequestStates.ERROR]
    ).select_related('user')

    if not license_requests:
        logger.info(f'No pending/errored license requests with uuids: {license_request_uuids} found.')
        return None

    user_emails = [license_request.user.email for license_request in license_requests]

    license_manager_api_client = LicenseManagerApiClient()
    response = license_manager_api_client.assign_licenses(user_emails, subscription_uuid)
    assigned_licenses = {
        assignment['user_email']: assignment['license'] for assignment in response['license_assignments']
    }

    return {
        'license_request_uuids': license_request_uuids,
        'assigned_licenses': assigned_licenses,
        'subscription_uuid': subscription_uuid
    }


@shared_task(base=LoggedTaskWithRetry)
def update_license_requests_after_assignments_task(license_assignment_results):
    """
    Update license requests after license assignments.

    Args:
        license_assignment_results (dict): a dict representing license assignment results in the form of:
            {
                'license_request_uuids' (list of UUID): license request UUIDs that were processed,
                'assigned_licenses' (dict): dict containing licenses assigned keyed by lms_user_ids,
                'subscription_uuid' (UUID): the UUID of the subscription that licenses were assigned from
            }

    Returns:
        None
    """

    if not license_assignment_results:
        logger.info('No license assignment results, skipping updates.')
        return

    license_request_uuids = license_assignment_results['license_request_uuids']
    assigned_licenses = license_assignment_results['assigned_licenses']
    subscription_uuid = license_assignment_results['subscription_uuid']

    license_requests = LicenseRequest.objects.filter(
        uuid__in=license_request_uuids
    ).select_related('user', 'reviewer')

    for license_request in license_requests:
        user_email = license_request.user.email

        if not assigned_licenses.get(user_email):
            msg = f'License was not assigned for {license_request.uuid}. {user_email} already had a license assigned.'
            logger.info(msg)
            license_request.state = SubsidyRequestStates.ERROR
        else:
            license_request.state = SubsidyRequestStates.APPROVED
            license_request.subscription_plan_uuid = subscription_uuid
            license_request.license_uuid = assigned_licenses[user_email]
            track_event(
                lms_user_id=license_request.user.lms_user_id,
                event_name=SegmentEvents.LICENSE_REQUEST_APPROVED,
                properties=LicenseRequestSerializer(license_request).data
            )

    LicenseRequest.bulk_update(license_requests, ['state', 'subscription_plan_uuid', 'license_uuid'])


@shared_task(base=LoggedTaskWithRetry)
def assign_coupon_codes_task(coupon_code_request_uuids, coupon_id):
    """
    Call Ecommerce API to assign coupon codes for the given coupon code requests.

    Args:
        coupon_code_request_uuids (list of UUID): list of coupon code request UUIDs to assign coupon codes for
        coupon_id (int): the id of the coupon to assign codes from

    Returns:
        A dict representing coupon code assignment results in the form of:
            {
                'coupon_code_request_uuids' (list of UUID): coupon code request UUIDs that were processed,
                'assigned_codes' (dict): dict containing codes assigned keyed by lms_user_ids,
                'coupon_id' (int): the id of the coupon that codes were assigned from
            }
    """

    coupon_code_requests = CouponCodeRequest.objects.filter(
        uuid__in=coupon_code_request_uuids,
        state=SubsidyRequestStates.PENDING
    ).select_related('user')

    if not coupon_code_requests:
        logger.info(f'No pending/errored coupon code requests with uuids: {coupon_code_requests} found.')
        return None

    user_emails = [coupon_code_request.user.email for coupon_code_request in coupon_code_requests]

    ecommerce_api_client = EcommerceApiClient()
    response = ecommerce_api_client.assign_coupon_codes(user_emails, coupon_id)
    assigned_codes = {assignment['user_email']: assignment['code'] for assignment in response['offer_assignments']}

    return {
        'coupon_code_request_uuids': coupon_code_request_uuids,
        'assigned_codes': assigned_codes,
        'coupon_id': coupon_id
    }


@shared_task(base=LoggedTaskWithRetry)
def update_coupon_code_requests_after_assignments_task(coupon_code_assignment_results):
    """
    Update coupon code requests after coupon code assignments.

    Args:
        coupon_code_assignment_results (dict): a dict representing coupon code assignment results in the form of:
            {
                'coupon_code_request_uuids' (list of UUID): coupon code request UUIDs that were processed,
                'assigned_codes' (dict): dict containing codes assigned keyed by lms_user_ids,
                'coupon_id' (int): the id of the coupon that codes were assigned from
            }

    Returns:
        None
    """

    if not coupon_code_assignment_results:
        logger.info('No coupon code assignment results, skipping updates.')
        return

    coupon_code_request_uuids = coupon_code_assignment_results['coupon_code_request_uuids']
    assigned_codes = coupon_code_assignment_results['assigned_codes']
    coupon_id = coupon_code_assignment_results['coupon_id']

    coupon_code_requests = CouponCodeRequest.objects.filter(
        uuid__in=coupon_code_request_uuids
    ).select_related('user', 'reviewer')

    for coupon_code_request in coupon_code_requests:
        coupon_code_request.state = SubsidyRequestStates.APPROVED
        coupon_code_request.coupon_id = coupon_id
        coupon_code_request.coupon_code = assigned_codes[coupon_code_request.user.email]

        track_event(
            lms_user_id=coupon_code_request.user.lms_user_id,
            event_name=SegmentEvents.COUPON_CODE_REQUEST_APPROVED,
            properties=CouponCodeRequestSerializer(coupon_code_request).data
        )

        if settings.KAFKA_ENABLED:  # pragma: no cover
            send_coupon_code_request_event_to_event_bus(
                COUPON_CODE_REQUEST_APPROVED.event_type,
                CouponCodeRequestSerializer(coupon_code_request).data
            )

    CouponCodeRequest.bulk_update(coupon_code_requests, ['state', 'coupon_id', 'coupon_code'])


@shared_task(base=LoggedTaskWithRetry)
def unlink_users_from_enterprise_task(enterprise_customer_uuid, lms_user_ids):
    """
    Unlink users with the given lms user ids from the enterprise.

    Args:
        enterprise_customer_uuid (UUID): the UUID of the enterprise customer
        lms_user_ids (list of str): lms user ids of the users to unlink

    Returns:
        None
    """

    user_emails = list(
        User.objects.filter(
            lms_user_id__in=lms_user_ids
        ).values_list('email', flat=True)
    )

    if user_emails:
        lms_client = LmsApiClient()
        lms_client.unlink_users_from_enterprise(
            enterprise_customer_uuid=enterprise_customer_uuid,
            user_emails=user_emails,
            is_relinkable=False
        )

