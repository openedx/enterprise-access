"""
Utils for any app in the enterprise-access project.
"""
import logging
import traceback
from datetime import datetime, timedelta
from decimal import Decimal

from django.apps import apps
from pytz import UTC

from enterprise_access.apps.content_assignments.constants import AssignmentAutomaticExpiredReason
from enterprise_access.apps.content_assignments.content_metadata_api import (
    get_content_metadata_for_assignments,
    parse_datetime_string
)
from enterprise_access.apps.enterprise_groups.constants import (
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_FINAL_REMINDER_DAY,
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_FIRST_REMINDER_DAY,
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_FOURTH_REMINDER_DAY,
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_SECOND_REMINDER_DAY,
    BRAZE_GROUPS_EMAIL_CAMPAIGNS_THIRD_REMINDER_DAY
)
from enterprise_access.apps.subsidy_request.constants import SubsidyTypeChoices

_MEMO_MISS = object()

logger = logging.getLogger(__name__)


def get_subsidy_model(subsidy_type):
    """
    Get subsidy model from subsidy_type string

    Args:
        subsidy_type (string): string name of subsidy
    Returns:
        Class of a model object
    """
    subsidy_model = None
    if subsidy_type == SubsidyTypeChoices.COUPON:
        subsidy_model = apps.get_model('subsidy_request.CouponCodeRequest')
    if subsidy_type == SubsidyTypeChoices.LICENSE:
        subsidy_model = apps.get_model('subsidy_request.LicenseRequest')
    if subsidy_type == SubsidyTypeChoices.LEARNER_CREDIT:
        subsidy_model = apps.get_model('subsidy_request.LearnerCreditRequest')
    return subsidy_model


def is_not_none(thing):
    return thing is not None


def is_none(thing):
    return thing is None


def localized_utcnow():
    """Helper function to return localized utcnow()."""
    return datetime.now().replace(tzinfo=UTC)


def chunks(a_list, chunk_size):
    """
    Helper to break a list up into chunks. Returns a generator of lists.
    """
    for i in range(0, len(a_list), chunk_size):
        yield a_list[i:i + chunk_size]


def format_traceback(exception):
    trace = ''.join(traceback.format_tb(exception.__traceback__))
    return f'{exception}\n{trace}'


def _get_subsidy_expiration(assignment):
    """
    Returns the datetime at which the subsidy for this assignment expires.
    """
    subsidy_expiration_datetime = (
        assignment.assignment_configuration.policy.subsidy_expiration_datetime
    )
    if not subsidy_expiration_datetime:
        return None
    subsidy_expiration_datetime = parse_datetime_string(subsidy_expiration_datetime).replace(tzinfo=UTC)
    return subsidy_expiration_datetime


def _get_enrollment_deadline_date(assignment, content_metadata):
    """
    Helper to get the enrollment end date from a content metadata record.
    """
    if not content_metadata:
        return None

    normalized_metadata = get_normalized_metadata_for_assignment(assignment, content_metadata)
    enrollment_end_date_str = normalized_metadata.get('enroll_by_date')
    try:
        datetime_obj = parse_datetime_string(enrollment_end_date_str)
        if datetime_obj:
            return datetime_obj.replace(tzinfo=UTC)
    except ValueError:
        logger.warning(
            'Bad datetime format for %s, value: %s',
            content_metadata.get('key'),
            enrollment_end_date_str,
        )
    return None


def get_automatic_expiration_date_and_reason(
    assignment,
    content_metadata: dict = None
):
    """
    For the given assignment, returns the date at which this assignment expires due to:
    * subsidy expiration
    * content enrollment deadline
    * 90-day timeout from allocation

    Whichever of the three above dates is the earliest is returned, along with the reason
    for the expiration as a dictionary.

    Arguments:
        assignment (LearnerContentAssignment): The assignment to check for expiration.
        [content_metadata] (dict): Content metadata for the assignment's content key. If not provided, it will be
            fetched and subsequently cached from the content metadata API.
    """
    assignment_configuration = assignment.assignment_configuration
    # pylint: disable=no-member,useless-suppression
    subsidy_access_policy = assignment_configuration.subsidy_access_policy

    # subsidy expiration
    subsidy_expiration_datetime = _get_subsidy_expiration(assignment)

    # content enrollment deadline
    if not content_metadata:
        content_key = assignment.content_key
        content_metadata_by_key = get_content_metadata_for_assignments(
            enterprise_catalog_uuid=subsidy_access_policy.catalog_uuid,
            assignments=[assignment],
        )
        content_metadata = content_metadata_by_key.get(content_key)
    enrollment_deadline_datetime = _get_enrollment_deadline_date(assignment, content_metadata)
    if enrollment_deadline_datetime:
        enrollment_deadline_datetime = enrollment_deadline_datetime.replace(tzinfo=UTC)

    # 90-day timeout from allocation
    timeout_expiration_datetime = assignment.get_allocation_timeout_expiration()

    # Determine which of the three expiration dates is the earliest
    subsidy_expiration = {
        'date': subsidy_expiration_datetime,
        'reason': AssignmentAutomaticExpiredReason.SUBSIDY_EXPIRED,
    }
    enrollment_deadline = {
        'date': enrollment_deadline_datetime,
        'reason': AssignmentAutomaticExpiredReason.ENROLLMENT_DATE_PASSED,
    }
    timeout_expiration = {
        'date': timeout_expiration_datetime,
        'reason': AssignmentAutomaticExpiredReason.NINETY_DAYS_PASSED,
    }
    expiration_dates = [subsidy_expiration, enrollment_deadline, timeout_expiration]
    sorted_available_expiration_dates = sorted(
        filter(lambda x: x['date'] is not None, expiration_dates),
        key=lambda x: x['date'],
    )
    action_required_by = sorted_available_expiration_dates[0]
    message = (
        'action_required_by assignment=%s: subsidy_expiration=%s, enrollment_deadline=%s, '
        'timeout_expiration_date=%s, action_required_by_datetime=%s, action_required_by_reason=%s',
    )
    logger.info(
        message,
        assignment.uuid,
        subsidy_expiration_datetime,
        enrollment_deadline_datetime,
        timeout_expiration_datetime,
        action_required_by['date'],
        action_required_by['reason'],
    )
    return action_required_by


def should_send_email_to_pecu(recent_action):
    """
    Helper to check if the groups invite was sent to pending enterprise customer user
    5, 25, 50, 65, or 85 days ago.
    """
    current_date = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
    is_5_days_since_invited = current_date - timedelta(
        days=BRAZE_GROUPS_EMAIL_CAMPAIGNS_FIRST_REMINDER_DAY
    ) == (datetime.strptime(recent_action, "%B %d, %Y"))
    is_25_days_since_invited = current_date - timedelta(
        days=BRAZE_GROUPS_EMAIL_CAMPAIGNS_SECOND_REMINDER_DAY
    ) == (datetime.strptime(recent_action, "%B %d, %Y"))
    is_50_days_since_invited = current_date - timedelta(
        days=BRAZE_GROUPS_EMAIL_CAMPAIGNS_THIRD_REMINDER_DAY
    ) == (datetime.strptime(recent_action, "%B %d, %Y"))
    is_65_days_since_invited = current_date - timedelta(
        days=BRAZE_GROUPS_EMAIL_CAMPAIGNS_FOURTH_REMINDER_DAY
    ) == (datetime.strptime(recent_action, "%B %d, %Y"))
    is_85_days_since_invited = current_date - timedelta(
        days=BRAZE_GROUPS_EMAIL_CAMPAIGNS_FINAL_REMINDER_DAY
    ) == (datetime.strptime(recent_action, "%B %d, %Y"))

    return (
        is_5_days_since_invited or
        is_25_days_since_invited or
        is_50_days_since_invited or
        is_65_days_since_invited or
        is_85_days_since_invited
    )


def get_normalized_metadata_for_assignment(assignment, content_metadata):
    """
    Retrieve normalized metadata for a given object. If the object is associated
    with a specific course run or a preferred course run key, return the metadata for that run.
    If metadata for the run is missing, return the normalized metadata for the advertised run.

    Args:
        assignment (dict): The assignment object.
        content_metadata (dict): The content metadata object

    Returns:
        dict: Normalized metadata, either for a specific course run or the advertised course run, if any.
    """
    normalized_metadata_by_run = content_metadata.get('normalized_metadata_by_run', {})
    # Return the content metadata for a specific course run based on the preferred_course_run_key
    if preferred_course_run_key := assignment.preferred_course_run_key:
        return normalized_metadata_by_run.get(preferred_course_run_key, {})
    # Return current advertised course run metadata if preferred_course_run_key is NULL (i.e.,
    # impacting legacy course-based assignments created pre-May 2024).
    return content_metadata.get('normalized_metadata', {})


def _curr_date(date_format=None):
    curr_date = localized_utcnow()
    if not date_format:
        return curr_date
    return curr_date.strftime(date_format)


def _days_from_now(days_from_now=0, date_format=None):
    date = datetime.now().replace(tzinfo=UTC) + timedelta(days=days_from_now)
    if not date_format:
        return date
    return date.strftime(date_format)


def get_advertised_course_run_metadata(content_metadata):
    course_runs = content_metadata.get('course_runs', [])
    advertised_course_run_uuid = content_metadata.get('advertised_course_run_uuid')
    return next((run for run in course_runs if run.get('uuid') == advertised_course_run_uuid), None)


def get_course_run_metadata_for_assignment(assignment, content_metadata):
    """
    Retrieves metadata for a specific course run associated with an assignment. If the assignment has
    a preferred course run, returns the metadata for that run. If the preferred run metadata is not
    found, returns normalized_metadata.

    Args:
        assignment (dict): The assignment object.
        content_metadata (dict): The content metadata object.

    Returns:
        dict: Course run metadata if available, otherwise advertised_course_run.
    """
    course_runs = content_metadata.get('course_runs', [])

    # For run-based assignments, return metadata for the preferred course run if
    # available. If not, fallback to advertised run.
    if preferred_course_run_key := assignment.preferred_course_run_key:
        course_run = next((run for run in course_runs if run.get('key') == preferred_course_run_key), None)
        if not course_run:
            logger.warning(
                'Metadata not found for preferred course run key %s in content metadata %s. Assignment UUID: %s',
                preferred_course_run_key,
                content_metadata.get('key'),
                assignment.uuid
            )
            # Fallback to advertised course run if preferred course run metadata is missing
            return get_advertised_course_run_metadata(content_metadata)
        return course_run

    # For course-based assignments, return metadata for the advertised course run
    return get_advertised_course_run_metadata(content_metadata)


def cents_to_dollars(value_in_cents):
    """
    Converts some value of cents (could be an int or a string)
    into dollars.

    Returns:
      A Decimal representation of cents converted to dollars.
    """
    return Decimal(value_in_cents) / Decimal(100)
