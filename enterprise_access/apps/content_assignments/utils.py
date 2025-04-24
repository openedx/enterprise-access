"""
Utils for content_assignments
"""
from datetime import datetime, timedelta

from dateutil import parser
from pytz import UTC

from enterprise_access.apps.content_assignments.constants import (
    BRAZE_TIMESTAMP_FORMAT,
    START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS
)
from enterprise_access.utils import localized_utcnow


def is_within_minimum_start_date_threshold(
    curr_date: datetime,
    start_date: datetime,
) -> bool:
    """
    Checks if today's date were set to a certain number of days in the past,
    offset_date_from_today, is the start_date before offset_date_from_today.
    """
    offset_date_from_today = curr_date - timedelta(days=START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS)
    return start_date < offset_date_from_today


def has_time_to_complete(
    curr_date: datetime,
    end_date: datetime,
    weeks_to_complete: int,
) -> bool:
    """
    Checks if today's date were set to a certain number of weeks_to_complete in the future,
    offset_now_by_weeks_to_complete, is offset_now_by_weeks_to_complete date before the end_date
    """
    offset_now_by_weeks_to_complete = curr_date + timedelta(weeks=weeks_to_complete)
    return offset_now_by_weeks_to_complete.date() <= end_date.date()


def get_self_paced_normalized_start_date(
    start_date: str,
    end_date: str,
    course_metadata: dict,
) -> str:
    """
    Normalizes courses start_date far in the past based on a heuristic for the purpose of displaying a
    reasonable start_date in content assignment related emails.

    Heuristic:
    For already started self-paced courses for which EITHER there is still enough time to
    complete it before it ends, OR the course started a long time ago, we should display
    today's date as the "start date".  Otherwise, return the actual start_date.
    """
    curr_date = localized_utcnow()
    pacing_type = course_metadata.get('pacing_type', {}) or None
    weeks_to_complete = course_metadata.get('weeks_to_complete', {}) or None
    if not (start_date and end_date and pacing_type and weeks_to_complete):
        return curr_date.strftime(BRAZE_TIMESTAMP_FORMAT)
    start_date_datetime = parser.parse(start_date).astimezone(UTC)
    end_date_datetime = parser.parse(end_date).astimezone(UTC)
    if pacing_type == "self_paced" and start_date_datetime < curr_date:
        if has_time_to_complete(curr_date, end_date_datetime, weeks_to_complete) or \
                is_within_minimum_start_date_threshold(curr_date, start_date_datetime):
            return curr_date.strftime(BRAZE_TIMESTAMP_FORMAT)
    return start_date
