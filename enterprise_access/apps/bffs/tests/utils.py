"""
Test utilities for BFFs.
"""
from unittest import mock

from django.test import RequestFactory, TestCase
from faker import Faker
from rest_framework import status

from enterprise_access.apps.api_client.tests.test_constants import DATE_FORMAT_ISO_8601, DATE_FORMAT_ISO_8601_MS
from enterprise_access.apps.content_assignments.tests.test_utils import mock_course_run_1
from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.utils import _days_from_now


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
            'active': True,
            'name': 'Mock Enterprise Customer',
            'enable_learner_portal': True,
            'site': {
                'domain': 'edX.org',
                'name': 'edX',
            },
            'branding_configuration': {
                'logo': 'https://edx.org/logo.png',
                'primary_color': '#000000',
                'secondary_color': '#000000',
                'tertiary_color': '#000000',
            },
            'enable_data_sharing_consent': True,
            'enforce_data_sharing_consent': 'at_enrollment',
            'disable_expiry_messaging_for_learner_credit': False,
            'enable_audit_enrollment': False,
            'replace_sensitive_sso_username': False,
            'enable_portal_code_management_screen': True,
            'sync_learner_profile_data': False,
            'enable_audit_data_reporting': False,
            'enable_learner_portal_offers': False,
            'enable_portal_learner_credit_management_screen': True,
            'enable_executive_education_2U_fulfillment': True,
            'enable_portal_reporting_config_screen': True,
            'enable_portal_saml_configuration_screen': True,
            'enable_portal_subscription_management_screen': True,
            'hide_course_original_price': False,
            'enable_analytics_screen': True,
            'enable_integrated_customer_learner_portal_search': True,
            'enable_generation_of_api_credentials': False,
            'enable_portal_lms_configurations_screen': True,
            'hide_labor_market_data': False,
            'modified': '2024-11-22T12:00:00Z',
            'enable_universal_link': True,
            'enable_browse_and_request': True,
            'enable_learner_portal_sidebar_message': False,
            'learner_portal_sidebar_content': None,
            'enable_pathways': True,
            'enable_programs': True,
            'enable_demo_data_for_analytics_and_lpr': False,
            'enable_academies': True,
            'enable_one_academy': False,
            'show_videos_in_learner_portal_search_results': True,
            'country': 'US',
            'enable_slug_login': False,
            'admin_users': [{
                'email': 'admin@example.com',
                'lms_user_id': 12,
            }],
            'active_integrations': [],
            'enterprise_customer_catalogs': [],
            'identity_provider': 'mock_idp',
            'identity_providers': [{
                'provider_id': 'mock_idp',
                'default_provider': True,
            }],
            'contact_email': None,
            'auth_org_id': None,
            'default_language': None,
            'enterprise_notification_banner': None,
            'reply_to': None,
            'sender_alias': None,
        }
        self.mock_all_linked_enterprise_customer_users = [{
            'id': 1,
            'user_id': 3,
            'enterprise_customer': self.mock_enterprise_customer,
            'active': self.mock_enterprise_customer.get('active'),
        }]
        self.mock_should_update_active_enterprise_customer_user = False
        self.mock_enterprise_customer_2 = {
            **self.mock_enterprise_customer,
            'uuid': self.mock_enterprise_customer_uuid_2,
            'slug': self.mock_enterprise_customer_slug_2,
            'name': 'Mock Enterprise Customer 2',
        }
        self.mock_enterprise_learner_response_data = {
            'results': [
                {
                    'id': 1,
                    'active': True,
                    'enterprise_customer': self.mock_enterprise_customer,
                    'user_id': 3,
                },
                {
                    'id': 2,
                    'active': False,
                    'enterprise_customer': self.mock_enterprise_customer_2,
                    'user_id': 6,
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
            '_enterprise_features': {'feature_flag': True},
            'data': {},
        }

        # Update default values with any overrides provided via kwargs
        default_values.update(kwargs)

        # Create the mock HandlerContext
        mock_handler_context = mock.MagicMock(**default_values)

        # Define a dictionary of private attributes to property names
        mock_property_enterprise_customer = getattr(mock_handler_context, 'data').get('enterprise_customer')
        mock_linked_enterprise_customer_users = getattr(mock_handler_context, 'data').get(
            'all_linked_enterprise_customer_users'
        )
        mock_should_update_active_enterprise_customer_user = getattr(mock_handler_context, 'data').get(
            'should_update_active_enterprise_customer_user'
        )
        property_mocks = {
            'request': getattr(mock_handler_context, '_request'),
            'status_code': getattr(mock_handler_context, '_status_code'),
            'errors': getattr(mock_handler_context, '_errors'),
            'warnings': getattr(mock_handler_context, '_warnings'),
            'enterprise_customer_uuid': getattr(mock_handler_context, '_enterprise_customer_uuid'),
            'enterprise_customer_slug': getattr(mock_handler_context, '_enterprise_customer_slug'),
            'enterprise_customer': mock_property_enterprise_customer,
            'active_enterprise_customer': mock_property_enterprise_customer,
            'staff_enterprise_customer': None,
            'lms_user_id': getattr(mock_handler_context, '_lms_user_id'),
            'enterprise_features': getattr(mock_handler_context, '_enterprise_features'),
            'all_linked_enterprise_customer_users': mock_linked_enterprise_customer_users,
            'should_update_active_enterprise_customer_user': mock_should_update_active_enterprise_customer_user,
        }

        # Override the property getters
        for property_name, private_attr in property_mocks.items():
            setattr(
                type(mock_handler_context),  # The class of the mock object
                property_name,
                mock.PropertyMock(return_value=private_attr)
            )

        return mock_handler_context


def mock_enterprise_learner_dependency(func):
    """
    Mock the enterprise customer related service dependencies.
    """
    @mock.patch('enterprise_access.apps.api_client.lms_client.LmsUserApiClient.get_enterprise_customers_for_user')
    def wrapper(self, *args, **kwargs):
        return func(self, *args, **kwargs)
    return wrapper


def mock_subsidy_dependencies(func):
    """
    Mock the service dependencies for the subsidies.
    """
    @mock.patch(
        'enterprise_access.apps.api_client.license_manager_client.LicenseManagerUserApiClient'
        '.get_subscription_licenses_for_learner'
    )
    @mock.patch(
        'enterprise_access.apps.api_client.lms_client.LmsUserApiClient'
        '.get_default_enterprise_enrollment_intentions_learner_status'
    )
    def wrapper(self, *args, **kwargs):
        return func(self, *args, **kwargs)
    return wrapper


def mock_common_dependencies(func):
    """
    Mock the common service dependencies.
    """
    @mock_enterprise_learner_dependency
    @mock_subsidy_dependencies
    def wrapper(self, *args, **kwargs):
        return func(self, *args, **kwargs)
    return wrapper


def mock_dashboard_dependencies(func):
    """
    Mock the service dependencies for the dashboard route.
    """
    @mock_common_dependencies
    @mock.patch('enterprise_access.apps.api_client.lms_client.LmsUserApiClient.get_enterprise_course_enrollments')
    def wrapper(self, *args, **kwargs):
        return func(self, *args, **kwargs)
    return wrapper
