"""
Unit tests for the ``enterprise_access.apps.customer_billing.api`` module.
"""
import uuid
from datetime import timedelta
from unittest import mock

import ddt
import requests
import stripe
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing import api as customer_billing_api
from enterprise_access.apps.customer_billing import stripe_api
from enterprise_access.apps.customer_billing.api import (
    SubsidyUnreachableError,
    evaluate_top_up_eligibility,
    evaluate_top_up_eligibility_for_policies,
    get_or_create_enterprise_stripe_customer,
    get_stripe_customer_id_for_enterprise,
    is_account_in_good_standing,
    is_top_up_enabled
)
from enterprise_access.apps.customer_billing.constants import TOP_UP_MAX_SUBSIDY_FETCH_ATTEMPTS, CheckoutIntentState
from enterprise_access.apps.customer_billing.models import (
    CheckoutIntent,
    EnterpriseStripeCustomer,
    FailedCheckoutIntentConflict,
    SlugReservationConflict,
    SspProduct
)
from enterprise_access.apps.customer_billing.tests.factories import (
    CheckoutIntentFactory,
    EnterpriseStripeCustomerFactory
)
from enterprise_access.apps.subsidy_access_policy.exceptions import SubsidyAPIHTTPError
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory
)

User = get_user_model()


def raise_404_error(*args, **kwargs):
    mock_404_response = requests.Response()
    mock_404_response.status_code = 404
    mock_404_response.raise_for_status()


QUARTERLY_PRICE_ID = 'price_test_quarterly'

MOCK_SSP_PRODUCTS = {
    'quarterly_license_plan': {
        'stripe_price_id': QUARTERLY_PRICE_ID,  # DEPRECATED: Use lookup_key instead
        'lookup_key': 'price_quarterly_0002',
        'quantity_range': (5, 30),
    },
    'yearly_license_plan': {
        'stripe_price_id': 'price_test_yearly',  # DEPRECATED: Use lookup_key instead
        'lookup_key': 'price_yearly_0001',
        'quantity_range': (5, 30),
    },
}

MOCK_SSP_PRICING_DATA = {
    'quarterly_license_plan': {
        'id': QUARTERLY_PRICE_ID,
        'lookup_key': 'price_quarterly_0002',
        'quantity_range': (5, 30),
        'unit_amount': 3300,
        'unit_amount_decimal': 33.00,
        'currency': 'usd',
        'ssp_product_key': 'quarterly_license_plan',
    },
    'yearly_license_plan': {
        'id': 'price_test_yearly',
        'lookup_key': 'price_yearly_0001',
        'quantity_range': (5, 30),
        'unit_amount': 36000,
        'unit_amount_decimal': 360.00,
        'currency': 'usd',
        'ssp_product_key': 'yearly_license_plan',
    },
}


@override_settings(
    SSP_PRODUCTS=MOCK_SSP_PRODUCTS,
    SSP_TRIAL_PERIOD_DAYS=14,
)
@ddt.ddt
class TestCreateFreeTrialCheckoutSession(TestCase):
    """
    Tests for the ``create_free_trial_checkout_session()`` function.
    """
    def setUp(self):
        self.user = UserFactory()
        self.other_user = UserFactory()

    def tearDown(self):
        # Clean up any intents created during tests
        CheckoutIntent.objects.all().delete()

    @mock.patch(
        'enterprise_access.apps.customer_billing.api.get_ssp_product_pricing',
        return_value=MOCK_SSP_PRICING_DATA,
    )
    @mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True)
    @mock.patch.object(stripe_api, 'stripe')
    def test_create_free_trial_checkout_session_success(
        self, mock_stripe, mock_lms_client_class, mock_get_ssp_pricing,  # pylint: disable=unused-argument
    ):
        """
        Happy path for ``create_free_trial_checkout_session()`` with checkout intent creation.
        """
        # Setup mocks library methods.
        mock_lms_client = mock_lms_client_class.return_value
        mock_lms_client.get_lms_user_account.return_value = [{'id': self.user.lms_user_id}]
        mock_lms_client.get_enterprise_customer_data.side_effect = raise_404_error
        mock_stripe_session = mock.Mock()
        mock_stripe_session.to_dict.return_value = {
            'id': 'test-stripe-checkout-session',
            'customer': 'cust-123',
        }
        mock_stripe.checkout.Session.create.return_value = mock_stripe_session
        mock_stripe.Customer.search.return_value.data = []

        # Actually call the API under test.
        result = customer_billing_api.create_free_trial_checkout_session(
            user=self.user,
            admin_email=self.user.email,
            enterprise_slug='my-sluggy',
            company_name='My Cool Company',
            quantity=20,
            stripe_price_id=QUARTERLY_PRICE_ID,
        )

        # Assert API response.
        self.assertEqual(
            result,
            {'id': 'test-stripe-checkout-session', 'customer': 'cust-123'},
        )

        # Assert that a CheckoutIntent was created
        intent = CheckoutIntent.objects.get(user=self.user)
        self.assertEqual(intent.state, CheckoutIntentState.CREATED)
        self.assertEqual(intent.enterprise_slug, 'my-sluggy')
        self.assertEqual(intent.enterprise_name, 'My Cool Company')
        self.assertEqual(intent.stripe_checkout_session_id, 'test-stripe-checkout-session')
        self.assertEqual(intent.stripe_customer_id, 'cust-123')
        self.assertFalse(intent.is_expired())

    @mock.patch(
        'enterprise_access.apps.customer_billing.api.get_ssp_product_pricing',
        return_value=MOCK_SSP_PRICING_DATA,
    )
    @mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True)
    @mock.patch.object(stripe_api, 'stripe')
    @mock.patch('enterprise_access.apps.customer_billing.api.create_subscription_checkout_session')
    def test_create_free_trial_checkout_session_with_ssp_product_slug(
        self,
        mock_create_checkout,
        mock_stripe,  # pylint: disable=unused-argument
        mock_lms_client_class,
        mock_get_ssp_pricing,  # pylint: disable=unused-argument
    ):
        mock_lms_client = mock_lms_client_class.return_value
        mock_lms_client.get_lms_user_account.return_value = [{'id': self.user.lms_user_id}]
        mock_lms_client.get_enterprise_customer_data.side_effect = raise_404_error
        mock_create_checkout.return_value = {'id': 'sess-ssp', 'customer': 'cust-ssp'}
        SspProduct.objects.create(
            slug='quarterly_license_plan',
            stripe_price_lookup_key=MOCK_SSP_PRODUCTS['quarterly_license_plan']['lookup_key'],
            is_active=True,
            catalog_query_uuid=uuid.uuid4(),
            catalog_query_id=1,
        )
        result = customer_billing_api.create_free_trial_checkout_session(
            user=self.user,
            admin_email=self.user.email,
            enterprise_slug='my-sluggy',
            company_name='My Cool Company',
            quantity=20,
            stripe_price_id=QUARTERLY_PRICE_ID,
            ssp_product_slug='quarterly_license_plan',
        )
        self.assertEqual(result, {'id': 'sess-ssp', 'customer': 'cust-ssp'})
        intent = CheckoutIntent.objects.get(user=self.user)
        self.assertIsNotNone(intent.ssp_product)
        called_input = mock_create_checkout.call_args[1]['input_data']
        self.assertEqual(called_input.get('ssp_product_slug'), 'quarterly_license_plan')
        self.assertEqual(called_input.get('stripe_price_id'), QUARTERLY_PRICE_ID)
        self.assertEqual(called_input.get('enterprise_slug'), 'my-sluggy')
        self.assertEqual(called_input.get('admin_email'), self.user.email)
        self.assertEqual(
            mock_create_checkout.call_args[1]['enterprise_catalog_metadata'],
            {'title': 'Open Courses', 'catalog_query_id': 1},
        )
        mock_lms_client.get_lms_user_account.assert_called_once_with(
            email=self.user.email,
        )
        mock_lms_client.get_enterprise_customer_data.assert_has_calls([
            mock.call(enterprise_customer_slug='my-sluggy'),
            mock.call(enterprise_customer_name='My Cool Company'),
        ])

    @mock.patch(
        'enterprise_access.apps.customer_billing.models.SspProduct.enterprise_catalog_metadata',
        new_callable=mock.PropertyMock,
        side_effect=ValueError('invalid catalog metadata'),
    )
    @mock.patch(
        'enterprise_access.apps.customer_billing.api.get_ssp_product_pricing',
        return_value=MOCK_SSP_PRICING_DATA,
    )
    @mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True)
    @mock.patch.object(stripe_api, 'stripe')
    @mock.patch('enterprise_access.apps.customer_billing.api.create_subscription_checkout_session')
    def test_create_free_trial_checkout_session_omits_catalog_on_metadata_failure(
        self,
        mock_create_checkout,
        mock_stripe,  # pylint: disable=unused-argument
        mock_lms_client_class,
        mock_get_ssp_pricing,  # pylint: disable=unused-argument
        mock_enterprise_catalog_metadata,
    ):
        """A ValueError from enterprise_catalog_metadata should not block checkout session creation."""
        mock_lms_client = mock_lms_client_class.return_value
        mock_lms_client.get_lms_user_account.return_value = [{'id': self.user.lms_user_id}]
        mock_lms_client.get_enterprise_customer_data.side_effect = raise_404_error
        mock_create_checkout.return_value = {'id': 'sess-ssp', 'customer': 'cust-ssp'}
        SspProduct.objects.create(
            slug='quarterly_license_plan',
            stripe_price_lookup_key=MOCK_SSP_PRODUCTS['quarterly_license_plan']['lookup_key'],
            is_active=True,
            catalog_query_uuid=uuid.uuid4(),
        )

        result = customer_billing_api.create_free_trial_checkout_session(
            user=self.user,
            admin_email=self.user.email,
            enterprise_slug='my-sluggy',
            company_name='My Cool Company',
            quantity=20,
            stripe_price_id=QUARTERLY_PRICE_ID,
            ssp_product_slug='quarterly_license_plan',
        )

        self.assertEqual(result, {'id': 'sess-ssp', 'customer': 'cust-ssp'})
        self.assertEqual(
            mock_create_checkout.call_args[1]['enterprise_catalog_metadata'],
            None,
        )
        mock_enterprise_catalog_metadata.assert_called()

    @mock.patch(
        'enterprise_access.apps.customer_billing.api.get_ssp_product_pricing',
        return_value=MOCK_SSP_PRICING_DATA,
    )
    @mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True)
    @mock.patch.object(stripe_api, 'stripe')
    @mock.patch(
        'enterprise_access.apps.customer_billing.api.CheckoutSessionInputValidator',
    )
    def test_create_free_trial_checkout_session_raises_without_stripe_price_id(
        self,
        mock_validator_class,
        mock_stripe,  # pylint: disable=unused-argument
        mock_lms_client_class,  # pylint: disable=unused-argument
        mock_get_ssp_pricing,  # pylint: disable=unused-argument
    ):
        """Ensure ValueError is raised when stripe_price_id is None or empty."""
        mock_validator_class.return_value.validate.return_value = {}
        SspProduct.objects.create(
            slug='quarterly_license_plan',
            stripe_price_lookup_key=MOCK_SSP_PRODUCTS['quarterly_license_plan']['lookup_key'],
            is_active=True,
            catalog_query_uuid=uuid.uuid4(),
        )
        for stripe_price_id in (None, ''):
            with self.subTest(stripe_price_id=stripe_price_id):
                with self.assertRaises(ValueError) as context:
                    customer_billing_api.create_free_trial_checkout_session(
                        user=self.user,
                        admin_email=self.user.email,
                        enterprise_slug='my-sluggy',
                        company_name='My Cool Company',
                        quantity=20,
                        stripe_price_id=stripe_price_id,
                        ssp_product_slug='quarterly_license_plan',
                    )
                self.assertIn('stripe_price_id is required', str(context.exception))

    @mock.patch(
        'enterprise_access.apps.customer_billing.api.get_ssp_product_pricing',
        return_value=MOCK_SSP_PRICING_DATA,
    )
    @mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True)
    @mock.patch.object(stripe_api, 'stripe')
    def test_create_free_trial_checkout_session_success_without_user(
        self, mock_stripe, mock_lms_client_class, mock_get_ssp_pricing,  # pylint: disable=unused-argument
    ):
        """
        Test that checkout session creation works without user (backwards compatibility).
        """
        # Setup mocks library methods.
        mock_lms_client = mock_lms_client_class.return_value
        mock_lms_client.get_lms_user_account.return_value = [{'id': 9876}]
        mock_lms_client.get_enterprise_customer_data.side_effect = raise_404_error
        mock_stripe.checkout.Session.create.return_value = {'id': 'test-stripe-checkout-session'}
        mock_stripe.Customer.search.return_value.data = []

        # Call without user parameter
        with self.assertRaises(customer_billing_api.CreateCheckoutSessionValidationError) as cm:
            customer_billing_api.create_free_trial_checkout_session(
                user=None,
                admin_email='test@example.com',
                enterprise_slug='my-sluggy',
                company_name='My Cool Company',
                quantity=20,
                stripe_price_id=QUARTERLY_PRICE_ID,
            )

        # Should get user validation error
        validation_errors = cm.exception.validation_errors_by_field
        self.assertIn('user', validation_errors)

    @mock.patch(
        'enterprise_access.apps.customer_billing.api.get_ssp_product_pricing',
        return_value=MOCK_SSP_PRICING_DATA,
    )
    @mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True)
    @mock.patch.object(stripe_api, 'stripe')
    def test_create_free_trial_checkout_session_replaces_user_intent(
        self, mock_stripe, mock_lms_client_class, mock_get_ssp_pricing,  # pylint: disable=unused-argument
    ):
        """
        Test that creating a new checkout session replaces the user's existing intent.
        """
        # Create an existing intent for the user
        CheckoutIntent.create_intent(
            user=self.user, quantity=10,
            slug='old-slug',
            name='Old Comapny',
            terms_metadata={'version': '1.0'}
        )

        # Setup mocks
        mock_lms_client = mock_lms_client_class.return_value
        mock_lms_client.get_lms_user_account.return_value = [{'id': 9876}]
        mock_lms_client.get_enterprise_customer_data.side_effect = raise_404_error
        mock_stripe_session = mock.Mock()
        mock_stripe_session.to_dict.return_value = {'id': 'new-stripe-session', 'customer': None}
        mock_stripe.checkout.Session.create.return_value = mock_stripe_session
        mock_stripe.Customer.search.return_value.data = []

        # Create new checkout session with different slug
        result = customer_billing_api.create_free_trial_checkout_session(
            user=self.user,
            admin_email='test@example.com',
            enterprise_slug='new-sluggy',
            company_name='New Company',
            quantity=20,
            stripe_price_id=QUARTERLY_PRICE_ID,
        )

        # Should succeed and replace the old intent
        self.assertEqual(result, {'id': 'new-stripe-session', 'customer': None})

        # Assert that a CheckoutIntent was updated
        intent = CheckoutIntent.objects.get(user=self.user)
        self.assertEqual(intent.state, CheckoutIntentState.CREATED)
        self.assertEqual(intent.enterprise_slug, 'new-sluggy')
        self.assertEqual(intent.enterprise_name, 'New Company')
        self.assertEqual(intent.stripe_checkout_session_id, 'new-stripe-session')
        self.assertFalse(intent.is_expired())

    @mock.patch(
        'enterprise_access.apps.customer_billing.api.get_ssp_product_pricing',
        return_value=MOCK_SSP_PRICING_DATA,
    )
    @mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True)
    @mock.patch.object(stripe_api, 'stripe')
    def test_slug_reservation_conflict(
        self, mock_stripe, mock_lms_client_class, mock_get_ssp_pricing,   # pylint: disable=unused-argument
    ):
        """
        Test that slug reservation prevents conflicts between different users.
        """
        # User 1 reserves a slug
        CheckoutIntent.create_intent(
            user=self.other_user,
            quantity=10,
            slug='conflicting-slug',
            name='My company',
            terms_metadata={'version': '1.0'},
        )

        # Setup mocks
        mock_lms_client = mock_lms_client_class.return_value
        mock_lms_client.get_lms_user_account.return_value = [{'id': 9876}]
        mock_lms_client.get_enterprise_customer_data.side_effect = raise_404_error

        # User 2 tries to use the same slug - should fail
        with self.assertRaises(customer_billing_api.CreateCheckoutSessionValidationError) as cm:
            customer_billing_api.create_free_trial_checkout_session(
                user=self.user,
                admin_email='test@example.com',
                enterprise_slug='conflicting-slug',
                company_name='doesnt matter',
                quantity=20,
                stripe_price_id=QUARTERLY_PRICE_ID,
            )
        # Should get slug reserved error
        validation_errors = cm.exception.validation_errors_by_field
        self.assertIn('enterprise_slug', validation_errors)
        self.assertEqual(validation_errors['enterprise_slug']['error_code'], 'slug_reserved')

    @mock.patch(
        'enterprise_access.apps.customer_billing.api.get_ssp_product_pricing',
        return_value=MOCK_SSP_PRICING_DATA,
    )
    @mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True)
    @mock.patch.object(stripe_api, 'stripe')
    def test_name_reservation_conflict(
        self, mock_stripe, mock_lms_client_class, mock_get_ssp_pricing,  # pylint: disable=unused-argument
    ):
        """
        Test that comapny name reservation prevents conflicts between different users.
        """
        # User 1 reserves a slug
        CheckoutIntent.create_intent(
            user=self.other_user,
            quantity=10,
            slug='ok-slug',
            name='Conflicting company',
            terms_metadata={'version': '1.0'},
        )

        # Setup mocks
        mock_lms_client = mock_lms_client_class.return_value
        mock_lms_client.get_lms_user_account.return_value = [{'id': 9876}]
        mock_lms_client.get_enterprise_customer_data.side_effect = raise_404_error

        # User 2 tries to use the same name - should fail
        with self.assertRaises(customer_billing_api.CreateCheckoutSessionValidationError) as cm:
            customer_billing_api.create_free_trial_checkout_session(
                user=self.user,
                admin_email='test@example.com',
                enterprise_slug='different-slug',
                company_name='Conflicting company',
                quantity=20,
                stripe_price_id=QUARTERLY_PRICE_ID,
            )
        # Should get company name conflict error
        validation_errors = cm.exception.validation_errors_by_field
        self.assertIn('company_name', validation_errors)
        self.assertEqual(validation_errors['company_name']['error_code'], 'existing_enterprise_customer')

    @mock.patch(
        'enterprise_access.apps.customer_billing.api.get_ssp_product_pricing',
        return_value=MOCK_SSP_PRICING_DATA,
    )
    @mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True)
    @mock.patch.object(stripe_api, 'stripe')
    def test_expired_intent_allows_reuse(
        self, mock_stripe, mock_lms_client_class, mock_get_ssp_pricing,  # pylint: disable=unused-argument
    ):
        """
        Test that expired intents don't block new intents.
        """
        # Create an expired intent
        expired_time = timezone.now() - timedelta(minutes=5)
        CheckoutIntent.objects.create(
            user=self.other_user,
            enterprise_slug='expired-slug',
            enterprise_name='Expired Company',
            state=CheckoutIntentState.EXPIRED,
            expires_at=expired_time,
            quantity=10,
            country='US',
            terms_metadata={'version': '1.0', 'expired': True}
        )

        # Setup mocks
        mock_lms_client = mock_lms_client_class.return_value
        mock_lms_client.get_lms_user_account.return_value = [{'id': 9876}]
        mock_lms_client.get_enterprise_customer_data.side_effect = raise_404_error
        mock_stripe_session = mock.Mock()
        mock_stripe_session.to_dict.return_value = {'id': 'test-session', 'customer': None}
        mock_stripe.checkout.Session.create.return_value = mock_stripe_session
        mock_stripe.Customer.search.return_value.data = []

        # Should be able to reserve the expired slug
        result = customer_billing_api.create_free_trial_checkout_session(
            user=self.user,
            admin_email='test@example.com',
            enterprise_slug='expired-slug',
            company_name='anything',
            quantity=20,
            stripe_price_id=QUARTERLY_PRICE_ID,
        )

        # Should succeed
        self.assertEqual(result, {'id': 'test-session', 'customer': None})

        # Assert that a CheckoutIntent was created
        intent = CheckoutIntent.objects.get(user=self.user)
        self.assertEqual(intent.state, CheckoutIntentState.CREATED)
        self.assertEqual(intent.enterprise_slug, 'expired-slug')
        self.assertEqual(intent.enterprise_name, 'anything')
        self.assertEqual(intent.stripe_checkout_session_id, 'test-session')
        self.assertFalse(intent.is_expired())

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
        {
            'request_quantity': 0,
            'request_stripe_price_id': QUARTERLY_PRICE_ID,
            'expected_validation_errors': {
                'quantity': {
                    'error_code': 'incomplete_data',
                    'developer_message': 'Not enough parameters were given.',
                }
            }
        },
    )
    @ddt.unpack
    @mock.patch(
        'enterprise_access.apps.customer_billing.api.get_ssp_product_pricing',
        return_value=MOCK_SSP_PRICING_DATA,
    )
    @mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True)
    @mock.patch.object(stripe_api, 'stripe')
    def test_create_free_trial_checkout_session_errors(
        self,
        mock_stripe,
        mock_lms_client_class,
        mock_get_ssp_pricing,  # pylint: disable=unused-argument
        email_registered=True,
        customer_exists=False,
        is_admin_for_existing_customer=False,
        request_enterprise_slug='my-sluggy',
        request_quantity=15,
        request_stripe_price_id=QUARTERLY_PRICE_ID,
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
                user=self.user,  # Include user in error cases too
                admin_email='test@example.com',
                enterprise_slug=request_enterprise_slug,
                quantity=request_quantity,
                stripe_price_id=request_stripe_price_id,
            )

        actual_validation_errors = cm.exception.validation_errors_by_field
        assert actual_validation_errors == expected_validation_errors

    @mock.patch(
        'enterprise_access.apps.customer_billing.'
        'api.get_ssp_product_pricing',
        return_value=MOCK_SSP_PRICING_DATA
    )
    @mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True)
    @mock.patch.object(CheckoutIntent, 'create_intent')
    def test_create_intent_raises_slug_reservation_conflict(self, mock_create_intent, mock_lms_client_class, _):
        """
        Test that SlugReservationConflict from create_intent is wrapped in CreateCheckoutSessionSlugReservationConflict.
        """
        mock_lms = mock_lms_client_class.return_value
        mock_lms.get_lms_user_account.return_value = [{'id': self.user.lms_user_id}]
        mock_lms.get_enterprise_customer_data.side_effect = raise_404_error
        mock_create_intent.side_effect = SlugReservationConflict()
        with self.assertRaises(customer_billing_api.CreateCheckoutSessionSlugReservationConflict) as cm:
            customer_billing_api.create_free_trial_checkout_session(
                user=self.user, admin_email=self.user.email, enterprise_slug='s',
                company_name='C', quantity=10, stripe_price_id=QUARTERLY_PRICE_ID,
            )
        self.assertEqual(cm.exception.non_field_errors[0]['error_code'], 'checkout_intent_conflict_slug_reserved')

    @mock.patch(
        'enterprise_access.apps.customer_billing.'
        'api.get_ssp_product_pricing',
        return_value=MOCK_SSP_PRICING_DATA
    )
    @mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True)
    @mock.patch.object(CheckoutIntent, 'create_intent')
    def test_create_intent_raises_failed_conflict(self, mock_create_intent, mock_lms_client_class, _):
        """
        Test that FailedCheckoutIntentConflict from create_intent is wrapped in CreateCheckoutSessionFailedConflict.
        """
        mock_lms = mock_lms_client_class.return_value
        mock_lms.get_lms_user_account.return_value = [{'id': self.user.lms_user_id}]
        mock_lms.get_enterprise_customer_data.side_effect = raise_404_error
        mock_create_intent.side_effect = FailedCheckoutIntentConflict()
        with self.assertRaises(customer_billing_api.CreateCheckoutSessionFailedConflict) as cm:
            customer_billing_api.create_free_trial_checkout_session(
                user=self.user, admin_email=self.user.email, enterprise_slug='s',
                company_name='C', quantity=10, stripe_price_id=QUARTERLY_PRICE_ID,
            )
        self.assertEqual(cm.exception.non_field_errors[0]['error_code'], 'checkout_intent_conflict_failed')


class TestCreateStripeBillingPortalSession(TestCase):
    """
    Tests for the ``create_stripe_billing_portal_session()`` function.
    """

    def test_no_customer_id_raises_value_error(self):
        """Missing stripe_customer_id raises ValueError."""
        intent = mock.Mock(stripe_customer_id=None, id='intent-1')
        with self.assertRaises(ValueError):
            customer_billing_api.create_stripe_billing_portal_session(intent, 'https://return.url')

    @mock.patch('enterprise_access.apps.customer_billing.api.stripe')
    def test_success(self, mock_stripe):
        """Happy path returns a portal session."""
        intent = mock.Mock(stripe_customer_id='cus_123', id='intent-1')
        mock_stripe.billing_portal.Session.create.return_value = mock.Mock(id='bps_1')
        result = customer_billing_api.create_stripe_billing_portal_session(intent, 'https://return.url')
        self.assertEqual(result.id, 'bps_1')
        mock_stripe.billing_portal.Session.create.assert_called_once_with(
            customer='cus_123', return_url='https://return.url',
        )

    @mock.patch('enterprise_access.apps.customer_billing.api.stripe')
    def test_stripe_error_propagates(self, mock_stripe):
        """StripeError from portal creation is re-raised after logging."""
        intent = mock.Mock(stripe_customer_id='cus_123', id='intent-1')
        mock_stripe.StripeError = stripe.StripeError
        mock_stripe.billing_portal.Session.create.side_effect = stripe.StripeError('fail')
        with self.assertRaises(stripe.StripeError):
            customer_billing_api.create_stripe_billing_portal_session(intent, 'https://return.url')


@mock.patch('enterprise_access.apps.customer_billing.api.get_ssp_product_pricing')
class TestValidatorHandlerEdgeCases(TestCase):
    """
    Direct handler tests for uncovered exception/edge-case branches.
    """

    def setUp(self):
        self.validator = customer_billing_api.CheckoutSessionInputValidator()

    # ── handle_quantity: except Exception when get_ssp_product_pricing raises ──
    def test_handle_quantity_pricing_exception(self, mock_pricing):
        mock_pricing.side_effect = RuntimeError('boom')
        result = self.validator.handle_quantity({'quantity': 10, 'stripe_price_id': QUARTERLY_PRICE_ID})
        self.assertEqual(result['error_code'], 'incomplete_data')

    # ── handle_ssp_product_slug: non-string input → INVALID_FORMAT ──
    def test_handle_ssp_product_slug_invalid_format(self, mock_pricing):
        result = self.validator.handle_ssp_product_slug({'ssp_product_slug': 123})
        self.assertEqual(result['error_code'], 'invalid_format')
        mock_pricing.assert_not_called()

    # ── handle_ssp_product_slug: unknown slug → DOES_NOT_EXIST ──
    def test_handle_ssp_product_slug_not_found(self, mock_pricing):
        mock_pricing.return_value = MOCK_SSP_PRICING_DATA
        result = self.validator.handle_ssp_product_slug({'ssp_product_slug': 'nonexistent_plan'})
        self.assertEqual(result['error_code'], 'does_not_exist')

    # ── handle_ssp_product_slug: pricing call raises → DOES_NOT_EXIST ──
    def test_handle_ssp_product_slug_pricing_exception(self, mock_pricing):
        mock_pricing.side_effect = RuntimeError('boom')
        result = self.validator.handle_ssp_product_slug({'ssp_product_slug': 'quarterly_license_plan'})
        self.assertEqual(result['error_code'], 'does_not_exist')

    # ── handle_stripe_price_id: non-string input → INVALID_FORMAT ──
    def test_handle_stripe_price_id_invalid_format(self, mock_pricing):
        result = self.validator.handle_stripe_price_id({'stripe_price_id': 999})
        self.assertEqual(result['error_code'], 'invalid_format')
        mock_pricing.assert_not_called()

    # ── handle_stripe_price_id: pricing call raises → DOES_NOT_EXIST ──
    def test_handle_stripe_price_id_pricing_exception(self, mock_pricing):
        mock_pricing.side_effect = RuntimeError('boom')
        result = self.validator.handle_stripe_price_id({'stripe_price_id': QUARTERLY_PRICE_ID})
        self.assertEqual(result['error_code'], 'does_not_exist')


SUBSIDY_CLIENT_PATH = 'enterprise_access.apps.subsidy_access_policy.models.get_versioned_subsidy_client'
ACCOUNT_STANDING_PATH = 'enterprise_access.apps.customer_billing.api.is_account_in_good_standing'


def _mock_retrieve_subsidy(**kwargs):
    """ Patch the subsidy client's retrieve_subsidy, e.g. ``return_value=...`` or ``side_effect=...``. """
    return mock.patch(SUBSIDY_CLIENT_PATH, **{f'return_value.retrieve_subsidy.{k}': v for k, v in kwargs.items()})


def _http_error(status_code):
    response = requests.Response()
    response.status_code = status_code
    return requests.exceptions.HTTPError(f'{status_code} error', response=response)


@ddt.ddt
class EvaluateTopUpEligibilityTests(TestCase):
    """
    Tests for evaluate_top_up_eligibility.
    """

    def setUp(self):
        super().setUp()
        self.now = timezone.now()
        self.policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory()

    def _subsidy(self, days_left=100, subsidy_active=True):
        return {
            'is_active': subsidy_active,
            'expiration_datetime': (self.now + timedelta(days=days_left)).isoformat(),
        }

    def _evaluate(self, days_left=100, subsidy_active=True, standing=True):
        """ Evaluate the policy against a subsidy record with the given state. """
        subsidy = self._subsidy(days_left, subsidy_active)
        with mock.patch(ACCOUNT_STANDING_PATH, return_value=standing):
            return evaluate_top_up_eligibility(self.policy, subsidy, now=self.now)

    def _codes(self, result):
        return [reason['error_code'] for reason in result['reasons']]

    def test_eligible(self):
        result = self._evaluate()
        self.assertTrue(result['is_eligible'])
        self.assertEqual(result['reasons'], [])
        self.assertEqual(result['policy_uuid'], self.policy.uuid)
        self.assertIsNotNone(result['subsidy_expiration_datetime'])

    @ddt.data(
        (13, False),
        (14, True),
        (15, True),
    )
    @ddt.unpack
    def test_expiry_boundary(self, days_left, expected_eligible):
        result = self._evaluate(days_left=days_left)
        self.assertEqual(result['is_eligible'], expected_eligible)
        if not expected_eligible:
            self.assertEqual(self._codes(result), ['budget_expiring_soon'])

    def test_inactive_policy(self):
        self.policy.active = False
        self.assertEqual(self._codes(self._evaluate()), ['budget_not_active'])

    def test_retired_policy(self):
        self.policy.retired = True
        self.assertEqual(self._codes(self._evaluate()), ['budget_not_active'])

    def test_inactive_subsidy(self):
        self.assertEqual(self._codes(self._evaluate(subsidy_active=False)), ['budget_not_active'])

    def test_account_not_eligible(self):
        self.assertEqual(self._codes(self._evaluate(standing=False)), ['account_not_eligible'])

    def test_multiple_reasons(self):
        result = self._evaluate(days_left=5, subsidy_active=False, standing=False)
        self.assertEqual(
            self._codes(result),
            ['budget_not_active', 'budget_expiring_soon', 'account_not_eligible'],
        )

    def test_reasons_have_message(self):
        result = self._evaluate(standing=False)
        self.assertTrue(result['reasons'][0]['message'])

    def test_account_standing_placeholder_passes_every_account(self):
        self.assertTrue(is_account_in_good_standing(self.policy))

    @ddt.data(True, False)
    def test_is_top_up_enabled_reads_setting(self, setting_value):
        with override_settings(FEATURE_TOP_UP_LEARNER_CREDIT=setting_value):
            self.assertIs(is_top_up_enabled(), setting_value)

    def test_is_top_up_enabled_is_declared_off_in_base_settings(self):
        """ Off unless an environment turns it on: it is the only production safeguard until OQ-8 (chunk 19). """
        from enterprise_access.settings import base  # pylint: disable=import-outside-toplevel
        self.assertIs(base.FEATURE_TOP_UP_LEARNER_CREDIT, False)

    def test_account_standing_placeholder_does_not_block(self):
        result = evaluate_top_up_eligibility(self.policy, self._subsidy(), now=self.now)
        self.assertTrue(result['is_eligible'])


@ddt.ddt
class EvaluateTopUpEligibilityForPoliciesTests(TestCase):
    """
    Tests for evaluate_top_up_eligibility_for_policies. These mock the subsidy client, so the real
    ``SubsidyAccessPolicy.subsidy_record(raise_on_error=True)`` path runs.
    """

    def setUp(self):
        super().setUp()
        self.healthy_subsidy = {
            'is_active': True,
            'expiration_datetime': (timezone.now() + timedelta(days=100)).isoformat(),
        }

    def _retrieve_failing(self, failing_subsidy_uuids, status_code):
        """ A retrieve_subsidy side effect failing with ``status_code`` for the given subsidies only. """
        def retrieve_subsidy(subsidy_uuid):
            if subsidy_uuid in failing_subsidy_uuids:
                raise _http_error(status_code)
            return self.healthy_subsidy
        return retrieve_subsidy

    def _codes_by_policy(self, results):
        return {r['policy_uuid']: [reason['error_code'] for reason in r['reasons']] for r in results}

    @ddt.data(403, 404)
    def test_unreachable_subsidy_reports_budget_not_active(self, status_code):
        """ One soft-deleted (403) or missing (404) subsidy only affects its own budget. """
        healthy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory()
        dead = PerLearnerSpendCapLearnerCreditAccessPolicyFactory()
        with _mock_retrieve_subsidy(side_effect=self._retrieve_failing({dead.subsidy_uuid}, status_code)):
            results = evaluate_top_up_eligibility_for_policies([healthy, dead])
        self.assertEqual(self._codes_by_policy(results), {healthy.uuid: [], dead.uuid: ['budget_not_active']})

    @ddt.data(403, 404)
    def test_every_subsidy_unreachable_raises(self, status_code):
        """ A 403 from every subsidy looks the same as our own credentials failing: treat it as an outage. """
        policies = [PerLearnerSpendCapLearnerCreditAccessPolicyFactory() for _ in range(2)]
        failing = {policy.subsidy_uuid for policy in policies}
        with _mock_retrieve_subsidy(side_effect=self._retrieve_failing(failing, status_code)):
            with self.assertRaises(SubsidyAPIHTTPError) as ctx:
                evaluate_top_up_eligibility_for_policies(policies)
        self.assertIsInstance(ctx.exception, SubsidyUnreachableError)
        self.assertEqual(ctx.exception.error_response.status_code, status_code)

    def test_every_subsidy_unreachable_reported_when_not_raising(self):
        policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory()
        with _mock_retrieve_subsidy(side_effect=_http_error(403)):
            results = evaluate_top_up_eligibility_for_policies([policy], raise_if_all_unreachable=False)
        self.assertEqual(self._codes_by_policy(results), {policy.uuid: ['budget_not_active']})

    @ddt.data(500, 503)
    def test_server_error_is_raised_even_with_healthy_budgets(self, status_code):
        healthy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory()
        dead = PerLearnerSpendCapLearnerCreditAccessPolicyFactory()
        with _mock_retrieve_subsidy(side_effect=self._retrieve_failing({dead.subsidy_uuid}, status_code)):
            with self.assertRaises(SubsidyAPIHTTPError):
                evaluate_top_up_eligibility_for_policies([healthy, dead], raise_if_all_unreachable=False)

    @ddt.data(requests.exceptions.ConnectionError, requests.exceptions.Timeout)
    def test_connection_failure_propagates(self, exc_class):
        policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory()
        with _mock_retrieve_subsidy(side_effect=exc_class('boom')):
            with self.assertRaises(exc_class):
                evaluate_top_up_eligibility_for_policies([policy])

    @ddt.data(403, 404)
    def test_unreachable_subsidy_is_tried_three_times(self, status_code):
        """ A 403/404 is retried up to TOP_UP_MAX_SUBSIDY_FETCH_ATTEMPTS times before budget_not_active. """
        policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory()
        with _mock_retrieve_subsidy(side_effect=_http_error(status_code)) as mock_get_client:
            results = evaluate_top_up_eligibility_for_policies([policy], raise_if_all_unreachable=False)
        self.assertEqual(self._codes_by_policy(results), {policy.uuid: ['budget_not_active']})
        self.assertEqual(mock_get_client.return_value.retrieve_subsidy.call_count, TOP_UP_MAX_SUBSIDY_FETCH_ATTEMPTS)

    def test_unreachable_subsidy_that_recovers_on_retry_is_evaluated(self):
        policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory()
        with _mock_retrieve_subsidy(side_effect=[_http_error(403), _http_error(403), self.healthy_subsidy]):
            results = evaluate_top_up_eligibility_for_policies([policy])
        self.assertEqual(self._codes_by_policy(results), {policy.uuid: []})

    def test_server_error_is_not_retried(self):
        policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory()
        with _mock_retrieve_subsidy(side_effect=_http_error(500)) as mock_get_client:
            with self.assertRaises(SubsidyAPIHTTPError) as ctx:
                evaluate_top_up_eligibility_for_policies([policy])
        self.assertNotIsInstance(ctx.exception, SubsidyUnreachableError)
        mock_get_client.return_value.retrieve_subsidy.assert_called_once()

    def test_shared_subsidy_is_fetched_once(self):
        """ subsidy_record() is request-cached, so budgets sharing a subsidy cost one call. """
        first = PerLearnerSpendCapLearnerCreditAccessPolicyFactory()
        second = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            subsidy_uuid=first.subsidy_uuid, enterprise_customer_uuid=first.enterprise_customer_uuid,
        )
        with _mock_retrieve_subsidy(return_value=self.healthy_subsidy) as mock_get_client:
            results = evaluate_top_up_eligibility_for_policies([first, second])
        self.assertEqual(len(results), 2)
        mock_get_client.return_value.retrieve_subsidy.assert_called_once_with(subsidy_uuid=first.subsidy_uuid)

    def test_no_policies_returns_empty_list(self):
        self.assertEqual(evaluate_top_up_eligibility_for_policies([]), [])

    @ddt.data(
        {'active': True, 'retired': True},
        {'active': False, 'retired': False},
    )
    @ddt.unpack
    def test_disabled_budget_skips_subsidy_fetch(self, active, retired):
        """ A disabled budget is budget_not_active without a fetch, so its soft-deleted subsidy can't cause a 503. """
        policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(active=active, retired=retired)
        with _mock_retrieve_subsidy(side_effect=_http_error(403)) as mock_get_client:
            results = evaluate_top_up_eligibility_for_policies([policy])
        self.assertEqual(self._codes_by_policy(results), {policy.uuid: ['budget_not_active']})
        self.assertIsNone(results[0]['subsidy_expiration_datetime'])
        mock_get_client.return_value.retrieve_subsidy.assert_not_called()

    def test_disabled_budget_does_not_count_toward_all_unreachable(self):
        """ Only the fetched budgets decide "every subsidy unreachable". """
        retired = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(retired=True)
        dead = PerLearnerSpendCapLearnerCreditAccessPolicyFactory()
        with _mock_retrieve_subsidy(side_effect=_http_error(403)):
            with self.assertRaises(SubsidyAPIHTTPError):
                evaluate_top_up_eligibility_for_policies([retired, dead])


def _create_checkout_intent(enterprise_uuid, stripe_customer_id, created):
    """
    Create a CheckoutIntent for ``enterprise_uuid`` with the given ``created`` timestamp.
    """
    intent = CheckoutIntentFactory(enterprise_uuid=enterprise_uuid, stripe_customer_id=stripe_customer_id)
    # ``created`` is set on insert, so it can only be backdated afterwards.
    CheckoutIntent.objects.filter(pk=intent.pk).update(created=created)
    return intent


@ddt.ddt
class GetStripeCustomerIdForEnterpriseTests(TestCase):
    """
    Tests for get_stripe_customer_id_for_enterprise (ENT-12509). The billing management endpoint tests cover the
    mapping being read before the CheckoutIntent.
    """

    @ddt.data(None, '')
    @mock.patch('enterprise_access.apps.customer_billing.stripe_api.stripe.Customer.create')
    def test_falls_back_to_newest_checkout_intent_with_a_customer(self, missing_customer_id, mock_create):
        """
        Without a mapping, the newest CheckoutIntent with a Stripe customer is used: NULL and empty IDs are skipped,
        other enterprises' rows are ignored, and nothing is created or written.
        """
        enterprise_uuid, only_missing_ids = uuid.uuid4(), uuid.uuid4()
        now = timezone.now()
        _create_checkout_intent(enterprise_uuid, 'cus_oldest', now - timedelta(days=3))
        _create_checkout_intent(enterprise_uuid, 'cus_newer', now - timedelta(days=2))
        _create_checkout_intent(enterprise_uuid, missing_customer_id, now - timedelta(days=1))
        _create_checkout_intent(only_missing_ids, missing_customer_id, now)
        EnterpriseStripeCustomerFactory()  # Another enterprise's mapping.

        self.assertEqual(get_stripe_customer_id_for_enterprise(enterprise_uuid), 'cus_newer')
        self.assertIsNone(get_stripe_customer_id_for_enterprise(only_missing_ids))
        mock_create.assert_not_called()
        self.assertEqual(EnterpriseStripeCustomer.objects.count(), 1)


@ddt.ddt
@mock.patch('enterprise_access.apps.customer_billing.stripe_api.stripe.Customer.create')
class GetOrCreateEnterpriseStripeCustomerTests(TestCase):
    """
    Tests for get_or_create_enterprise_stripe_customer (ENT-12509).
    """

    def setUp(self):
        super().setUp()
        self.enterprise_uuid = uuid.uuid4()
        self.idempotency_key = f'enterprise-stripe-customer-{self.enterprise_uuid}'

    def test_creates_one_stripe_customer_then_returns_the_mapping(self, mock_create):
        """
        The first call creates a customer keyed by the enterprise, however its UUID is spelled. Later calls return the
        recorded mapping without calling Stripe, even once a CheckoutIntent has another customer.
        """
        mock_create.return_value = {'id': 'cus_new', 'object': 'customer'}

        with self.assertRaises(ValueError):
            get_or_create_enterprise_stripe_customer('not-a-uuid')
        first = get_or_create_enterprise_stripe_customer(self.enterprise_uuid.hex)
        _create_checkout_intent(self.enterprise_uuid, 'cus_intent', timezone.now())
        second = get_or_create_enterprise_stripe_customer(str(self.enterprise_uuid).upper())

        mock_create.assert_called_once_with(idempotency_key=self.idempotency_key)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            (second.enterprise_customer_uuid, second.stripe_customer_id, second.source),
            (self.enterprise_uuid, 'cus_new', EnterpriseStripeCustomer.SourceChoices.CREATED_FOR_TOP_UP),
        )
        self.assertEqual(list(second.history.values_list('history_type', flat=True)), ['+'])

    def test_records_checkout_intent_customer_without_calling_stripe(self, mock_create):
        """
        A self-service enterprise keeps the customer get_stripe_customer_id_for_enterprise() already returns.
        """
        now = timezone.now()
        _create_checkout_intent(self.enterprise_uuid, 'cus_intent', now - timedelta(days=1))
        _create_checkout_intent(self.enterprise_uuid, '', now)

        mapping = get_or_create_enterprise_stripe_customer(self.enterprise_uuid)

        mock_create.assert_not_called()
        self.assertEqual(
            (mapping.stripe_customer_id, mapping.source),
            ('cus_intent', EnterpriseStripeCustomer.SourceChoices.CHECKOUT_INTENT_BACKFILL),
        )

    @ddt.data(
        stripe.APIConnectionError('Stripe is unreachable'),
        stripe.IdempotencyError('Keys for idempotent requests can only be used with the same parameters'),
    )
    def test_stripe_failure_records_nothing_and_a_later_call_retries(self, stripe_error, mock_create):
        mock_create.side_effect = stripe_error

        with self.assertLogs(stripe_api.logger, level='ERROR') as logs, self.assertRaises(type(stripe_error)):
            get_or_create_enterprise_stripe_customer(self.enterprise_uuid)
        self.assertIn(str(self.enterprise_uuid), logs.output[0])
        self.assertFalse(EnterpriseStripeCustomer.objects.exists())

        mock_create.side_effect = None
        mock_create.return_value = {'id': 'cus_retry', 'object': 'customer'}
        self.assertEqual(get_or_create_enterprise_stripe_customer(self.enterprise_uuid).stripe_customer_id, 'cus_retry')
        self.assertEqual(mock_create.call_args_list, [mock.call(idempotency_key=self.idempotency_key)] * 2)

    @ddt.data(
        # Stripe replayed the same customer for the shared idempotency key.
        ('cus_winner', False),
        # Stripe no longer remembered the key (over 24 hours): this call's customer is left unrecorded and logged.
        ('cus_loser', True),
    )
    @ddt.unpack
    def test_concurrent_call_that_recorded_first_wins(self, created_customer_id, logs_error, mock_create):
        """
        Another request records the mapping while this one creates the customer. The recorded row wins, and the
        caller's own transaction stays usable after the lost insert.
        """
        def record_concurrently(**kwargs):
            EnterpriseStripeCustomerFactory(
                enterprise_customer_uuid=self.enterprise_uuid, stripe_customer_id='cus_winner',
            )
            return {'id': created_customer_id}
        mock_create.side_effect = record_concurrently

        with mock.patch.object(customer_billing_api.logger, 'error') as mock_log_error, transaction.atomic():
            mapping = get_or_create_enterprise_stripe_customer(self.enterprise_uuid)
            self.assertEqual(EnterpriseStripeCustomer.objects.count(), 1)

        self.assertEqual(mapping.stripe_customer_id, 'cus_winner')
        self.assertEqual(mock_log_error.called, logs_error)

    def test_integrity_error_without_a_mapping_is_raised(self, mock_create):
        mock_create.return_value = {'id': 'cus_new', 'object': 'customer'}

        with mock.patch.object(EnterpriseStripeCustomer.objects, 'create', side_effect=IntegrityError('other')):
            with self.assertRaises(IntegrityError):
                get_or_create_enterprise_stripe_customer(self.enterprise_uuid)
