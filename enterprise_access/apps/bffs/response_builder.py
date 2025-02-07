"""
Response Builder Module for bffs app
"""

import logging

from enterprise_access.apps.bffs.mixins import BaseLearnerDataMixin, LearnerDashboardDataMixin
from enterprise_access.apps.bffs.serializers import LearnerDashboardResponseSerializer

logger = logging.getLogger(__name__)


class BaseResponseBuilder:
    """
    A base response builder class that provides shared core functionality for different response builders.

    The `BaseResponseBuilder` includes methods for building response data and can be extended by specific
    response builders like `LearnerDashboardResponseBuilder` or `CourseResponseBuilder`.
    """

    @property
    def status_code(self):
        """
        Returns the HTTP status code for the response from HandlerContext.
        """
        return self.context.status_code

    def __init__(self, context):
        """
        Initializes the BaseResponseBuilder with a HandlerContext.

        Args:
            context (HandlerContext): The context object containing data, errors, and request information.
        """
        self.context = context
        self.response_data = {}

    def build(self):
        """
        Builds the response data. This method should be overridden by subclasses to implement
        specific response formatting logic.

        Returns:
            dict: A dictionary containing the response data.
        """
        self.response_data['enterprise_customer'] = self.context.enterprise_customer
        self.response_data['all_linked_enterprise_customer_users'] = self.context.all_linked_enterprise_customer_users
        self.response_data['should_update_active_enterprise_customer_user'] = (
            self.context.should_update_active_enterprise_customer_user
        )
        self.response_data['enterprise_features'] = self.context.enterprise_features
        return self.response_data, self.status_code

    def add_errors_warnings_to_response(self):
        """
        Adds any errors to the response data.
        """
        self.response_data['errors'] = self.context.errors
        self.response_data['warnings'] = self.context.warnings


class BaseLearnerResponseBuilder(BaseResponseBuilder, BaseLearnerDataMixin):
    """
    A base response builder class for learner-focused routes.

    The `BaseLearnerResponseBuilder` extends `BaseResponseBuilder` and provides shared core functionality
    for building responses across all learner-focused page routes.
    """

    def common_response_logic(self):
        """
        Applies common response logic for learner-related responses.

        Args:
            response_data (dict): The initial response data.

        Returns:
            dict: The modified response data with common logic applied.
        """
        self.response_data['enterprise_customer_user_subsidies'] = self.enterprise_customer_user_subsidies

    def build(self):
        """
        Builds the base response data for learner routes.

        This method can be overridden by subclasses to provide route-specific logic.

        Returns:
            dict: A tuple containing the base response data and status code.
        """
        super().build()

        # Initialize response data with common learner-related logic
        self.common_response_logic()

        # Add any errors, etc.
        self.add_errors_warnings_to_response()

        # Return the response data and status code
        return self.response_data, self.status_code


class LearnerDashboardResponseBuilder(BaseLearnerResponseBuilder, LearnerDashboardDataMixin):
    """
    A response builder for the learner dashboard route.

    The `LearnerDashboardResponseBuilder` extends `BaseLearnerResponseBuilder` to extract and format data
    relevant to the learner dashboard page.
    """

    def build(self):
        """
        Builds the response data for the learner dashboard route.

        This method overrides the `build` method in `BaseResponseBuilder`.

        Returns:
            dict: A tuple containing the learner dashboard serialized response data and status code.
        """
        # Build common response data
        super().build()

        # Add specific fields related to the learner dashboard
        self.response_data.update({
            'enterprise_course_enrollments': self.enterprise_course_enrollments,
            'all_enrollments_by_status': self.all_enrollments_by_status,
        })
        # Serialize and validate the response
        try:
            serializer = LearnerDashboardResponseSerializer(data=self.response_data)
            serializer.is_valid(raise_exception=True)
            serialized_data = serializer.validated_data

            # Return the response data and status code
            return serialized_data, self.status_code
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('Could not serialize the response data.')
            self.context.add_warning(
                user_message='An error occurred while processing the response data.',
                developer_message=f'Could not serialize the response data. Error: {exc}',
            )
            self.add_errors_warnings_to_response()
            serializer = LearnerDashboardResponseSerializer(self.response_data)
            serialized_data = serializer.data
            return serialized_data, self.status_code
