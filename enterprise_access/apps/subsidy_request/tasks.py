"""
Tasks for subsidy requests app.
"""

import logging

from celery import shared_task
from django.apps import apps

from enterprise_access.apps.api_client.discovery_client import DiscoveryApiClient
from enterprise_access.apps.core.tasks import LoggedTaskWithRetry
from enterprise_access.apps.subsidy_request.constants import SubsidyTypeChoices

logger = logging.getLogger(__name__)


@shared_task(base=LoggedTaskWithRetry)
def update_course_title_for_subsidy_request_task(subsidy_type, subsidy_request_uuid):
    """
    Get course_title from lms and update subsidy_request with it
    """
    if subsidy_type == SubsidyTypeChoices.LICENSE:
        subsidy_model = apps.get_model('subsidy_request.LicenseRequest')
    else:
        subsidy_model = apps.get_model('subsidy_request.CouponCodeRequest')
    subsidy_request = subsidy_model.objects.get(uuid=subsidy_request_uuid)

    discovery_client = DiscoveryApiClient()
    course_data = discovery_client.get_course_data(subsidy_request.course_id)
    subsidy_request.course_title = course_data['title']

    # Use bulk_update so we don't trigger save() again
    subsidy_model.bulk_update([subsidy_request], ['course_title'])
