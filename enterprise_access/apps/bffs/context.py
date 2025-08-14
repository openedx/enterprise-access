"""
HandlerContext for bffs app.
"""
import logging
from urllib.error import HTTPError

from rest_framework import status

from enterprise_access.apps.bffs import serializers
from enterprise_access.apps.bffs.api import (
    get_and_cache_enterprise_customer_users,
    get_and_cache_secured_algolia_search_keys,
    transform_enterprise_customer_users_data,
    transform_secured_algolia_api_key_response
)

logger = logging.getLogger(__name__)


class BaseHandlerContext:
    """
    A base context object for managing the state throughout the lifecycle of a request.
    The `BaseHandlerContext` class stores request information, generic data, and any errors
    and warnings that may occur during the request, without storing any customer or user data.

    Attributes:
        request: The original request object containing information about the incoming HTTP request.
        data: A dictionary to store data loaded and processed by the handlers.
        errors: A list to store errors that occur during request processing.
        warnings: A list to store warnings that occur during the request processing.
        status_code: The HTTP status code to return in the response.
    """

    def __init__(self, request):
        """
        Initializes the BaseHandlerContext with request information.
        Args:
            request: The incoming HTTP request.
        """
        self._request = request
        self._status_code = status.HTTP_200_OK
        self._errors = []  # Stores any errors that occur during processing
        self._warnings = []  # Stores any warnings that occur during processing
        self.data = {}  # Stores processed data for the response

    @property
    def request(self):
        return self._request

    @property
    def user(self):
        return self._request.user

    @property
    def status_code(self):
        return self._status_code

    @property
    def errors(self):
        return self._errors

    @property
    def warnings(self):
        return self._warnings

    def set_status_code(self, status_code):
        """
        Sets the status code for the response.
        """
        self._status_code = status_code

    def add_error(self, status_code=None, **kwargs):
        """
        Adds an error to the context.

        Args:
            user_message (str): A user-friendly message describing the error.
            developer_message (str): A message describing the error for developers.
            [status_code] (int): The HTTP status code to return in the response.
        """
        serializer = serializers.ErrorSerializer(data=kwargs)
        serializer.is_valid(raise_exception=True)
        self.errors.append(serializer.data)
        if status_code:
            self.set_status_code(status_code)

    def add_warning(self, **kwargs):
        """
        Adds a warning to the context.

        Args:
            user_message (str): A user-friendly message describing the error.
            developer_message (str): A message describing the error for developers.
        """
        serializer = serializers.WarningSerializer(data=kwargs)
        serializer.is_valid(raise_exception=True)
        self.warnings.append(serializer.data)


class HandlerContext(BaseHandlerContext):
    """
    A context object for managing the state throughout the lifecycle of a Backend-for-Frontend (BFF) request.
    The `HandlerContext` class stores request information, loaded data, and any errors and warnings
    that may occur during the request.

    Attributes (inherited from BaseHandlerContext):
        request: The original request object containing information about the incoming HTTP request.
        data: A dictionary to store data loaded and processed by the handlers.
        errors: A list to store errors that occur during request processing.
        warnings: A list to store warnings that occur during the request processing.
        status_code: The HTTP status code to return in the response.

    Additional Attributes:
        user: The original request user information about the incoming HTTP request.
        enterprise_customer_uuid: The enterprise customer uuid associated with this request.
        enterprise_customer_slug: The enterprise customer slug associated with this request.
        lms_user_id: The id associated with the authenticated user.
        enterprise_features: A dictionary to store enterprise features associated with the authenticated user.
        enterprise_customer: The enterprise customer associated with the request.
        active_enterprise_customer: The active enterprise customer associated with the request user.
        all_linked_enterprise_customer_users: A list of all linked enterprise customer users
          associated with the request user.
        staff_enterprise_customer: The enterprise customer, if resolved as a staff request user.
        is_request_user_linked_to_enterprise_customer: A boolean indicating if the request user is linked
          to the resolved enterprise customer.
    """

    def __init__(self, request):
        """
        Initializes the HandlerContext with request information, route, and optional initial data.
        Args:
            request: The incoming HTTP request.
        """
        super().__init__(request)

        self._enterprise_customer_uuid = None
        self._enterprise_customer_slug = None
        self._lms_user_id = getattr(self.user, 'lms_user_id', None)
        self._enterprise_features = {}

        # Initialize common context data
        self._initialize_common_context_data()

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
    def staff_enterprise_customer(self):
        return self.data.get('staff_enterprise_customer')

    @property
    def all_linked_enterprise_customer_users(self):
        return self.data.get('all_linked_enterprise_customer_users')

    @property
    def should_update_active_enterprise_customer_user(self):
        return self.data.get('should_update_active_enterprise_customer_user')

    @property
    def secured_algolia_api_key(self):
        if algolia := self.data.get('algolia', {}):
            return algolia.get('secured_algolia_api_key')
        return None

    @property
    def valid_until(self):
        if algolia := self.data.get('algolia', {}):
            return algolia.get('valid_until')
        return None

    @property
    def algolia(self):
        return self.data.get('algolia')

    @property
    def catalog_uuids_to_catalog_query_uuids(self):
        return self.data.get('catalog_uuids_to_catalog_query_uuids')

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

        # Initialize the enterprise customer users metadata derived from the LMS
        try:
            self._initialize_enterprise_customer_users()
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception(
                'Error initializing enterprise customer users for request user %s, '
                'enterprise customer uuid %s and/or slug %s',
                self.lms_user_id,
                enterprise_customer_uuid,
                enterprise_customer_slug,
            )
            self.add_error(
                user_message='Error initializing enterprise customer users',
                developer_message=f'Could not initialize enterprise customer users. Error: {exc}',
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
            return

        if not self.enterprise_customer:
            # If no enterprise customer is found, return early
            logger.info(
                'No enterprise customer found for request user %s, enterprise customer uuid %s, '
                'and/or enterprise slug %s',
                self.lms_user_id,
                enterprise_customer_uuid,
                enterprise_customer_slug,
            )
            self.add_error(
                user_message='No enterprise customer found',
                developer_message=(
                    f'No enterprise customer found for request user {self.lms_user_id} and enterprise uuid '
                    f'{enterprise_customer_uuid}, and/or enterprise slug {enterprise_customer_slug}'
                ),
                status_code=status.HTTP_404_NOT_FOUND,
            )
            return

        # Otherwise, update the enterprise customer UUID and slug if not already set
        if not self.enterprise_customer_slug:
            self._enterprise_customer_slug = self.enterprise_customer.get('slug')
        if not self.enterprise_customer_uuid:
            self._enterprise_customer_uuid = self.enterprise_customer.get('uuid')

        # Initialize the secured algolia api keys metadata derived from enterprise catalog
        try:
            self._initialize_secured_algolia_api_keys()
        except HTTPError as exc:
            exception_response = exc.response.json()
            exception_response_user_message = exception_response.get('user_message')
            exception_response_developer_message = exception_response.get('developer_message')
            logger.exception(
                'HTTP Error initializing the secured algolia api keys for request user %s, '
                'enterprise customer uuid %s',
                self.lms_user_id,
                enterprise_customer_uuid,
            )
            self.add_error(
                user_message=exception_response_user_message or 'HTTP Error initializing the secured algolia api keys',
                developer_message=exception_response_developer_message or
                f'Could not initialize the secured algolia api keys. Error: {exc}',
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception(
                'Error initializing the secured algolia api keys for request user %s, '
                'enterprise customer uuid %s',
                self.lms_user_id,
                enterprise_customer_uuid,
            )
            self.add_error(
                user_message='Error initializing the secured algolia api keys',
                developer_message=f'Could not initialize the secured algolia api keys. Error: {exc}',
            )

        if not (self.secured_algolia_api_key and self.catalog_uuids_to_catalog_query_uuids and self.valid_until):
            logger.info(
                'No secured algolia key found for request user %s, enterprise customer uuid %s, '
                'and/or enterprise slug %s',
                self.lms_user_id,
                enterprise_customer_uuid,
                enterprise_customer_slug,
            )
            self.add_error(
                user_message='No secured algolia api key or catalog query mapping found',
                developer_message=(
                    f'No secured algolia api key or catalog query mapping found for request '
                    f'user {self.lms_user_id} and enterprise uuid '
                    f'{enterprise_customer_uuid}, and/or enterprise slug {enterprise_customer_slug}'
                ),
            )
            return

    def _initialize_enterprise_customer_users(self):
        """
        Initializes the enterprise customer users for the request user.
        """
        enterprise_customer_users_data = get_and_cache_enterprise_customer_users(
            self.request,
            traverse_pagination=True
        )

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
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                'Error transforming enterprise customer users metadata for request user %s, '
                'enterprise customer uuid %s and/or slug %s',
                self.lms_user_id,
                self.enterprise_customer_uuid,
                self.enterprise_customer_slug,
            )

        # Update the context data with the transformed enterprise customer users data
        self.data.update({
            'enterprise_customer': transformed_data.get('enterprise_customer'),
            'active_enterprise_customer': transformed_data.get('active_enterprise_customer'),
            'all_linked_enterprise_customer_users': transformed_data.get('all_linked_enterprise_customer_users', []),
            'staff_enterprise_customer': transformed_data.get('staff_enterprise_customer'),
            'should_update_active_enterprise_customer_user': transformed_data.get(
                'should_update_active_enterprise_customer_user',
                False
            )
        })

    def _initialize_secured_algolia_api_keys(self):
        """
        Initializes the secured algolia api key for the request user.
        """
        secured_algolia_api_key_data = get_and_cache_secured_algolia_search_keys(
            self.request,
            self._enterprise_customer_uuid,
        )

        secured_algolia_api_key = None
        valid_until = None
        catalog_uuids_to_catalog_query_uuids = {}
        try:
            (
                secured_algolia_api_key,
                catalog_uuids_to_catalog_query_uuids,
                valid_until,
            ) = transform_secured_algolia_api_key_response(
                secured_algolia_api_key_data
            )
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                'Error transforming secured algolia api key for request user %s,'
                'enterprise customer uuid %s and/or slug %s',
                self.lms_user_id,
                self.enterprise_customer_uuid,
                self.enterprise_customer_slug,
            )
        self.data.update({
            'catalog_uuids_to_catalog_query_uuids': catalog_uuids_to_catalog_query_uuids,
            'algolia': {
                'secured_algolia_api_key': secured_algolia_api_key,
                'valid_until': valid_until
            }
        })
