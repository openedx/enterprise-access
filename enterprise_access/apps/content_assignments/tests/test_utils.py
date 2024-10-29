"""
Tests for Enterprise Access content_assignments utils.
"""

import ddt
from django.test import TestCase

from enterprise_access.apps.content_assignments.constants import BRAZE_ACTION_REQUIRED_BY_TIMESTAMP_FORMAT
from enterprise_access.apps.content_assignments.utils import (
    get_self_paced_normalized_start_date,
    has_time_to_complete,
    is_within_minimum_start_date_threshold
)
from enterprise_access.utils import _curr_date, _days_from_now


@ddt.ddt
class UtilsTests(TestCase):
    """
    Tests related to utility functions for content assignments
    """

    @ddt.data(
        # Start after is before the curr_date - START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS
        {
            "start_date": _days_from_now(5, '%Y-%m-%dT%H:%M:%SZ'),
            "curr_date": _curr_date(),
            "expected_output": False
        },
        # Start after is before the curr_date - START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS

        {
            "start_date": _days_from_now(-5, '%Y-%m-%dT%H:%M:%SZ'),
            "curr_date": _curr_date(),
            "expected_output": False
        },
        # Start after is before the curr_date - START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS
        {
            "start_date": _days_from_now(15, '%Y-%m-%dT%H:%M:%SZ'),
            "curr_date": _curr_date(),
            "expected_output": False
        },
        # Start date is before the curr_date - START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS
        {
            "start_date": _days_from_now(-15, '%Y-%m-%dT%H:%M:%SZ'),
            "curr_date": _curr_date(),
            "expected_output": True
        },
        # Start after is before the curr_date - START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS
        {
            "start_date": _curr_date('%Y-%m-%dT%H:%M:%SZ'),
            "curr_date": _curr_date(),
            "expected_output": False
        }
    )
    @ddt.unpack
    def test_is_within_minimum_start_date_threshold(self, start_date, curr_date, expected_output):
        assert is_within_minimum_start_date_threshold(curr_date, start_date) == expected_output

    @ddt.data(
        # endDate is the exact day as weeks to complete offset
        {
            "end_date": _days_from_now(49, '%Y-%m-%dT%H:%M:%SZ'),
            "curr_date": _curr_date(),
            "weeks_to_complete": 7,
            "expected_output": True
        },
        # weeks to complete is within endDate
        {
            "end_date": _days_from_now(49, '%Y-%m-%dT%H:%M:%SZ'),
            "curr_date": _curr_date(),
            "weeks_to_complete": 4,
            "expected_output": True
        },
        # weeks to complete is beyond end date
        {
            "end_date": _days_from_now(49, '%Y-%m-%dT%H:%M:%SZ'),
            "curr_date": _curr_date(),
            "weeks_to_complete": 8,
            "expected_output": False
        },
        # end date is current date
        {
            "end_date": _curr_date('%Y-%m-%dT%H:%M:%SZ'),
            "curr_date": _curr_date(),
            "weeks_to_complete": 1,
            "expected_output": False
        },
    )
    @ddt.unpack
    def test_has_time_to_complete(self, end_date, curr_date, weeks_to_complete, expected_output):
        assert has_time_to_complete(curr_date, end_date, weeks_to_complete) == expected_output

    @ddt.data(
        {
            "start_date": None,
            "end_date": _days_from_now(10, '%Y-%m-%dT%H:%M:%SZ'),
            "course_metadata": {
                "pacing_type": "self_paced",
                "weeks_to_complete": 8,
            },
        },
        {
            "start_date": _days_from_now(5, '%Y-%m-%dT%H:%M:%SZ'),
            "end_date": None,
            "course_metadata": {
                "pacing_type": "self_paced",
                "weeks_to_complete": 8,
            },
        },
        {
            "start_date": _days_from_now(5, '%Y-%m-%dT%H:%M:%SZ'),
            "end_date": _days_from_now(10, '%Y-%m-%dT%H:%M:%SZ'),
            "course_metadata": {
                "pacing_type": None,
                "weeks_to_complete": 8,
            },
        },
        {
            "start_date": _days_from_now(5, '%Y-%m-%dT%H:%M:%SZ'),
            "end_date": _days_from_now(10, '%Y-%m-%dT%H:%M:%SZ'),
            "course_metadata": {
                "pacing_type": "self_paced",
                "weeks_to_complete": None,
            },
        },
        {
            "start_date": None,
            "end_date": None,
            "course_metadata": {
                "pacing_type": None,
                "weeks_to_complete": None,
            },
        },
    )
    @ddt.unpack
    def test_get_self_paced_normalized_start_date_empty_data(self, start_date, end_date, course_metadata):
        assert get_self_paced_normalized_start_date(start_date, end_date, course_metadata) == \
               _curr_date(BRAZE_ACTION_REQUIRED_BY_TIMESTAMP_FORMAT)

    @ddt.data(
        # self-paced, has time to complete
        {
            "start_date": _days_from_now(5, '%Y-%m-%dT%H:%M:%SZ'),
            "end_date": _days_from_now(28, '%Y-%m-%dT%H:%M:%SZ'),
            "course_metadata": {
                "pacing_type": "self_paced",
                "weeks_to_complete": 3,
            },
        },
        # self-paced, does not have time to complete, but start date older than
        # START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS
        {
            "start_date": _days_from_now(-15, '%Y-%m-%dT%H:%M:%SZ'),
            "end_date": _days_from_now(10, '%Y-%m-%dT%H:%M:%SZ'),
            "course_metadata": {
                "pacing_type": "self_paced",
                "weeks_to_complete": 300,
            },
        },
        # self-paced, does not have time to complete, start date within
        # START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS
        {
            "start_date": _days_from_now(-5, '%Y-%m-%dT%H:%M:%SZ'),
            "end_date": _days_from_now(10, '%Y-%m-%dT%H:%M:%SZ'),
            "course_metadata": {
                "pacing_type": "self_paced",
                "weeks_to_complete": 300,
            },
        },
        # instructor paced
        {
            "start_date": _days_from_now(5, '%Y-%m-%dT%H:%M:%SZ'),
            "end_date": _days_from_now(10, '%Y-%m-%dT%H:%M:%SZ'),
            "course_metadata": {
                "pacing_type": "instructor_paced",
                "weeks_to_complete": 8,
            },
        },
    )
    @ddt.unpack
    def test_get_self_paced_normalized_start_date_self_paced(self, start_date, end_date, course_metadata):
        pacing_type = course_metadata.get('pacing_type')
        weeks_to_complete = course_metadata.get('weeks_to_complete')

        can_complete_in_time = has_time_to_complete(_curr_date(), end_date, weeks_to_complete)
        within_start_date_threshold = is_within_minimum_start_date_threshold(_curr_date(), start_date)

        if pacing_type == 'self_paced' and (can_complete_in_time or within_start_date_threshold):
            assert get_self_paced_normalized_start_date(start_date, end_date, course_metadata) == \
                   _curr_date(BRAZE_ACTION_REQUIRED_BY_TIMESTAMP_FORMAT)
        else:
            assert get_self_paced_normalized_start_date(start_date, end_date, course_metadata) == \
                   start_date
