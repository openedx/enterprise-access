"""
Celery tasks for Enterprise Access API.
"""
import logging

from celery import shared_task
from django.conf import settings

from enterprise_access.apps.api.exceptions import MissingEnterpriseLearnerDataError
from enterprise_access.apps.api.serializers import CouponCodeRequestSerializer, LicenseRequestSerializer
from enterprise_access.apps.api_client.braze_client import BrazeApiClient
from enterprise_access.apps.api_client.ecommerce_client import EcommerceApiClient
from enterprise_access.apps.api_client.license_manager_client import LicenseManagerApiClient
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.subsidy_request.constants import (
    SUBSIDY_TYPE_CHANGE_DECLINATION,
    SegmentEvents,
    SubsidyRequestStates,
    SubsidyTypeChoices
)
from enterprise_access.apps.subsidy_request.models import CouponCodeRequest, LicenseRequest
from enterprise_access.apps.track.segment import track_event
from enterprise_access.tasks import LoggedTaskWithRetry
from enterprise_access.utils import get_aliased_recipient_object_from_email, get_subsidy_model

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

def _get_enterprise_learner_data(lms_user_ids):
    """
    Call LMS API to fetch user data for the given lms_user_ids.

    Args:
        lms_user_ids (list of string): list of lms user ids

    Returns:
        A dict containing user data keyed by lms_user_ids.
    """

    lms_client = LmsApiClient()
    unique_lms_user_ids = set(lms_user_ids)
    learner_data = lms_client.get_enterprise_learner_data(unique_lms_user_ids)

    # Sanity check to make sure that we have user data for each lms_user_id
    missing_lms_user_ids = [
        lms_user_id for lms_user_id in unique_lms_user_ids if lms_user_id
        not in learner_data
    ]

    if missing_lms_user_ids:
        msg = f'Enterprise learner data missing for lms_user_ids: {missing_lms_user_ids}.'
        logger.error(msg)
        raise MissingEnterpriseLearnerDataError(msg)

    return learner_data

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
def send_notification_emails_for_requests(
        subsidy_request_uuids,
        braze_campaign_id,
        subsidy_type,
        braze_trigger_properties=None,
    ):
    """
    Send emails via braze for each subsidy_request

    Args:
        subsidy_request_uuids: list of strings containing subsidy request uuid identifiers
        braze_campaign_id: string representation of braze campaign uuid
        subsidy_type: string representing the type of subsidy (e.g. 'Coupon')
        braze_trigger_properties: dictionary where keys are names of properties that
            match variable names in a braze template, and values are the strings
            you wish to appear in the braze email template where the key (name)
            is found.
    """
    if braze_trigger_properties is None:
        braze_trigger_properties = {}

    braze_client_instance = BrazeApiClient()

    subsidy_model = get_subsidy_model(subsidy_type)
    subsidy_requests = subsidy_model.objects.filter(uuid__in=subsidy_request_uuids)
    lms_user_ids = [subsidy_request.user.lms_user_id for subsidy_request in subsidy_requests]
    enterprise_learner_data = _get_enterprise_learner_data(
        lms_user_ids
    )

    for subsidy_request in subsidy_requests:
        user_email = enterprise_learner_data[subsidy_request.user.lms_user_id]['email']
        recipient = get_aliased_recipient_object_from_email(user_email)

        contact_email = enterprise_learner_data[
            subsidy_request.user.lms_user_id
        ]['enterprise_customer']['contact_email']
        braze_trigger_properties['contact_email'] = contact_email

        enterprise_slug = enterprise_learner_data[subsidy_request.user.lms_user_id]['enterprise_customer']['slug']
        course_about_page_url = '{}/{}/course/{}'.format(
            settings.ENTERPRISE_LEARNER_PORTAL_URL,
            enterprise_slug,
            subsidy_request.course_id
        )
        braze_trigger_properties['course_about_page_url'] = course_about_page_url

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
    assigned_codes = { assignment['user_email']: assignment['code'] for assignment in response['offer_assignments'] }

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

    CouponCodeRequest.bulk_update(coupon_code_requests, ['state', 'coupon_id', 'coupon_code'])
