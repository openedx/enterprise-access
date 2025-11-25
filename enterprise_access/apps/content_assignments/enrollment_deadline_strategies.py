"""
Enrollment deadline calculation strategies for different assignment types.
"""
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from enterprise_access.apps.content_assignments.models import LearnerContentAssignment

from pytz import UTC

from enterprise_access.apps.content_assignments.content_metadata_api import parse_datetime_string

logger = logging.getLogger(__name__)


class EnrollmentDeadlineStrategy(ABC):
    """
    Abstract base class for enrollment deadline calculation strategies.
    """

    @abstractmethod
    def get_enrollment_deadline(
        self,
        assignment: "LearnerContentAssignment",
        content_metadata: dict
    ) -> Optional[datetime]:
        """
        Calculate the enrollment deadline for the given assignment.

        Args:
            assignment: The LearnerContentAssignment instance.
            content_metadata: The content metadata dictionary.

        Returns:
            The enrollment deadline datetime, or None if not determinable.
        """


class DefaultEnrollmentDeadlineStrategy(EnrollmentDeadlineStrategy):
    """
    Default strategy that uses the normalized metadata enrollment deadline.

    This is the existing behavior for:
    - Admin-allocated run-based assignments (uses preferred_course_run_key)
    - Admin-allocated course-based assignments (uses advertised run)
    """

    def get_enrollment_deadline(
        self,
        assignment: "LearnerContentAssignment",
        content_metadata: dict
    ) -> Optional[datetime]:
        # Import here to avoid circular import with enterprise_access.utils
        from enterprise_access.utils import get_normalized_metadata_for_assignment

        if not content_metadata:
            return None

        normalized_metadata = get_normalized_metadata_for_assignment(
            assignment, content_metadata
        )
        enrollment_end_date_str = normalized_metadata.get('enroll_by_date')

        if not enrollment_end_date_str:
            return None

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
            pass

        return None


class CreditRequestEnrollmentDeadlineStrategy(EnrollmentDeadlineStrategy):
    """
    Strategy for assignments created via Browse & Request credit requests.

    This strategy considers the last course run when determining the enrollment
    deadline, as credit requests are course-level (not run-level) requests.
    Learners can redeem the course as long as there's any future course run available.

    The logic:
    1. Get all course runs from normalized_metadata_by_run
    2. Find the last course run (the one with the latest enrollment deadline)
    3. If the last run's enrollment deadline is in the future, return it
    4. Otherwise, fall back to the current normalized_metadata deadline
    """

    def get_enrollment_deadline(
        self,
        assignment,
        content_metadata: dict
    ) -> Optional[datetime]:
        # Import here to avoid circular import with enterprise_access.utils
        from enterprise_access.utils import localized_utcnow

        if not content_metadata:
            return None

        # Get the last course run's enrollment deadline
        last_run_deadline = self._get_last_course_run_enrollment_deadline(content_metadata)

        # If the last run's deadline is in the future, use it
        if last_run_deadline and last_run_deadline > localized_utcnow():
            return last_run_deadline

        # Fall back to the default behavior (current advertised run)
        return DefaultEnrollmentDeadlineStrategy().get_enrollment_deadline(
            assignment, content_metadata
        )

    def _get_last_course_run_enrollment_deadline(
        self,
        content_metadata: dict
    ) -> Optional[datetime]:
        """
        Find the enrollment deadline of the last course run.

        Uses `normalized_metadata_by_run` dict to extract all `enroll_by_date` values
        and returns the maximum (latest) deadline.

        Args:
            content_metadata: The content metadata dictionary.

        Returns:
            The last course run's enrollment deadline, or None if not determinable.
        """
        normalized_metadata_by_run = content_metadata.get('normalized_metadata_by_run', {})

        if not normalized_metadata_by_run:
            return None

        deadlines = [
            parse_datetime_string(run_metadata.get('enroll_by_date')).replace(tzinfo=UTC)
            for run_metadata in normalized_metadata_by_run.values()
            if run_metadata.get('enroll_by_date')
        ]

        return max(deadlines) if deadlines else None


class EnrollmentDeadlineStrategyFactory:
    """
    Factory for selecting the appropriate enrollment deadline strategy.
    """

    @staticmethod
    def get_strategy(assignment: "LearnerContentAssignment") -> EnrollmentDeadlineStrategy:
        """
        Select the appropriate strategy based on assignment characteristics.

        Args:
            assignment: The LearnerContentAssignment instance.

        Returns:
            The appropriate EnrollmentDeadlineStrategy instance.
        """
        # Check if this assignment was created via a credit request
        credit_request = getattr(assignment, 'credit_request', None)

        if credit_request is not None:
            return CreditRequestEnrollmentDeadlineStrategy()

        return DefaultEnrollmentDeadlineStrategy()
