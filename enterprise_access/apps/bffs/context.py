"""
HandlerContext for bffs app.
"""

from rest_framework import status

from enterprise_access.apps.api_client.lms_client import LmsApiClient, LmsUserApiClient
from enterprise_access.apps.bffs import serializers


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

        # API clients
        self.lms_api_client = LmsApiClient()
        self.lms_user_api_client = LmsUserApiClient(request)

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
        return self.data.get('enterprise_customer', {})

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
            enterprise_customer_users_data = self.lms_user_api_client.get_enterprise_customers_for_user(
                self.user.username,
                traverse_pagination=True
            )
        except Exception as e:  # pylint: disable=broad-except
            self.add_error(
                user_message='Error retrieving linked enterprise customers',
                developer_message=str(e)
            )
            return

        # Set enterprise features from the response
        self._enterprise_features = enterprise_customer_users_data.get('enterprise_features', {})

        # Parse the enterprise customer user data
        enterprise_customer_users = enterprise_customer_users_data.get('results', [])
        active_enterprise_customer = self._get_active_enterprise_customer(enterprise_customer_users)
        enterprise_customer_user_for_requested_customer = next(
            (
                enterprise_customer_user
                for enterprise_customer_user in enterprise_customer_users
                if self._enterprise_customer_matches_slug_or_uuid(enterprise_customer_user.get('enterprise_customer'))
            ),
            None
        )

        # If no enterprise customer user is found for the requested customer (i.e., request user not explicitly
        # linked), but the request user is staff, attempt to retrieve enterprise customer metadata from the
        # `/enterprise-customer/` LMS API endpoint instead.
        if not enterprise_customer_user_for_requested_customer:
            staff_enterprise_customer = self._get_staff_enterprise_customer()
        else:
            staff_enterprise_customer = None

        # Determine the enterprise customer user to display
        requested_enterprise_customer = (
            enterprise_customer_user_for_requested_customer.get('enterprise_customer')
            if enterprise_customer_user_for_requested_customer else None
        )
        enterprise_customer = self._determine_enterprise_customer_for_display(
            active_enterprise_customer=active_enterprise_customer,
            requested_enterprise_customer=requested_enterprise_customer,
            staff_enterprise_customer=staff_enterprise_customer,
        )

        # Update the context data with the enterprise customer user information
        self.data.update({
            'enterprise_customer': enterprise_customer,
            'active_enterprise_customer': active_enterprise_customer,
            'all_linked_enterprise_customer_users': enterprise_customer_users,
            'staff_enterprise_customer': staff_enterprise_customer,
        })

    def _get_active_enterprise_customer(self, enterprise_customer_users):
        """
        Get the active enterprise customer user from the list of enterprise customer users.
        """
        active_enterprise_customer_user = next(
            (
                enterprise_customer_user
                for enterprise_customer_user in enterprise_customer_users
                if enterprise_customer_user.get('active', False)
            ),
            None
        )
        if active_enterprise_customer_user:
            return active_enterprise_customer_user.get('enterprise_customer')
        return None

    def _get_staff_enterprise_customer(self):
        """
        Retrieve enterprise customer metadata from `enterprise-customer` LMS API endpoint
        if there is no enterprise customer user for the request enterprise and the user is staff.
        """
        has_enterprise_customer_slug_or_uuid = self.enterprise_customer_slug or self.enterprise_customer_uuid
        if has_enterprise_customer_slug_or_uuid and self.user.is_staff:
            try:
                staff_enterprise_customer = self.lms_api_client.get_enterprise_customer_data(
                    enterprise_customer_uuid=self.enterprise_customer_uuid,
                    enterprise_customer_slug=self.enterprise_customer_slug,
                )
                return staff_enterprise_customer
            except Exception as e:  # pylint: disable=broad-except
                self.add_error(
                    user_message='Error retrieving enterprise customer data',
                    developer_message=str(e)
                )
        return None

    def _determine_enterprise_customer_for_display(
        self,
        active_enterprise_customer=None,
        requested_enterprise_customer=None,
        staff_enterprise_customer=None,
    ):
        """
        Determine the enterprise customer user for display.

        Returns:
            The enterprise customer user for display.
        """
        if not self.enterprise_customer_slug and not self.enterprise_customer_uuid:
            # No enterprise customer specified in the request, so return the active enterprise customer
            return active_enterprise_customer

        # If the requested enterprise does not match the active enterprise customer user's slug/uuid
        # and there is a linked enterprise customer user for the requested enterprise, return the
        # linked enterprise customer.
        request_matches_active_enterprise_customer = self._request_matches_active_enterprise_customer(
            active_enterprise_customer
        )
        if not request_matches_active_enterprise_customer and requested_enterprise_customer:
            return requested_enterprise_customer

        # If the request user is staff and the requested enterprise does not match the active enterprise
        # customer user's slug/uuid, return the staff-enterprise customer.
        if staff_enterprise_customer:
            return staff_enterprise_customer

        # Otherwise, return the active enterprise customer.
        return active_enterprise_customer

    def _request_matches_active_enterprise_customer(self, active_enterprise_customer):
        """
        Check if the request matches the active enterprise customer.
        """
        slug_matches_active_enterprise_customer = (
            active_enterprise_customer and active_enterprise_customer.get('slug') == self.enterprise_customer_slug
        )
        uuid_matches_active_enterprise_customer = (
            active_enterprise_customer and active_enterprise_customer.get('uuid') == self.enterprise_customer_uuid
        )
        return (
            slug_matches_active_enterprise_customer or uuid_matches_active_enterprise_customer
        )

    def _enterprise_customer_matches_slug_or_uuid(self, enterprise_customer):
        """
        Check if the enterprise customer matches the slug or UUID.
        Args:
            enterprise_customer: The enterprise customer data.
        Returns:
            True if the enterprise customer matches the slug or UUID, otherwise False.
        """
        if not enterprise_customer:
            return False

        return (
            enterprise_customer.get('slug') == self.enterprise_customer_slug or
            enterprise_customer.get('uuid') == self.enterprise_customer_uuid
        )

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
