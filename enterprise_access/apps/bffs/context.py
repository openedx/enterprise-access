"""
HandlerContext for bffs app.
"""
import logging

from rest_framework import status

from enterprise_access.apps.bffs import serializers
from enterprise_access.apps.bffs.api import (
    get_and_cache_enterprise_customer_users,
    transform_enterprise_customer_users_data
)

logger = logging.getLogger(__name__)


class HandlerContext:
    """
    A context object for managing the state throughout the lifecycle of a Backend-for-Frontend (BFF) request.
    The `HandlerContext` class stores request information, loaded data, and any errors and warnings
    that may occur during the request.
    Attributes:
        request: The original request object containing information about the incoming HTTP request.
        user: The original request user information about hte incoming HTTP request.
        data: A dictionary to store data loaded and processed by the handlers.
        errors: A list to store errors that occur during request processing.
        warnings: A list to store warnings that occur during the request processing.
        enterprise_customer_uuid: The enterprise customer uuid associated with this request.
        enterprise_customer_slug: The enterprise customer slug associated with this request.
        lms_user_id: The id associated with the authenticated user.
        enterprise_features: A dictionary to store enterprise features associated with the authenticated user.
    """

    def __init__(self, request):
        """
        Initializes the HandlerContext with request information, route, and optional initial data.
        Args:
            request: The incoming HTTP request.
        """
        self._request = request
        self._status_code = status.HTTP_200_OK
        self._errors = []  # Stores any errors that occur during processing
        self._warnings = []  # Stores any warnings that occur during processing
        self._enterprise_customer_uuid = None
        self._enterprise_customer_slug = None
        self._lms_user_id = getattr(self.user, 'lms_user_id', None)
        self._enterprise_features = {}
        self.data = {}  # Stores processed data for the response

        # Initialize common context data
        self._initialize_common_context_data()

    @property
    def request(self):
        return self._request

    @property
    def status_code(self):
        return self._status_code

    @property
    def user(self):
        return self._request.user

    @property
    def errors(self):
        return self._errors

    @property
    def warnings(self):
        return self._warnings

    @property
    def enterprise_customer_uuid(self):
        return self._enterprise_customer_uuid

    @property
    def enterprise_customer_slug(self):
        return self._enterprise_customer_slug

    @property
    def lms_user_id(self):
        return self._lms_user_id

    @property
    def enterprise_features(self):
        return self._enterprise_features

    @property
    def enterprise_customer(self):
        return self.data.get('enterprise_customer')

    @property
    def active_enterprise_customer(self):
        return self.data.get('active_enterprise_customer')

    @property
    def all_linked_enterprise_customer_users(self):
        return self.data.get('all_linked_enterprise_customer_users')

    @property
    def is_request_user_linked_to_enterprise_customer(self):
        """
        Returns True if the request user is linked to the resolved enterprise customer, False otherwise.
        Primary use case is to determine if the request user is explicitly linked to the enterprise customer versus
        being a staff user with access.
        """
        if not self.enterprise_customer:
            return False

        enterprise_customer_uuid = self.enterprise_customer.get('uuid')
        return any(
            enterprise_customer_user.get('enterprise_customer', {}).get('uuid') == enterprise_customer_uuid
            for enterprise_customer_user in self.all_linked_enterprise_customer_users
        )

    @property
    def staff_enterprise_customer(self):
        return self.data.get('staff_enterprise_customer')

    def _initialize_common_context_data(self):
        """
        Initializes common context data, like enterprise customer UUID and user ID.
        """
        enterprise_uuid_query_param = self.request.query_params.get('enterprise_customer_uuid')
        enterprise_slug_query_param = self.request.query_params.get('enterprise_customer_slug')

        enterprise_uuid_post_param = None
        enterprise_slug_post_param = None
        if self.request.method == 'POST':
            enterprise_uuid_post_param = self.request.data.get('enterprise_customer_uuid')
            enterprise_slug_post_param = self.request.data.get('enterprise_customer_slug')

        enterprise_customer_uuid = enterprise_uuid_query_param or enterprise_uuid_post_param
        self._enterprise_customer_uuid = enterprise_customer_uuid
        enterprise_customer_slug = enterprise_slug_query_param or enterprise_slug_post_param
        self._enterprise_customer_slug = enterprise_customer_slug

        # Initialize the enterprise customer users metatata derived from the LMS
        self._initialize_enterprise_customer_users()

        if not self.enterprise_customer:
            # If no enterprise customer is found, return early
            return

        # Otherwise, update the enterprise customer UUID and slug if not already set
        if not self.enterprise_customer_slug:
            self._enterprise_customer_slug = self.enterprise_customer.get('slug')
        if not self.enterprise_customer_uuid:
            self._enterprise_customer_uuid = self.enterprise_customer.get('uuid')

    def _initialize_enterprise_customer_users(self):
        """
        Initializes the enterprise customer users for the request user.
        """
        try:
            enterprise_customer_users_data = get_and_cache_enterprise_customer_users(
                self.request,
                traverse_pagination=True
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('Error retrieving linked enterprise customers')
            self.add_error(
                user_message='Error retrieving linked enterprise customers',
                developer_message=f'Could not fetch enterprise customer users. Error: {exc}'
            )
            return

        # Set enterprise features from the response
        self._enterprise_features = enterprise_customer_users_data.get('enterprise_features', {})

        # Parse/transform the enterprise customer users data and update the context data
        transformed_data = {}
        try:
            transformed_data = transform_enterprise_customer_users_data(
                enterprise_customer_users_data,
                request=self.request,
                enterprise_customer_slug=self.enterprise_customer_slug,
                enterprise_customer_uuid=self.enterprise_customer_uuid,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('Error transforming enterprise customer users data')
            self.add_error(
                user_message='Error transforming enterprise customer users data',
                developer_message=f'Could not transform enterprise customer users data. Error: {exc}'
            )

        self.data.update({
            'enterprise_customer': transformed_data.get('enterprise_customer'),
            'active_enterprise_customer': transformed_data.get('active_enterprise_customer'),
            'all_linked_enterprise_customer_users': transformed_data.get('all_linked_enterprise_customer_users', []),
            'staff_enterprise_customer': transformed_data.get('staff_enterprise_customer'),
        })

    def add_error(self, **kwargs):
        """
        Adds an error to the context.
        Output fields determined by the ErrorSerializer
        """
        serializer = serializers.ErrorSerializer(data=kwargs)
        serializer.is_valid(raise_exception=True)
        self.errors.append(serializer.data)

    def add_warning(self, **kwargs):
        """
        Adds a warning to the context.
        Output fields determined by the WarningSerializer
        """
        serializer = serializers.WarningSerializer(data=kwargs)
        serializer.is_valid(raise_exception=True)
        self.warnings.append(serializer.data)
