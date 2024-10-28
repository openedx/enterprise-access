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

    @property
    def request(self):
        return self._request

    @property
    def user(self):
        return self._request.user

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
