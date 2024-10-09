"""
TODO
"""


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

    def add_errors_to_response(self, response_data):
        """
        Adds any errors to the response data.
        """
        if self.context.errors:
            response_data['errors'] = [
                error for error in self.context.errors if error['severity'] == 'error'
            ]
            response_data['warnings'] = [
                error for error in self.context.errors if error['severity'] == 'warning'
            ]
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
        response_data = self.add_errors_to_response(response_data)

        return response_data


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
        response_data = self.add_errors_to_response(response_data)

        # Retrieve the status code
        status_code = self.get_status_code()

        return response_data, status_code


class BaseResponseBuilderFactory:
    """
    A base factory to create response builders based on route information.

    The `BaseResponseBuilderFactory` provides a method to instantiate appropriate response
    builders based on route information, allowing for shared logic between specialized factories.
    """

    _response_builder_map = {}

    @classmethod
    def get_response_builder(cls, context):
        """
        Returns a route-specific response builder based on the route information in the context.

        Args:
            context (HandlerContext): The context object containing data, errors, and route information.

        Returns:
            BaseResponseBuilder: An instance of the appropriate response builder class.

        Raises:
            ValueError: If no response builder is found for the given route.
        """
        page_route = context.page_route

        response_builder_class = cls._response_builder_map.get(page_route)

        if response_builder_class is not None:
            return response_builder_class(context)

        raise ValueError(f"No response builder found for route: {page_route}")


class LearnerPortalResponseBuilderFactory(BaseResponseBuilderFactory):
    """
    A learner portal-specific factory to create response builders based on learner portal route information.

    The `LearnerPortalResponseBuilderFactory` extends `BaseResponseBuilderFactory` and provides a
    mapping of learner portal-specific routes to response builders.
    """

    _response_builder_map = {
        'dashboard': LearnerDashboardResponseBuilder,
        # Add additional routes and response builders here
    }
