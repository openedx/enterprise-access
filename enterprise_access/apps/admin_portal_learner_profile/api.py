"""
API for admin_portal_learner_profile operations.
"""
import logging

import requests

from ..api_client.license_manager_client import LicenseManagerApiClient
from ..api_client.lms_client import LmsApiClient

logger = logging.getLogger(__name__)


def get_learner_subscriptions(enterprise_customer_uuid, user_email):
    """
    Fetches subscription licenses for a learner.
    """
    try:
        license_manager_client = LicenseManagerApiClient()
        return license_manager_client.get_learner_subscription_licenses_for_admin(
            enterprise_customer_uuid=enterprise_customer_uuid,
            user_email=user_email
        )
    except requests.exceptions.HTTPError as exc:
        logger.error(f"Failed to fetch subscriptions for {user_email}: {exc}")
        return {'error': 'Failed to fetch subscriptions'}


def get_group_memberships(enterprise_customer_uuid, lms_user_id):
    """
    Fetches group memberships for a learner.
    """
    try:
        lms_client = LmsApiClient()
        return lms_client.get_enterprise_group_memberships_for_learner(
            enterprise_uuid=enterprise_customer_uuid,
            lms_user_id=lms_user_id
        )
    except requests.exceptions.HTTPError as exc:
        logger.error(f"Failed to fetch group memberships for {lms_user_id}: {exc}")
        return {'error': 'Failed to fetch group memberships'}


def get_enrollments(enterprise_customer_uuid, lms_user_id):
    """
    Fetches enrollments for a learner.
    """
    try:
        lms_client = LmsApiClient()
        return lms_client.get_course_enrollments_for_learner_profile(
            enterprise_uuid=enterprise_customer_uuid,
            lms_user_id=lms_user_id
        )
    except requests.exceptions.HTTPError as exc:
        logger.error(f"Failed to fetch enrollments for {lms_user_id}: {exc}")
        return {'error': 'Failed to fetch enrollments'}
