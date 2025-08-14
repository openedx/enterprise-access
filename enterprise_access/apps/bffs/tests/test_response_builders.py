from unittest import mock
from unittest.mock import MagicMock

import ddt
from pytest_dictsdiff import check_objects
from rest_framework import status
from rest_framework.serializers import Serializer

from enterprise_access.apps.api_client.tests.test_license_manager_client import MockLicenseManagerMetadataMixin
from enterprise_access.apps.bffs.context import BaseHandlerContext
from enterprise_access.apps.bffs.response_builder import (
    BaseLearnerResponseBuilder,
    BaseResponseBuilder,
    UnauthenticatedBaseResponseBuilder
)
from enterprise_access.apps.bffs.serializers import BaseResponseSerializer, MinimalBffResponseSerializer
from enterprise_access.apps.bffs.tests.utils import TestHandlerContextMixin


class MockResponseBuilder(BaseResponseBuilder):
    """
    A mock response builder class that extends BaseResponseBuilder.
    """

    serializer_class = BaseResponseSerializer


@ddt.ddt
class TestBaseResponseBuilder(TestHandlerContextMixin):
    """
    Tests for BaseResponseBuilder.
    """

    @mock.patch('enterprise_access.apps.bffs.context.HandlerContext')
    def test_base_build_error(self, mock_handler_context):
        mock_context_data = {
            'enterprise_customer': self.mock_enterprise_customer,
            'all_linked_enterprise_customer_users': self.mock_all_linked_enterprise_customer_users,
            'staff_enterprise_customer': self.mock_staff_enterprise_customer,
            'active_enterprise_customer': self.mock_active_enterprise_customer,
            'catalog_uuids_to_catalog_query_uuids': self.mock_catalog_uuids_to_catalog_query_uuids,
            'algolia': self.mock_algolia_object,
            'should_update_active_enterprise_customer_user': self.mock_should_update_active_enterprise_customer_user,
        }
        mock_handler_context.return_value = self.get_mock_handler_context(
            data=mock_context_data,
        )
        mock_handler_context_instance = mock_handler_context.return_value
        base_response_builder = MockResponseBuilder(mock_handler_context_instance)
        base_response_builder.build()
        response_data, status_code = base_response_builder.serialize()
        expected_response_data = {
            'enterprise_customer': self.mock_enterprise_customer,
            'enterprise_features': {'feature_flag': True},
            'all_linked_enterprise_customer_users': self.mock_all_linked_enterprise_customer_users,
            'staff_enterprise_customer': self.mock_staff_enterprise_customer,
            'active_enterprise_customer': self.mock_active_enterprise_customer,
            'catalog_uuids_to_catalog_query_uuids': self.mock_catalog_uuids_to_catalog_query_uuids,
            'algolia': self.mock_algolia_object,
            'should_update_active_enterprise_customer_user': self.mock_should_update_active_enterprise_customer_user,
            'errors': [],
            'warnings': [],
        }
        self.assertEqual(status_code, status.HTTP_200_OK)
        assert check_objects(response_data, expected_response_data)

    @ddt.data(
        {
            'errors': True,
            'warnings': True,
        },
        {
            'errors': True,
            'warnings': False,
        },
        {
            'errors': False,
            'warnings': True,
        },
        {
            'errors': False,
            'warnings': False,
        }
    )
    @mock.patch('enterprise_access.apps.bffs.context.HandlerContext')
    @ddt.unpack
    def test_add_errors_warnings_to_response(self, mock_handler_context, errors, warnings):
        mock_context_data = {
            'enterprise_customer': self.mock_enterprise_customer,
            'all_linked_enterprise_customer_users': self.mock_all_linked_enterprise_customer_users,
            'staff_enterprise_customer': self.mock_staff_enterprise_customer,
            'active_enterprise_customer': self.mock_active_enterprise_customer,
            'catalog_uuids_to_catalog_query_uuids': self.mock_catalog_uuids_to_catalog_query_uuids,
            'algolia': self.mock_algolia_object,
            'should_update_active_enterprise_customer_user': self.mock_should_update_active_enterprise_customer_user,
        }
        mock_handler_context.return_value = self.get_mock_handler_context(
            data=mock_context_data,
        )
        mock_handler_context_instance = mock_handler_context.return_value
        base_response_builder = MockResponseBuilder(mock_handler_context_instance)
        expected_output = {
            'enterprise_customer': self.mock_enterprise_customer,
            'enterprise_features': {'feature_flag': True},
            'all_linked_enterprise_customer_users': self.mock_all_linked_enterprise_customer_users,
            'staff_enterprise_customer': self.mock_staff_enterprise_customer,
            'active_enterprise_customer': self.mock_active_enterprise_customer,
            'catalog_uuids_to_catalog_query_uuids': self.mock_catalog_uuids_to_catalog_query_uuids,
            'algolia': self.mock_algolia_object,
            'should_update_active_enterprise_customer_user': self.mock_should_update_active_enterprise_customer_user,
            'errors': [],
            'warnings': [],
        }

        if errors:
            mock_handler_context_instance.errors.append(self.mock_error)
            expected_output['errors'] = [self.mock_error]
        if warnings:
            mock_handler_context_instance.warnings.append(self.mock_warning)
            expected_output['warnings'] = [self.mock_warning]
        base_response_builder.add_errors_warnings_to_response()
        base_response_builder.build()
        response_data, _ = base_response_builder.serialize()
        assert check_objects(response_data, expected_output)

    # TODO Revisit this function in ENT-9633 to determine if 200 is ok for a nested errored response
    @ddt.data(
        {
            'status_code': status.HTTP_400_BAD_REQUEST
        },
        {
            'status_code': None
        }
    )
    @mock.patch('enterprise_access.apps.bffs.context.HandlerContext')
    @ddt.unpack
    def test_status_code(self, mock_handler_context, status_code):
        if status_code:
            mock_handler_context.return_value = self.get_mock_handler_context(
                _status_code=status_code
            )
        else:
            mock_handler_context.return_value = self.mock_handler_context
        mock_handler_context_instance = mock_handler_context.return_value
        base_response_builder = MockResponseBuilder(mock_handler_context_instance)
        expected_output = status_code if status_code else status.HTTP_200_OK
        response_status_code = base_response_builder.status_code
        self.assertEqual(response_status_code, expected_output)


@ddt.ddt
class TestBaseLearnerResponseBuilder(TestBaseResponseBuilder, MockLicenseManagerMetadataMixin):
    """
    Test suite for BaseLearnerResponseBuilder.
    """
    def setUp(self):
        super().setUp()
        self.mock_enterprise_catalog_uuid_1 = self.faker.uuid4()
        self.mock_enterprise_catalog_uuid_2 = self.faker.uuid4()

        self.mock_subscription_plan_uuid_1 = self.faker.uuid4()
        self.mock_subscription_plan_uuid_2 = self.faker.uuid4()

        self.mock_customer_agreement_uuid_1 = self.faker.uuid4()
        self.mock_customer_agreement_uuid_2 = self.faker.uuid4()

    @ddt.data(
        {'has_subscriptions_data': True},
        {'has_subscriptions_data': False}
    )
    @mock.patch('enterprise_access.apps.bffs.context.HandlerContext')
    @ddt.unpack
    def test_build(self, mock_handler_context, has_subscriptions_data):
        mock_context_data = {
            'enterprise_customer': self.mock_enterprise_customer,
            'all_linked_enterprise_customer_users': self.mock_all_linked_enterprise_customer_users,
            'staff_enterprise_customer': self.mock_staff_enterprise_customer,
            'active_enterprise_customer': self.mock_active_enterprise_customer,
            'catalog_uuids_to_catalog_query_uuids': self.mock_catalog_uuids_to_catalog_query_uuids,
            'algolia': self.mock_algolia_object,
            'should_update_active_enterprise_customer_user': self.mock_should_update_active_enterprise_customer_user,
        }
        mock_handler_context.return_value = self.get_mock_handler_context(
            data=mock_context_data,
        )
        mock_handler_context_instance = mock_handler_context.return_value
        base_learner_response_builder = BaseLearnerResponseBuilder(mock_handler_context_instance)
        mock_subscriptions_data = {
            "customer_agreement": None,
            "subscription_licenses": [],
            "subscription_licenses_by_status": {
                'activated': [],
                'assigned': [],
                'revoked': [],
            },
        }

        if has_subscriptions_data:
            mock_subscriptions_data.update({
                "customer_agreement": self.mock_customer_agreement,
                "subscription_licenses": [self.mock_subscription_license],
                "subscription_licenses_by_status": {
                    **mock_subscriptions_data['subscription_licenses_by_status'],
                    'activated': [self.mock_subscription_license],
                },
            })

        mock_handler_context_instance.data['enterprise_customer_user_subsidies'] = {
            'subscriptions': mock_subscriptions_data,
        }

        expected_response = {
            'enterprise_customer': self.mock_enterprise_customer,
            'enterprise_features': {'feature_flag': True},
            'enterprise_customer_user_subsidies': {
                'subscriptions': mock_subscriptions_data,
            },
            'staff_enterprise_customer': self.mock_staff_enterprise_customer,
            'active_enterprise_customer': self.mock_active_enterprise_customer,
            'all_linked_enterprise_customer_users': self.mock_all_linked_enterprise_customer_users,
            'catalog_uuids_to_catalog_query_uuids': self.mock_catalog_uuids_to_catalog_query_uuids,
            'algolia': self.mock_algolia_object,
            'should_update_active_enterprise_customer_user': self.mock_should_update_active_enterprise_customer_user,
            'errors': [],
            'warnings': [],
        }

        response_data, status_code = base_learner_response_builder.build()

        self.assertEqual(response_data, expected_response)
        self.assertEqual(status_code, status.HTTP_200_OK)


class MockUnauthenticatedBaseResponseBuilder(UnauthenticatedBaseResponseBuilder):
    """
    Test UnauthenticatedBaseResponseBuilder implementation.
    """
    serializer_class = MinimalBffResponseSerializer


@ddt.ddt
class TestUnauthenticatedBaseResponseBuilder(TestHandlerContextMixin):
    """
    Tests for UnauthenticatedBaseResponseBuilder with unauthenticated requests using BaseHandlerContext.
    """
    EXPECTED_EMPTY_RESPONSE_DATA = {
        'errors': [],
        'warnings': [],
        'enterprise_features': {},
    }

    def get_mock_base_handler_context(self, data=None, errors=None, warnings=None, _status_code=None):
        """
        Creates a mock BaseHandlerContext for testing unauthenticated scenarios.

        Args:
            data (dict): Data to store in the context
            errors (list): List of errors
            warnings (list): List of warnings
            _status_code (int): HTTP status code

        Returns:
            MagicMock: BaseHandlerContext instance
        """
        mock_context = BaseHandlerContext(request=MagicMock())
        mock_context.data = data or {}
        mock_context._errors = errors or []
        mock_context._warnings = warnings or []
        mock_context._status_code = _status_code or status.HTTP_200_OK

        return mock_context

    @mock.patch('enterprise_access.apps.bffs.context.BaseHandlerContext')
    def test_unauthenticated_build_empty_response(self, mock_base_handler_context):
        """
        Test UnauthenticatedBaseResponseBuilder builds an empty response by default.
        Since build() does nothing, the response should only contain errors and warnings.
        """
        mock_base_handler_context.return_value = self.get_mock_base_handler_context()
        mock_context_instance = mock_base_handler_context.return_value

        response_builder = MockUnauthenticatedBaseResponseBuilder(mock_context_instance)
        response_builder.build()
        response_builder.add_errors_warnings_to_response()
        response_data, status_code = response_builder.serialize()

        # Since build() does nothing, we expect a minimal response with just errors/warnings
        self.assertEqual(status_code, status.HTTP_200_OK)
        assert check_objects(response_data, self.EXPECTED_EMPTY_RESPONSE_DATA)

    @mock.patch('enterprise_access.apps.bffs.context.BaseHandlerContext')
    def test_unauthenticated_build_with_context_data_ignored(self, mock_base_handler_context):
        """
        Test that UnauthenticatedBaseResponseBuilder ignores any data in the context.
        Even if context has data, build() does nothing so it won't be included in response.
        """
        # Add some data to context that would normally be used
        mock_context_data = {
            'some_public_data': {'key': 'value'},
            'enterprise_features': {'public_feature': True},
        }

        mock_base_handler_context.return_value = self.get_mock_base_handler_context(
            data=mock_context_data,
        )
        mock_context_instance = mock_base_handler_context.return_value

        response_builder = MockUnauthenticatedBaseResponseBuilder(mock_context_instance)
        response_builder.build()
        response_builder.add_errors_warnings_to_response()
        response_data, status_code = response_builder.serialize()

        # Data in context should be ignored since build() does nothing
        self.assertEqual(status_code, status.HTTP_200_OK)
        assert check_objects(response_data, self.EXPECTED_EMPTY_RESPONSE_DATA)

    @ddt.data(
        {
            'errors': [{'user_message': 'Rate limit exceeded', 'developer_message': 'Too many requests'}],
            'warnings': [],
            'status_code': status.HTTP_429_TOO_MANY_REQUESTS
        },
        {
            'errors': [],
            'warnings': [{'user_message': 'Service degraded', 'developer_message': 'Using fallback data'}],
            'status_code': status.HTTP_200_OK
        },
        {
            'errors': [{'user_message': 'Service unavailable', 'developer_message': 'External API down'}],
            'warnings': [{'user_message': 'Limited functionality', 'developer_message': 'Some features disabled'}],
            'status_code': status.HTTP_503_SERVICE_UNAVAILABLE
        }
    )
    @mock.patch('enterprise_access.apps.bffs.context.BaseHandlerContext')
    @ddt.unpack
    def test_unauthenticated_build_with_errors_warnings(self, mock_base_handler_context, errors, warnings, status_code):
        """
        Test UnauthenticatedBaseResponseBuilder properly includes errors and warnings.
        Even though build() does nothing, errors and warnings should still be included.
        """
        mock_base_handler_context.return_value = self.get_mock_base_handler_context(
            errors=errors,
            warnings=warnings,
            _status_code=status_code
        )
        mock_context_instance = mock_base_handler_context.return_value

        response_builder = MockUnauthenticatedBaseResponseBuilder(mock_context_instance)
        response_builder.build()
        response_builder.add_errors_warnings_to_response()
        response_data, response_status_code = response_builder.serialize()

        self.assertEqual(response_status_code, status_code)
        assert check_objects(
            response_data,
            {
                'errors': errors,
                'warnings': warnings,
                'enterprise_features': {},
            },
        )

    @mock.patch('enterprise_access.apps.bffs.context.BaseHandlerContext')
    def test_unauthenticated_status_code_from_context(self, mock_base_handler_context):
        """
        Test that UnauthenticatedBaseResponseBuilder properly returns status code from context.
        """
        test_status_code = status.HTTP_202_ACCEPTED

        mock_base_handler_context.return_value = self.get_mock_base_handler_context(
            _status_code=test_status_code
        )
        mock_context_instance = mock_base_handler_context.return_value

        response_builder = MockUnauthenticatedBaseResponseBuilder(mock_context_instance)

        # Status code should come from context
        self.assertEqual(response_builder.status_code, test_status_code)

    @mock.patch('enterprise_access.apps.bffs.context.BaseHandlerContext')
    def test_unauthenticated_multiple_builds_safe(self, mock_base_handler_context):
        """
        Test that calling build multiple times is safe and doesn't cause issues.
        """
        mock_base_handler_context.return_value = self.get_mock_base_handler_context()
        mock_context_instance = mock_base_handler_context.return_value

        response_builder = MockUnauthenticatedBaseResponseBuilder(mock_context_instance)

        # Call build multiple times
        response_builder.build()
        response_builder.build()
        response_builder.build()

        # Should still work fine
        response_builder.add_errors_warnings_to_response()
        response_data, status_code = response_builder.serialize()

        self.assertEqual(status_code, status.HTTP_200_OK)
        assert check_objects(response_data, self.EXPECTED_EMPTY_RESPONSE_DATA)
