"""
HandlerContext for bffs app.
"""

class HandlerContext:
    """
    A context object for managing the state throughout the lifecycle of a Backend-for-Frontend (BFF) request.

    The `HandlerContext` class stores request information, the current route, loaded data, and any errors
    that may occur during the request.

    Attributes:
        request: The original request object containing information about the incoming HTTP request.
        route: The route for which the response is being generated.
        data: A dictionary to store data loaded and processed by the handlers.
        errors: A list to store errors that occur during request processing.
    """

    def __init__(self, request):
        """
        Initializes the HandlerContext with request information, route, and optional initial data.

        Args:
            request: The incoming HTTP request.
        """
        self.request = request
        self.user = request.user
        self.data = {}  # Stores processed data for the response
        self.errors = []  # Stores any errors that occur during processing
        self.warnings = []  # Stores any warnings that occur during processing
        self.enterprise_customer_uuid = None
        self.lms_user_id = None

    def add_error(self, user_message, developer_message):
        """
        Adds an error to the context.

        Args:
            user_message (str): A user-friendly error message.
            developer_message (str): A more detailed error message for debugging purposes.
        """
        if not (user_message and developer_message):
            raise ValueError("User message and developer message are required for errors.")

        self.errors.append({
            "user_message": user_message,
            "developer_message": developer_message,
        })
