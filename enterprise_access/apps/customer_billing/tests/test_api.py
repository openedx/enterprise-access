"""
Unit tests for the ``enterprise_access.apps.customer_billing.api`` module.
"""
from unittest import mock

import ddt
import requests
from django.test import TestCase, override_settings

from enterprise_access.apps.customer_billing import api as customer_billing_api


def raise_404_error(*args, **kwargs):
    mock_404_response = requests.Response()
    mock_404_response.status_code = 404
    mock_404_response.raise_for_status()


@override_settings(
    SSP_PRODUCTS={
        'quarterly_license_plan': {
            'stripe_price_id': 'price_ABC',
            'quantity_range': (5, 30),
        },
        'yearly_license_plan': {
            'stripe_price_id': 'price_XYZ',
            'quantity_range': (5, 30),
        },
    },
    SSP_TRIAL_PERIOD_DAYS=14,
)
@ddt.ddt
class TestCreateFreeTrialCheckoutSession(TestCase):
    """
    Tests for the ``create_free_trial_checkout_session()`` function.
    """
    def tearDown(self):
        customer_billing_api._get_lms_user_id.cache_clear()  # pylint: disable=protected-access

    @mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True)
    @mock.patch.object(customer_billing_api, 'stripe', autospec=True)
    def test_create_free_trial_checkout_session_success(self, mock_stripe, mock_lms_client_class):
        """
        Happy path for ``create_free_trial_checkout_session()``.
        """
        # Setup mocks library methods.
        mock_lms_client = mock_lms_client_class.return_value
        mock_lms_client.get_lms_user_account.return_value = [{'id': 9876}]
        mock_lms_client.get_enterprise_customer_data.side_effect = raise_404_error
        mock_stripe.checkout.Session.create.return_value = {'id': 'test-stripe-checkout-session'}
        mock_stripe.Customer.search.return_value.data = []

        # Actually call the API under test.
        result = customer_billing_api.create_free_trial_checkout_session(
            admin_email='test@example.com',
            enterprise_slug='my-sluggy',
            quantity=20,
            stripe_price_id='price_ABC',
        )

        # Assert API response.
        self.assertEqual(
            result,
            {'id': 'test-stripe-checkout-session'},
        )

        # Assert library methods were called correctly.

        # The "once" is relevant here because it's abstracted by a cached function called twice.
        mock_lms_client.get_lms_user_account.assert_called_once_with(
            email='test@example.com',
        )
        mock_lms_client.get_enterprise_customer_data.assert_called_once_with(
            enterprise_customer_slug='my-sluggy',
        )
        mock_stripe.checkout.Session.create.assert_called_once_with(
            mode='subscription',
            ui_mode='custom',
            line_items=[{
                'price': 'price_ABC',
                'quantity': 20,
            }],
            subscription_data={
                'trial_period_days': 14,
                'trial_settings': {
                    'end_behavior': {'missing_payment_method': 'cancel'},
                },
                'metadata': {
                    'enterprise_customer_slug': 'my-sluggy',
                    'lms_user_id': '9876',
                }
            },
            payment_method_collection='always',
        )

    @ddt.data(
        {
            'email_registered': False,
            'expected_validation_errors': {
                'admin_email': {
                    'error_code': 'not_registered',
                    'developer_message': 'Given email address does not correspond to an existing user.',
                }
            }
        },
        {
            'request_enterprise_slug': 'weird#slug',
            'expected_validation_errors': {
                'enterprise_slug': {
                    'error_code': 'invalid_format',
                    'developer_message': 'Invalid format for given slug.',
                }
            }
        },
        {
            'customer_exists': True,
            'is_admin_for_existing_customer': False,
            'expected_validation_errors': {
                'enterprise_slug': {
                    'error_code': 'existing_enterprise_customer',
                    'developer_message': 'The slug conflicts with an existing customer.',
                }
            }
        },
        {
            'customer_exists': True,
            'is_admin_for_existing_customer': True,
            'expected_validation_errors': {
                'enterprise_slug': {
                    'error_code': 'existing_enterprise_customer',
                    'developer_message': 'The slug conflicts with an existing customer.',
                }
            }
        },
        {
            'request_quantity': -1,
            'expected_validation_errors': {
                'quantity': {
                    'error_code': 'invalid_format',
                    'developer_message': 'Must be a positive integer.',
                }
            }
        },
        {
            'request_quantity': "foo",
            'expected_validation_errors': {
                'quantity': {
                    'error_code': 'invalid_format',
                    'developer_message': 'Must be a positive integer.',
                }
            }
        },
        {
            'request_quantity': 100,
            'expected_validation_errors': {
                'quantity': {
                    'error_code': 'range_exceeded',
                    'developer_message': 'Exceeded allowed range for given stripe_price_id.',
                }
            }
        },
        {
            'request_stripe_price_id': 'price_not-configured',
            'expected_validation_errors': {
                'stripe_price_id': {
                    'error_code': 'does_not_exist',
                    'developer_message': 'This stripe_price_id has not been configured.',
                },
                'quantity': {
                    'developer_message': 'Not enough parameters were given.',
                    'error_code': 'incomplete_data',
                },
            }
        },
    )
    @ddt.unpack
    @mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True)
    @mock.patch.object(customer_billing_api, 'stripe', autospec=True)
    def test_create_free_trial_checkout_session_errors(
        self,
        mock_stripe,
        mock_lms_client_class,
        email_registered=True,
        customer_exists=False,
        is_admin_for_existing_customer=False,
        request_enterprise_slug='my-sluggy',
        request_quantity=15,
        request_stripe_price_id='price_ABC',
        expected_validation_errors=None,
    ):
        """
        Error cases for ``create_free_trial_checkout_session()``.
        """
        # Setup mocks library methods.
        mock_lms_client = mock_lms_client_class.return_value
        if email_registered:
            mock_lms_client.get_lms_user_account.return_value = [{'id': 9876}]
        else:
            mock_lms_client.get_lms_user_account.side_effect = raise_404_error
        if customer_exists:
            if is_admin_for_existing_customer:
                mock_lms_client.get_enterprise_customer_data.return_value = {
                    'admin_users': [{'email': 'test@example.com'}]
                }
            else:
                mock_lms_client.get_enterprise_customer_data.return_value = {
                    'admin_users': []
                }
        else:
            mock_lms_client.get_enterprise_customer_data.side_effect = raise_404_error
        mock_stripe.checkout.Session.create.return_value = {'id': 'test-stripe-checkout-session'}
        mock_stripe.Customer.search.return_value.data = []

        # Actually call the API under test.
        with self.assertRaises(customer_billing_api.CreateCheckoutSessionValidationError) as cm:
            customer_billing_api.create_free_trial_checkout_session(
                admin_email='test@example.com',
                enterprise_slug=request_enterprise_slug,
                quantity=request_quantity,
                stripe_price_id=request_stripe_price_id,
            )

        actual_validation_errors = cm.exception.validation_errors_by_field
        assert actual_validation_errors == expected_validation_errors
