"""
Test utilities for BFFs.
"""
from unittest import mock

from django.test import RequestFactory, TestCase
from faker import Faker
from rest_framework import status

from enterprise_access.apps.content_assignments.tests.test_utils import mock_course_run_1
from enterprise_access.apps.core.tests.factories import UserFactory


class TestHandlerContextMixin(TestCase):
    """
    Mixin for HandlerContext tests
    """

    def setUp(self):
        super().setUp()
        self.maxDiff = None
        self.factory = RequestFactory()
        self.mock_user = UserFactory()
        self.mock_staff_user = UserFactory(is_staff=True)
        self.faker = Faker()

        self.mock_enterprise_customer_uuid = str(self.faker.uuid4())
        self.mock_enterprise_customer_slug = 'mock-slug'
        self.mock_enterprise_customer_uuid_2 = str(self.faker.uuid4())
        self.mock_enterprise_customer_slug_2 = 'mock-slug-2'

        # Mock request
        self.request = self.factory.get('sample/api/call')
        self.request.user = self.mock_user
        self.request.query_params = {
            'enterprise_customer_uuid': self.mock_enterprise_customer_uuid
        }
        self.request.data = {}

        # Mock HandlerContext
        self.mock_handler_context = self.get_mock_handler_context()

        # Mock enterprise customer data
        self.mock_enterprise_customer = {
            'uuid': self.mock_enterprise_customer_uuid,
            'slug': self.mock_enterprise_customer_slug,
            'enable_learner_portal': True,
        }
        self.mock_enterprise_customer_2 = {
            'uuid': self.mock_enterprise_customer_uuid_2,
            'slug': self.mock_enterprise_customer_slug_2,
            'enable_learner_portal': True,
        }
        self.mock_enterprise_learner_response_data = {
            'results': [
                {
                    'active': True,
                    'enterprise_customer': self.mock_enterprise_customer,
                },
                {
                    'active': False,
                    'enterprise_customer': self.mock_enterprise_customer_2,
                },
            ],
            'enterprise_features': {'feature_flag': True}
        }
        self.mock_error = {
            "developer_message": "No enterprise uuid associated to the user mock-uuid",
            "user_message": "You may not be associated with the enterprise.",
        }
        self.mock_warning = {
            "developer_message": "Heuristic Expiration",
            "user_message": "The data received might be out-dated",
        }

    def get_mock_handler_context(self, **kwargs):
        """
        Returns a mock handler context with optional overrides.
        Any attribute in the context can be overridden by passing it as a keyword argument.
        """
        # Create default values for the mock HandlerContext
        default_values = {
            '_request': self.request,
            '_status_code': status.HTTP_200_OK,
            '_errors': [],
            '_warnings': [],
            '_enterprise_customer_uuid': self.mock_enterprise_customer_uuid,
            '_enterprise_customer_slug': self.mock_enterprise_customer_slug,
            '_lms_user_id': self.request.user.lms_user_id,
            '_enterprise_features': {'feature_a': True},
            'data': {},
        }

        # Update default values with any overrides provided via kwargs
        default_values.update(kwargs)

        # Create the mock HandlerContext
        mock_handler_context = mock.MagicMock(**default_values)

        # Define a dictionary of private attributes to property names
        property_mocks = {
            'request': getattr(mock_handler_context, '_request'),
            'status_code': getattr(mock_handler_context, '_status_code'),
            'errors': getattr(mock_handler_context, '_errors'),
            'warnings': getattr(mock_handler_context, '_warnings'),
            'enterprise_customer_uuid': getattr(mock_handler_context, '_enterprise_customer_uuid'),
            'enterprise_customer_slug': getattr(mock_handler_context, '_enterprise_customer_slug'),
            'lms_user_id': getattr(mock_handler_context, '_lms_user_id'),
            'enterprise_features': getattr(mock_handler_context, '_enterprise_features'),
        }

        # Override the property getters
        for property_name, private_attr in property_mocks.items():
            setattr(
                type(mock_handler_context),  # The class of the mock object
                property_name,
                mock.PropertyMock(return_value=private_attr)
            )

        return mock_handler_context
