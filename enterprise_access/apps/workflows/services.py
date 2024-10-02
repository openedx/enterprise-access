"""
Services for the workflows app.
"""

import logging
import uuid

logger = logging.getLogger(__name__)

ENTERPRISE_CATALOG_UUID = str(uuid.uuid4())


def activated_subscription_licenses(*args, **kwargs):
    """
    Mocked function to fetch subscription licenses for a given process.
    Replace this with an actual API call to retrieve licenses.
    """
    logger.info(f"Fetching subscription licenses for process: {kwargs.get('process_id')}")

    return [
        {
            "uuid": str(uuid.uuid4()),
            "subscription_plan_uuid": str(uuid.uuid4()),
            "status": "activated",
            "enterprise_catalog_uuid": ENTERPRISE_CATALOG_UUID,
        },
    ]


def default_enterprise_course_enrollments(*args, **kwargs):
    """
    Mocked function to fetch default enterprise course enrollments for a process.
    Replace this with an actual API call to retrieve enrollments.
    """
    logger.info(
        f"Fetching default enterprise course enrollments for process: {kwargs.get('process_id')}"
    )

    return [
        # default top-level course enrollment
        {
            "uuid":  str(uuid.uuid4()),
            "content_key": "edX+DemoX",
            "course_run_key": "course-v1:edX+DemoX+Demo_Course",  # advertised course run
            "content_metadata": {
                "start_date": "...",
                "end_date": "...",
                "enroll_by_date": "...",
                "enroll_start_date": "...",
                "content_price": 123,
            },
            "applicable_enterprise_catalog_uuids": [ENTERPRISE_CATALOG_UUID],
        },
    ]


def enroll_courses(redeemable_enrollments, *args, **kwargs):
    """
    Mocked function to enroll users in redeemable courses.
    Replace this with an actual API call to enroll the user.
    """

    logger.info(f"Enrolling users in redeemable courses: {redeemable_enrollments}")

    return {
        "status": "success",
        "message": "Enrolled users in redeemable courses",
    }
