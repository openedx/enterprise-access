"""
Response Builder Module for bffs app
"""

import logging
from typing import Type

from rest_framework.serializers import Serializer

from enterprise_access.apps.bffs.mixins import BaseLearnerDataMixin, LearnerDashboardDataMixin
from enterprise_access.apps.bffs.serializers import (
    LearnerAcademyResponseSerializer,
    LearnerDashboardResponseSerializer,
    LearnerSearchResponseSerializer,
    LearnerSkillsQuizResponseSerializer
)
from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy

logger = logging.getLogger(__name__)


class BaseResponseBuilder:
    """
    A base response builder class that provides shared core functionality for different response builders.

    The `BaseResponseBuilder` includes methods for building response data and can be extended by specific
    response builders like `LearnerDashboardResponseBuilder` or `CourseResponseBuilder`.
    """

    serializer_class: Type[Serializer]  # Subclasses must define a serializer_class

    def __init__(self, context):
        """
        Initializes the BaseResponseBuilder with a HandlerContext.

        Args:
            context (HandlerContext): The context object containing data, errors, and request information.
        """
        self.context = context
        self.response_data = {}

    @property
    def status_code(self):
        """
        Returns the HTTP status code for the response from HandlerContext.
        """
        return self.context.status_code

    def build(self):
        """
        Builds the response data. This method should be overridden by subclasses to implement
        specific response formatting logic.

        Returns:
            dict: A dictionary containing the response data.
        """
        self.response_data['enterprise_customer'] = self.context.enterprise_customer
        self.response_data['all_linked_enterprise_customer_users'] = self.context.all_linked_enterprise_customer_users
        self.response_data['active_enterprise_customer'] = self.context.active_enterprise_customer
        self.response_data['staff_enterprise_customer'] = self.context.staff_enterprise_customer
        self.response_data['should_update_active_enterprise_customer_user'] = (
            self.context.should_update_active_enterprise_customer_user
        )
        self.response_data['enterprise_features'] = self.context.enterprise_features
        self.response_data['algolia'] = self.context.algolia
        self.response_data['catalog_uuids_to_catalog_query_uuids'] = self.context.catalog_uuids_to_catalog_query_uuids

    def add_errors_warnings_to_response(self):
        """
        Adds any errors to the response data.
        """
        self.response_data['errors'] = self.context.errors
        self.response_data['warnings'] = self.context.warnings

    def serialize(self):
        """
        Serializes the response data. If serialization fails, it logs the serialization error
        as a warning in the BFF response.

        Returning a partially invalid serialized response is better than returning an error here to
        return any data that was successfully serialized to support as much of the corresponding
        frontend page route as possible.
        """
        if not hasattr(self, 'serializer_class') or self.serializer_class is None:
            raise NotImplementedError("Subclasses must define a serializer_class.")

        serializer = self.serializer_class(data=self.response_data)

        try:
            serializer.is_valid(raise_exception=True)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('Could not serialize the response data.')
            self.context.add_warning(
                user_message='An error occurred while processing the response data.',
                developer_message=f'Could not serialize the response data. Error: {exc}',
            )

        serialized_data = serializer.data
        serialized_data['errors'] = self.context.errors
        serialized_data['warnings'] = self.context.warnings
        serialized_data['enterprise_features'] = getattr(self.context, 'enterprise_features', {})
        return serialized_data, self.status_code


class UnauthenticatedBaseResponseBuilder(BaseResponseBuilder):
    """
    A ResponseBuilder class for unauthenticated requests, where we don't
    expect customer or user inputs or outputs to exist.
    """
    def build(self):
        """
        Does no enterprise- or user- specific action by default.
        """


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

    serializer_class = LearnerDashboardResponseSerializer

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
            'has_bnr_enabled_policy': bool(SubsidyAccessPolicy.has_bnr_enabled_policy_for_enterprise(
                self.context.enterprise_customer_uuid
            )),
        })


class LearnerSearchResponseBuilder(BaseLearnerResponseBuilder):
    """
    A response builder for the learner search route.

    Extends `BaseLearnerResponseBuilder` to extract and format data
    relevant to the learner search page.
    """

    serializer_class = LearnerSearchResponseSerializer


class LearnerAcademyResponseBuilder(BaseLearnerResponseBuilder):
    """
    A response builder for the learner academy route.

    Extends `BaseLearnerResponseBuilder` to extract and format data
    relevant to the learner academy detail page.
    """

    serializer_class = LearnerAcademyResponseSerializer


class LearnerSkillsQuizResponseBuilder(BaseLearnerResponseBuilder):
    """
    A response builder for the learner academy route.

    Extends `BaseLearnerResponseBuilder` to extract and format data
    relevant to the learner skills quiz page.
    """

    serializer_class = LearnerSkillsQuizResponseSerializer
