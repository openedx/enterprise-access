"""
Text for the BFF context
"""
from django.test import RequestFactory, TestCase
from rest_framework.exceptions import ValidationError

from enterprise_access.apps.api_client.tests.test_constants import DATE_FORMAT_ISO_8601
from enterprise_access.apps.bffs.context import HandlerContext
from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.utils import _curr_date


class TestHandlerContext(TestCase):
    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.mock_user = UserFactory()

    def test_handler_context_init(self):
        request = self.factory.get('sample/api/call')
        request.user = self.mock_user
        context = HandlerContext(request)

        self.assertEqual(context.request, request)
        self.assertEqual(context.user, self.mock_user)
        self.assertEqual(context.data, {})
        self.assertEqual(context.errors, [])
        self.assertEqual(context.warnings, [])
        self.assertEqual(context.enterprise_customer_uuid, None)
        self.assertEqual(context.lms_user_id, None)

    def test_handler_context_add_error_serializer(self):
        request = self.factory.get('sample/api/call')
        request.user = self.mock_user
        context = HandlerContext(request)
        expected_output = {
            "developer_message": "No enterprise uuid associated to the user mock-uuid",
            "user_message": "You may not be associated with the enterprise.",
        }
        # Define kwargs for add_error
        arguments = {
            **expected_output,
            "status": 403  # Add an attribute that is not explicitly defined in the serializer to verify
        }
        context.add_error(
            **arguments
        )
        self.assertEqual(expected_output, context.errors[0])

    def test_handler_context_add_error_serializer_is_valid(self):
        request = self.factory.get('sample/api/call')
        request.user = self.mock_user
        context = HandlerContext(request)
        malformed_output = {
            "developer_message": "No enterprise uuid associated to the user mock-uuid",
        }
        with self.assertRaises(ValidationError):
            context.add_error(**malformed_output)

    def test_handler_context_add_warning_serializer(self):
        request = self.factory.get('sample/api/call')
        request.user = self.mock_user
        context = HandlerContext(request)
        expected_output = {
            "developer_message": "Heuristic Expiration",
            "user_message": "The data received might be out-dated",
        }
        # Define kwargs for add_warning
        arguments = {
            **expected_output,
            "status": 113  # Add an attribute that is not explicitly defined in the serializer to verify
        }
        context.add_warning(
            **arguments
        )
        self.assertEqual(expected_output, context.warnings[0])

    def test_handler_context_add_warning_serializer_is_valid(self):
        request = self.factory.get('sample/api/call')
        request.user = self.mock_user
        context = HandlerContext(request)
        malformed_output = {
            "user_message": "The data received might be out-dated",
        }
        with self.assertRaises(ValidationError):
            context.add_error(**malformed_output)
