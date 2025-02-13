"""
Tests for Enterprise Access content_assignments utils.
"""

import uuid

import ddt
from django.test import TestCase

from enterprise_access.apps.api_client.tests.test_constants import DATE_FORMAT_ISO_8601
from enterprise_access.apps.content_assignments.constants import BRAZE_TIMESTAMP_FORMAT
from enterprise_access.apps.content_assignments.tests.factories import LearnerContentAssignmentFactory
from enterprise_access.apps.content_assignments.utils import (
    get_self_paced_normalized_start_date,
    has_time_to_complete,
    is_within_minimum_start_date_threshold
)
from enterprise_access.utils import (
    _curr_date,
    _days_from_now,
    get_course_run_metadata_for_assignment,
    get_normalized_metadata_for_assignment
)

mock_course_run_1 = {
    'start_date': _days_from_now(-370, DATE_FORMAT_ISO_8601),
    'end_date': _days_from_now(350, DATE_FORMAT_ISO_8601),
    'enroll_by_date': _days_from_now(-363, DATE_FORMAT_ISO_8601),
    'enroll_start_date': _days_from_now(-380, DATE_FORMAT_ISO_8601),
    'content_price': 100,
}

mock_course_run_2 = {
    'start_date': _days_from_now(-70, DATE_FORMAT_ISO_8601),
    'end_date': _days_from_now(50, DATE_FORMAT_ISO_8601),
    'enroll_by_date': _days_from_now(-63, DATE_FORMAT_ISO_8601),
    'enroll_start_date': _days_from_now(-80, DATE_FORMAT_ISO_8601),
    'content_price': 100,
}

mock_advertised_course_run = {
    'start_date': _days_from_now(-10, DATE_FORMAT_ISO_8601),
    'end_date': _days_from_now(10, DATE_FORMAT_ISO_8601),
    'enroll_by_date': _days_from_now(-3, DATE_FORMAT_ISO_8601),
    'enroll_start_date': _days_from_now(-20, DATE_FORMAT_ISO_8601),
    'content_price': 100,
}

assignment_uuid = uuid.uuid4()
advertised_course_run_uuid = uuid.uuid4()


@ddt.ddt
class UtilsTests(TestCase):
    """
    Tests related to utility functions for content assignments
    """
    def setUp(self):
        super().setUp()
        self.mock_course_key = "edX+DemoX"
        self.mock_course_run_key_1 = "course-v1:edX+DemoX+1T360"
        self.mock_course_run_key_2 = "course-v1:edX+DemoX+1T60"
        self.mock_advertised_course_run_key = 'course-v1:edX+DemoX+1T0'
        self.mock_course_run_1 = mock_course_run_1
        self.mock_course_run_2 = mock_course_run_2
        self.mock_advertised_course_run = mock_advertised_course_run
        self.mock_content_metadata = {
            'normalized_metadata': self.mock_advertised_course_run,
            'normalized_metadata_by_run': {
                "course-v1:edX+DemoX+1T360": self.mock_course_run_1,
                "course-v1:edX+DemoX+1T60": self.mock_course_run_2,
                'course-v1:edX+DemoX+1T0': self.mock_advertised_course_run
            }
        }

    @ddt.data(
        # Start after is before the curr_date - START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS
        {
            "start_date": _days_from_now(5, DATE_FORMAT_ISO_8601),
            "curr_date": _curr_date(),
            "expected_output": False
        },
        # Start after is before the curr_date - START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS

        {
            "start_date": _days_from_now(-5, DATE_FORMAT_ISO_8601),
            "curr_date": _curr_date(),
            "expected_output": False
        },
        # Start after is before the curr_date - START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS
        {
            "start_date": _days_from_now(15, DATE_FORMAT_ISO_8601),
            "curr_date": _curr_date(),
            "expected_output": False
        },
        # Start date is before the curr_date - START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS
        {
            "start_date": _days_from_now(-15, DATE_FORMAT_ISO_8601),
            "curr_date": _curr_date(),
            "expected_output": True
        },
        # Start after is before the curr_date - START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS
        {
            "start_date": _curr_date(DATE_FORMAT_ISO_8601),
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
            "end_date": _days_from_now(49, DATE_FORMAT_ISO_8601),
            "curr_date": _curr_date(),
            "weeks_to_complete": 7,
            "expected_output": True
        },
        # weeks to complete is within endDate
        {
            "end_date": _days_from_now(49, DATE_FORMAT_ISO_8601),
            "curr_date": _curr_date(),
            "weeks_to_complete": 4,
            "expected_output": True
        },
        # weeks to complete is beyond end date
        {
            "end_date": _days_from_now(49, DATE_FORMAT_ISO_8601),
            "curr_date": _curr_date(),
            "weeks_to_complete": 8,
            "expected_output": False
        },
        # end date is current date
        {
            "end_date": _curr_date(DATE_FORMAT_ISO_8601),
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
            "end_date": _days_from_now(10, DATE_FORMAT_ISO_8601),
            "course_metadata": {
                "pacing_type": "self_paced",
                "weeks_to_complete": 8,
            },
        },
        {
            "start_date": _days_from_now(5, DATE_FORMAT_ISO_8601),
            "end_date": None,
            "course_metadata": {
                "pacing_type": "self_paced",
                "weeks_to_complete": 8,
            },
        },
        {
            "start_date": _days_from_now(5, DATE_FORMAT_ISO_8601),
            "end_date": _days_from_now(10, DATE_FORMAT_ISO_8601),
            "course_metadata": {
                "pacing_type": None,
                "weeks_to_complete": 8,
            },
        },
        {
            "start_date": _days_from_now(5, DATE_FORMAT_ISO_8601),
            "end_date": _days_from_now(10, DATE_FORMAT_ISO_8601),
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
               _curr_date(BRAZE_TIMESTAMP_FORMAT)

    @ddt.data(
        # self-paced, has time to complete
        {
            "start_date": _days_from_now(5, DATE_FORMAT_ISO_8601),
            "end_date": _days_from_now(28, DATE_FORMAT_ISO_8601),
            "course_metadata": {
                "pacing_type": "self_paced",
                "weeks_to_complete": 3,
            },
        },
        # self-paced, does not have time to complete, but start date older than
        # START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS
        {
            "start_date": _days_from_now(-15, DATE_FORMAT_ISO_8601),
            "end_date": _days_from_now(10, DATE_FORMAT_ISO_8601),
            "course_metadata": {
                "pacing_type": "self_paced",
                "weeks_to_complete": 300,
            },
        },
        # self-paced, does not have time to complete, start date within
        # START_DATE_DEFAULT_TO_TODAY_THRESHOLD_DAYS
        {
            "start_date": _days_from_now(-5, DATE_FORMAT_ISO_8601),
            "end_date": _days_from_now(10, DATE_FORMAT_ISO_8601),
            "course_metadata": {
                "pacing_type": "self_paced",
                "weeks_to_complete": 300,
            },
        },
        # instructor paced
        {
            "start_date": _days_from_now(5, DATE_FORMAT_ISO_8601),
            "end_date": _days_from_now(10, DATE_FORMAT_ISO_8601),
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
                   _curr_date(BRAZE_TIMESTAMP_FORMAT)
        else:
            assert get_self_paced_normalized_start_date(start_date, end_date, course_metadata) == \
                   start_date

    @ddt.data(
        # Expects course run associated with configured content_key for run-based assignment
        {
            'assignment': {
                'is_assigned_course_run': True,
                'preferred_course_run_key': 'course-v1:edX+DemoX+1T60',
                'content_key': 'course-v1:edX+DemoX+1T60',
            },
            'expected_output': mock_course_run_2,
        },
        # Expects the preferred content_key
        {
            'assignment': {
                'is_assigned_course_run': False,
                'preferred_course_run_key': 'course-v1:edX+DemoX+1T360',
                'content_key': 'edX+DemoX',
            },
            'expected_output': mock_course_run_1,
        },
        # Expects the top level course key
        {
            'assignment': {
                'is_assigned_course_run': False,
                'preferred_course_run_key': None,
                'content_key': 'edX+DemoX',
            },
            'expected_output': mock_advertised_course_run,
        },
    )
    @ddt.unpack
    def test_get_normalized_metadata_for_assignment(self, assignment, expected_output):
        assignment_obj = LearnerContentAssignmentFactory(**assignment)
        normalized_metadata = get_normalized_metadata_for_assignment(
            assignment=assignment_obj,
            content_metadata=self.mock_content_metadata
        )
        self.assertEqual(normalized_metadata, expected_output)

    @ddt.data(
        # Test case for preferred course run that exists
        {
            'assignment': {
                'preferred_course_run_key': 'course-v1:edX+DemoX+1T60',
                'uuid': assignment_uuid
            },
            'content_metadata': {
                'course_runs': [
                    {'key': 'course-v1:edX+DemoX+1T60', 'data': 'test1'},
                    {'key': 'course-v1:edX+DemoX+1T360', 'data': 'test2'}
                ],
                'advertised_course_run_uuid': advertised_course_run_uuid,
                'key': 'test-content-key'
            },
            'expected_output': {'key': 'course-v1:edX+DemoX+1T60', 'data': 'test1'}
        },
        # Test case for preferred course run that doesn't exist - should fall back to advertised run
        {
            'assignment': {
                'preferred_course_run_key': 'non-existent-key',
                'uuid': assignment_uuid
            },
            'content_metadata': {
                'course_runs': [
                    {'key': 'course-v1:edX+DemoX+1T60', 'uuid': advertised_course_run_uuid, 'data': 'test1'},
                    {'key': 'course-v1:edX+DemoX+1T360', 'data': 'test2'}
                ],
                'advertised_course_run_uuid': advertised_course_run_uuid,
                'key': 'test-content-key'
            },
            'expected_output': {'key': 'course-v1:edX+DemoX+1T60', 'uuid': advertised_course_run_uuid, 'data': 'test1'}
        },
        # Test case for no preferred course run - should return advertised run
        {
            'assignment': {
                'preferred_course_run_key': None,
                'uuid': assignment_uuid
            },
            'content_metadata': {
                'course_runs': [
                    {'key': 'course-v1:edX+DemoX+1T60', 'uuid': advertised_course_run_uuid, 'data': 'test1'},
                    {'key': 'course-v1:edX+DemoX+1T360', 'data': 'test2'}
                ],
                'advertised_course_run_uuid': advertised_course_run_uuid,
                'key': 'test-content-key'
            },
            'expected_output': {'key': 'course-v1:edX+DemoX+1T60', 'uuid': advertised_course_run_uuid, 'data': 'test1'}
        }
    )
    @ddt.unpack
    def test_get_course_run_metadata_for_assignment(self, assignment, content_metadata, expected_output):
        """
        Test get_course_run_metadata_for_assignment returns the correct course run metadata
        based on assignment and content metadata.
        """
        assignment_obj = LearnerContentAssignmentFactory(**assignment)
        course_run_metadata = get_course_run_metadata_for_assignment(
            assignment=assignment_obj,
            content_metadata=content_metadata
        )
        self.assertEqual(course_run_metadata, expected_output)
