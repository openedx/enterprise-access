"""
HandlerContext for bffs app.
"""
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
        enterprise_customer_uuid: The enterprise customer the user is associated with.
        lms_user_id: The id associated with the authenticated user.
    """

    def __init__(self, request):
        """
        Initializes the HandlerContext with request information, route, and optional initial data.
        Args:
            request: The incoming HTTP request.
        """
        self._request = request
        self.data = {}  # Stores processed data for the response
        self.errors = []  # Stores any errors that occur during processing
        self.warnings = []  # Stores any warnings that occur during processing
        self.enterprise_customer_uuid = None
        self.lms_user_id = None

        # Set common context attributes
        self.initialize_common_context_data()

    @property
    def request(self):
        return self._request

    @property
    def user(self):
        return self._request.user

    def initialize_common_context_data(self):
        """
        Initialize commonly used context attributes, such as enterprise customer UUID and LMS user ID.
        """
        enterprise_uuid_query_param = self.request.query_params.get('enterprise_customer_uuid')
        enterprise_uuid_post_param = None
        if self.request.method == 'POST':
            enterprise_uuid_post_param = self.request.data.get('enterprise_customer_uuid')

        enterprise_customer_uuid = enterprise_uuid_query_param or enterprise_uuid_post_param
        if enterprise_customer_uuid:
            self.enterprise_customer_uuid = enterprise_customer_uuid
        else:
            raise ValueError("enterprise_customer_uuid is required for this request.")

        # Set lms_user_id from the authenticated user object in the request
        self.lms_user_id = getattr(self.user, 'lms_user_id', None)

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
