from unittest import mock
from unittest.mock import MagicMock

import ddt
from rest_framework import status
from pytest_dictsdiff import check_objects

from enterprise_access.apps.api_client.tests.test_license_manager_client import MockLicenseManagerMetadataMixin
from enterprise_access.apps.bffs.response_builder import BaseLearnerResponseBuilder, BaseResponseBuilder
from enterprise_access.apps.bffs.serializers import (
    BaseResponseSerializer,
)
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
            'should_update_active_enterprise_customer_user': self.mock_should_update_active_enterprise_customer_user,
            'errors': [],
            'warnings': [],
        }

        response_data, status_code = base_learner_response_builder.build()

        self.assertEqual(response_data, expected_response)
        self.assertEqual(status_code, status.HTTP_200_OK)
