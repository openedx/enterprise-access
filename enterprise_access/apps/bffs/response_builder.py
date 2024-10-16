"""
Response Builder Module
"""

from enterprise_access.apps.bffs.serializers import LearnerDashboardResponseSerializer


class BaseResponseBuilder:
    """
    A base response builder class that provides shared core functionality for different response builders.

    The `BaseResponseBuilder` includes methods for building response data and can be extended by specific
    response builders like `LearnerDashboardResponseBuilder` or `CourseResponseBuilder`.
    """

    def __init__(self, context):
        """
        Initializes the BaseResponseBuilder with a HandlerContext.

        Args:
            context (HandlerContext): The context object containing data, errors, and request information.
        """
        self.context = context

    def build(self):
        """
        Builds the response data. This method should be overridden by subclasses to implement
        specific response formatting logic.

        Returns:
            dict: A dictionary containing the response data.
        """
        raise NotImplementedError("Subclasses must implement the `build` method.")

    def add_errors_warnings_to_response(self, response_data):
        """
        Adds any errors to the response data.
        """
        if self.context.errors:
            response_data['errors'] = self.context.errors

        if self.context.warnings:
            response_data['warnings'] = self.context.warnings

        return response_data

    def get_status_code(self):
        """
        Gets the current status code from the context.

        Returns:
            int: The HTTP status code.
        """
        return self.context.status_code if hasattr(self.context, 'status_code') else 200


class BaseLearnerResponseBuilder(BaseResponseBuilder):
    """
    A base response builder class for learner-focused routes.

    The `BaseLearnerResponseBuilder` extends `BaseResponseBuilder` and provides shared core functionality
    for building responses across all learner-focused page routes.
    """

    def common_response_logic(self, response_data):
        """
        Applies common response logic for learner-related responses.

        Args:
            response_data (dict): The initial response data.

        Returns:
            dict: The modified response data with common logic applied.
        """
        subscriptions_context = self.context.data.get('subscriptions', {})
        enterprise_customer_user_subsidies = response_data.get('enterprise_customer_user_subsidies', {})
        subscriptions = enterprise_customer_user_subsidies.get('subscriptions', {})
        subscriptions.update(subscriptions_context)
        enterprise_customer_user_subsidies.update({
            'subscriptions': subscriptions,
        })
        response_data['enterprise_customer_user_subsidies'] = enterprise_customer_user_subsidies
        return response_data

    def build(self):
        """
        Builds the base response data for learner routes.

        This method can be overridden by subclasses to provide route-specific logic.

        Returns:
            dict: A dictionary containing the base response data.
        """
        # Initialize response data with common learner-related logic
        response_data = {}
        response_data = self.common_response_logic(response_data)

        # Add any errors, etc.
        response_data = self.add_errors_warnings_to_response(response_data)

        # Return the response data and status code
        return response_data, self.get_status_code()





class LearnerDashboardResponseBuilder(BaseLearnerResponseBuilder):
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
            dict: A dictionary containing the learner dashboard response data.
        """
        # Initialize the response data with common learner-related fields
        response_data = self.common_response_logic({})

        # Add specific fields related to the learner dashboard
        response_data.update({
            'enterprise_course_enrollments': self.context.data.get('enterprise_course_enrollments', {}),
        })

        # Add any errors and warnings to the response
        response_data = self.add_errors_warnings_to_response(response_data)

        # Serialize and validate the response
        serializer = LearnerDashboardResponseSerializer(response_data)
        serialized_data = serializer.data

        # Return the response data and status code
        return serialized_data, self.get_status_code()
