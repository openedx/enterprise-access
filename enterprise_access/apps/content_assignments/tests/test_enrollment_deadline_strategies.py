"""
Tests for enrollment deadline strategies.
"""
from unittest import mock

import ddt
from django.test import TestCase
from pytz import UTC

from enterprise_access.apps.content_assignments.enrollment_deadline_strategies import (
    CreditRequestEnrollmentDeadlineStrategy,
    DefaultEnrollmentDeadlineStrategy,
    EnrollmentDeadlineStrategyFactory
)
from enterprise_access.apps.content_assignments.tests.factories import (
    AssignmentConfigurationFactory,
    LearnerContentAssignmentFactory
)
from enterprise_access.apps.subsidy_request.tests.factories import LearnerCreditRequestFactory
from enterprise_access.utils import _days_from_now

DATE_FORMAT_ISO_8601 = '%Y-%m-%dT%H:%M:%SZ'


@ddt.ddt
class TestEnrollmentDeadlineStrategyFactory(TestCase):
    """
    Tests for EnrollmentDeadlineStrategyFactory.
    """

    def test_returns_credit_request_strategy_for_assignment_with_credit_request(self):
        """
        Factory should return CreditRequestEnrollmentDeadlineStrategy when
        assignment has an associated credit_request.
        """
        assignment_config = AssignmentConfigurationFactory()
        assignment = LearnerContentAssignmentFactory(assignment_configuration=assignment_config)
        LearnerCreditRequestFactory(assignment=assignment)

        strategy = EnrollmentDeadlineStrategyFactory.get_strategy(assignment)

        self.assertIsInstance(strategy, CreditRequestEnrollmentDeadlineStrategy)

    def test_returns_default_strategy_for_assignment_without_credit_request(self):
        """
        Factory should return DefaultEnrollmentDeadlineStrategy when
        assignment has no associated credit_request.
        """
        assignment_config = AssignmentConfigurationFactory()
        assignment = LearnerContentAssignmentFactory(assignment_configuration=assignment_config)

        strategy = EnrollmentDeadlineStrategyFactory.get_strategy(assignment)

        self.assertIsInstance(strategy, DefaultEnrollmentDeadlineStrategy)


@ddt.ddt
class TestDefaultEnrollmentDeadlineStrategy(TestCase):
    """
    Tests for DefaultEnrollmentDeadlineStrategy.
    """

    def setUp(self):
        super().setUp()
        self.strategy = DefaultEnrollmentDeadlineStrategy()
        self.assignment_config = AssignmentConfigurationFactory()

    def test_returns_none_when_content_metadata_is_none(self):
        """
        Should return None when content_metadata is None.
        """
        assignment = LearnerContentAssignmentFactory(assignment_configuration=self.assignment_config)

        result = self.strategy.get_enrollment_deadline(assignment, None)

        self.assertIsNone(result)

    def test_returns_none_when_content_metadata_is_empty(self):
        """
        Should return None when content_metadata is empty.
        """
        assignment = LearnerContentAssignmentFactory(assignment_configuration=self.assignment_config)

        result = self.strategy.get_enrollment_deadline(assignment, {})

        self.assertIsNone(result)

    def test_returns_enroll_by_date_from_normalized_metadata(self):
        """
        Should return the enroll_by_date from normalized_metadata.
        """
        enroll_by_date = _days_from_now(30, DATE_FORMAT_ISO_8601)
        assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_config,
            preferred_course_run_key=None
        )
        content_metadata = {
            'normalized_metadata': {
                'enroll_by_date': enroll_by_date,
            }
        }

        result = self.strategy.get_enrollment_deadline(assignment, content_metadata)

        self.assertIsNotNone(result)
        self.assertEqual(result.tzinfo, UTC)

    def test_returns_enroll_by_date_from_preferred_course_run(self):
        """
        Should return the enroll_by_date from the preferred course run.
        """
        preferred_run_key = 'course-v1:edX+DemoX+Run1'
        preferred_run_enroll_by = _days_from_now(60, DATE_FORMAT_ISO_8601)
        advertised_enroll_by = _days_from_now(30, DATE_FORMAT_ISO_8601)

        assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_config,
            preferred_course_run_key=preferred_run_key
        )
        content_metadata = {
            'normalized_metadata': {
                'enroll_by_date': advertised_enroll_by,
            },
            'normalized_metadata_by_run': {
                preferred_run_key: {
                    'enroll_by_date': preferred_run_enroll_by,
                }
            }
        }

        result = self.strategy.get_enrollment_deadline(assignment, content_metadata)

        self.assertIsNotNone(result)
        self.assertEqual(result.tzinfo, UTC)


@ddt.ddt
class TestCreditRequestEnrollmentDeadlineStrategy(TestCase):
    """
    Tests for CreditRequestEnrollmentDeadlineStrategy.
    """

    def setUp(self):
        super().setUp()
        self.strategy = CreditRequestEnrollmentDeadlineStrategy()
        self.assignment_config = AssignmentConfigurationFactory()

    def test_returns_none_when_content_metadata_is_none(self):
        """
        Should return None when content_metadata is None.
        """
        assignment = LearnerContentAssignmentFactory(assignment_configuration=self.assignment_config)

        result = self.strategy.get_enrollment_deadline(assignment, None)

        self.assertIsNone(result)

    def test_returns_last_run_deadline_when_in_future(self):
        """
        Should return the last course run's enrollment deadline when it's in the future.
        """
        # Advertised run deadline is in the past
        advertised_enroll_by = _days_from_now(-5, DATE_FORMAT_ISO_8601)
        # Future run deadline is in the future
        future_run_enroll_by = _days_from_now(60, DATE_FORMAT_ISO_8601)

        assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_config,
            preferred_course_run_key='course-v1:edX+DemoX+CurrentRun'
        )
        content_metadata = {
            'normalized_metadata': {
                'enroll_by_date': advertised_enroll_by,
            },
            'normalized_metadata_by_run': {
                'course-v1:edX+DemoX+CurrentRun': {
                    'enroll_by_date': advertised_enroll_by,
                },
                'course-v1:edX+DemoX+FutureRun': {
                    'enroll_by_date': future_run_enroll_by,
                }
            }
        }

        result = self.strategy.get_enrollment_deadline(assignment, content_metadata)

        self.assertIsNotNone(result)
        # Should be the future run's deadline (60 days from now), not the advertised one
        self.assertGreater(result, _days_from_now(30))

    def test_falls_back_to_default_when_last_run_deadline_passed(self):
        """
        Should fall back to default strategy when all run deadlines have passed.
        """
        # All deadlines are in the past
        past_deadline_1 = _days_from_now(-30, DATE_FORMAT_ISO_8601)
        past_deadline_2 = _days_from_now(-10, DATE_FORMAT_ISO_8601)

        assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_config,
            preferred_course_run_key=None
        )
        content_metadata = {
            'normalized_metadata': {
                'enroll_by_date': past_deadline_2,
            },
            'normalized_metadata_by_run': {
                'course-v1:edX+DemoX+Run1': {
                    'enroll_by_date': past_deadline_1,
                },
                'course-v1:edX+DemoX+Run2': {
                    'enroll_by_date': past_deadline_2,
                }
            }
        }

        result = self.strategy.get_enrollment_deadline(assignment, content_metadata)

        # Should fall back to normalized_metadata deadline
        self.assertIsNotNone(result)

    def test_returns_max_deadline_from_multiple_future_runs(self):
        """
        Should return the maximum (last) deadline when multiple future runs exist.
        """
        run1_deadline = _days_from_now(30, DATE_FORMAT_ISO_8601)
        run2_deadline = _days_from_now(60, DATE_FORMAT_ISO_8601)
        run3_deadline = _days_from_now(90, DATE_FORMAT_ISO_8601)

        assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_config,
            preferred_course_run_key=None
        )
        content_metadata = {
            'normalized_metadata': {
                'enroll_by_date': run1_deadline,
            },
            'normalized_metadata_by_run': {
                'course-v1:edX+DemoX+Run1': {
                    'enroll_by_date': run1_deadline,
                },
                'course-v1:edX+DemoX+Run2': {
                    'enroll_by_date': run2_deadline,
                },
                'course-v1:edX+DemoX+Run3': {
                    'enroll_by_date': run3_deadline,
                }
            }
        }

        result = self.strategy.get_enrollment_deadline(assignment, content_metadata)

        self.assertIsNotNone(result)
        # Should be approximately 90 days from now (the max)
        self.assertGreater(result, _days_from_now(80))

    def test_falls_back_when_normalized_metadata_by_run_is_empty(self):
        """
        Should fall back to default strategy when normalized_metadata_by_run is empty.
        """
        advertised_enroll_by = _days_from_now(30, DATE_FORMAT_ISO_8601)

        assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_config,
            preferred_course_run_key=None
        )
        content_metadata = {
            'normalized_metadata': {
                'enroll_by_date': advertised_enroll_by,
            },
            'normalized_metadata_by_run': {}
        }

        result = self.strategy.get_enrollment_deadline(assignment, content_metadata)

        # Should fall back to normalized_metadata deadline
        self.assertIsNotNone(result)

    def test_handles_runs_without_enroll_by_date(self):
        """
        Should handle runs that don't have enroll_by_date gracefully.
        """
        future_deadline = _days_from_now(60, DATE_FORMAT_ISO_8601)

        assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_config,
            preferred_course_run_key=None
        )
        content_metadata = {
            'normalized_metadata': {
                'enroll_by_date': _days_from_now(-5, DATE_FORMAT_ISO_8601),
            },
            'normalized_metadata_by_run': {
                'course-v1:edX+DemoX+Run1': {
                    # No enroll_by_date
                    'start_date': '2025-01-01T00:00:00Z',
                },
                'course-v1:edX+DemoX+Run2': {
                    'enroll_by_date': future_deadline,
                }
            }
        }

        result = self.strategy.get_enrollment_deadline(assignment, content_metadata)

        self.assertIsNotNone(result)
        # Should use the run that has enroll_by_date
        self.assertGreater(result, _days_from_now(30))
