"""
Utils for content_assignments
"""
import logging
from datetime import datetime, timedelta

from dateutil import parser
from pytz import UTC

from enterprise_access.apps.content_assignments.constants import (
    BRAZE_ACTION_REQUIRED_BY_TIMESTAMP_FORMAT,
    START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS
)


logger = logging.getLogger(__name__)

def is_within_minimum_start_date_threshold(curr_date, start_date):
    """
    Checks if today's date were set to a certain number of days in the past,
    offset_date_from_today, is the start_date before offset_date_from_today.
    """
    start_date_datetime = parser.parse(start_date)
    offset_date_from_today = curr_date - timedelta(days=START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS)
    return start_date_datetime < offset_date_from_today.replace(tzinfo=UTC)


def has_time_to_complete(curr_date, end_date, weeks_to_complete):
    """
    Checks if today's date were set to a certain number of weeks_to_complete in the future,
    offset_now_by_weeks_to_complete, is offset_now_by_weeks_to_complete date before the end_date
    """
    end_date_datetime = parser.parse(end_date)
    offset_now_by_weeks_to_complete = curr_date + timedelta(weeks=weeks_to_complete)
    return offset_now_by_weeks_to_complete.replace(tzinfo=UTC).strftime("%Y-%m-%d") <= \
        end_date_datetime.strftime("%Y-%m-%d")


def get_self_paced_normalized_start_date(start_date, end_date, course_metadata):
    """
    Normalizes courses start_date far in the past based on a heuristic for the purpose of displaying a
    reasonable start_date in content assignment related emails.

    Heuristic:
    For self-paced courses with a weeks_to_complete field too close to the end date to complete the course
    or a start_date that is before today offset by the START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS should
    default to today's date.
    Otherwise, return the current start_date
    """
    curr_date = datetime.now()
    pacing_type = course_metadata.get('pacing_type', {}) or None
    weeks_to_complete = course_metadata.get('weeks_to_complete', {}) or None
    logger.info(f"Start Date: {start_date}, End Date: {end_date}, Course Metadata: {course_metadata}")
    if not (start_date and end_date and pacing_type and weeks_to_complete):
        logger.info(f"[Case-1] Missing required data: returning: curr_date:{curr_date}")
        return curr_date.strftime(BRAZE_ACTION_REQUIRED_BY_TIMESTAMP_FORMAT)
    if pacing_type == "self_paced":
        if has_time_to_complete(curr_date, end_date, weeks_to_complete) or \
                is_within_minimum_start_date_threshold(curr_date, start_date):
            logger.info(
                "[Case-2] Self-paced course with sufficient time to complete. "
                f"curr_date: {curr_date}, end_date: {end_date}, weeks_to_complete: {weeks_to_complete}, "
                f"start_date: {start_date}"
            )
            return curr_date.strftime(BRAZE_ACTION_REQUIRED_BY_TIMESTAMP_FORMAT)
    logger.info(f"[Case-3] Returning original start_date {start_date}.")
    return start_date
