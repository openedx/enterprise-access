"""
Unit tests for the ``enterprise_access.apps.customer_billing.api`` module.
"""
from datetime import timedelta
from unittest import mock

import ddt
import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing import api as customer_billing_api
from enterprise_access.apps.customer_billing.constants import CHECKOUT_SESSION_ERROR_CODES
from enterprise_access.apps.customer_billing.models import EnterpriseSlugReservation

User = get_user_model()


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
    ENTERPRISE_SLUG_RESERVATION_MINUTES=30,
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
        customer_billing_api._get_lms_user_id.cache_clear()  # pylint: disable=protected-access
        # Clean up any reservations created during tests
        EnterpriseSlugReservation.objects.all().delete()

    @mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True)
    @mock.patch.object(customer_billing_api, 'stripe', autospec=True)
    def test_create_free_trial_checkout_session_success(self, mock_stripe, mock_lms_client_class):
        """
        Happy path for ``create_free_trial_checkout_session()`` with slug reservation.
        """
        # Setup mocks library methods.
        mock_lms_client = mock_lms_client_class.return_value
        mock_lms_client.get_lms_user_account.return_value = [{'id': self.user.lms_user_id}]
        mock_lms_client.get_enterprise_customer_data.side_effect = raise_404_error
        mock_stripe.checkout.Session.create.return_value = {'id': 'test-stripe-checkout-session'}
        mock_stripe.Customer.search.return_value.data = []

        # Actually call the API under test.
        result = customer_billing_api.create_free_trial_checkout_session(
            user=self.user,
            admin_email=self.user.email,
            enterprise_slug='my-sluggy',
            quantity=20,
            stripe_price_id='price_ABC',
        )

        # Assert API response.
        self.assertEqual(
            result,
            {'id': 'test-stripe-checkout-session'},
        )

        # Assert that a reservation was created
        reservation = EnterpriseSlugReservation.objects.get(user=self.user)
        self.assertEqual(reservation.slug, 'my-sluggy')
        self.assertEqual(reservation.stripe_checkout_session_id, 'test-stripe-checkout-session')
        self.assertFalse(reservation.is_expired())

        # Assert library methods were called correctly.
        mock_lms_client.get_lms_user_account.assert_called_once_with(
            email=self.user.email,
        )
        mock_lms_client.get_enterprise_customer_data.assert_called_once_with(
            enterprise_customer_slug='my-sluggy',
        )

        # Check that reservation ID is in Stripe metadata
        call_args = mock_stripe.checkout.Session.create.call_args
        metadata = call_args[1]['subscription_data']['metadata']
        self.assertEqual(metadata['enterprise_customer_slug'], 'my-sluggy')
        self.assertEqual(metadata['lms_user_id'], str(self.user.lms_user_id))

    @mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True)
    @mock.patch.object(customer_billing_api, 'stripe', autospec=True)
    def test_create_free_trial_checkout_session_success_without_user(self, mock_stripe, mock_lms_client_class):
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
                quantity=20,
                stripe_price_id='price_ABC',
            )
            # Should get slug reserved error
            validation_errors = cm.exception.validation_errors_by_field
            self.assertIn('user', validation_errors)

    @mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True)
    @mock.patch.object(customer_billing_api, 'stripe', autospec=True)
    def test_create_free_trial_checkout_session_replaces_user_reservation(self, mock_stripe, mock_lms_client_class):
        """
        Test that creating a new checkout session replaces the user's existing reservation.
        """
        # Create an existing reservation for the user
        EnterpriseSlugReservation.reserve_slug(self.user, 'old-slug')

        # Setup mocks
        mock_lms_client = mock_lms_client_class.return_value
        mock_lms_client.get_lms_user_account.return_value = [{'id': 9876}]
        mock_lms_client.get_enterprise_customer_data.side_effect = raise_404_error
        mock_stripe.checkout.Session.create.return_value = {'id': 'new-stripe-session'}
        mock_stripe.Customer.search.return_value.data = []

        # Create new checkout session with different slug
        result = customer_billing_api.create_free_trial_checkout_session(
            user=self.user,
            admin_email='test@example.com',
            enterprise_slug='new-sluggy',
            quantity=20,
            stripe_price_id='price_ABC',
        )

        # Should succeed and replace the old reservation
        self.assertEqual(result, {'id': 'new-stripe-session'})

        # Should only have one reservation for this user with the new slug
        reservation = EnterpriseSlugReservation.objects.get(user=self.user)
        self.assertEqual(reservation.slug, 'new-sluggy')
        self.assertEqual(reservation.stripe_checkout_session_id, 'new-stripe-session')

    def test_slug_reservation_conflict(self):
        """
        Test that slug reservation prevents conflicts between different users.
        """
        # User 1 reserves a slug
        EnterpriseSlugReservation.reserve_slug(self.other_user, 'conflicting-slug')

        # Setup mocks
        with mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True) as mock_lms_client_class:
            with mock.patch.object(customer_billing_api, 'stripe', autospec=True):
                mock_lms_client = mock_lms_client_class.return_value
                mock_lms_client.get_lms_user_account.return_value = [{'id': 9876}]
                mock_lms_client.get_enterprise_customer_data.side_effect = raise_404_error

                # User 2 tries to use the same slug - should fail
                with self.assertRaises(customer_billing_api.CreateCheckoutSessionValidationError) as cm:
                    customer_billing_api.create_free_trial_checkout_session(
                        user=self.user,
                        admin_email='test@example.com',
                        enterprise_slug='conflicting-slug',
                        quantity=20,
                        stripe_price_id='price_ABC',
                    )

                # Should get slug reserved error
                validation_errors = cm.exception.validation_errors_by_field
                self.assertIn('enterprise_slug', validation_errors)
                self.assertEqual(validation_errors['enterprise_slug']['error_code'], 'slug_reserved')

    def test_expired_reservation_allows_reuse(self):
        """
        Test that expired reservations don't block new reservations.
        """
        # Create an expired reservation
        expired_time = timezone.now() - timedelta(minutes=5)
        EnterpriseSlugReservation.objects.create(
            user=self.other_user,
            slug='expired-slug',
            expires_at=expired_time
        )

        # Setup mocks
        with mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True) as mock_lms_client_class:
            with mock.patch.object(customer_billing_api, 'stripe', autospec=True) as mock_stripe:
                mock_lms_client = mock_lms_client_class.return_value
                mock_lms_client.get_lms_user_account.return_value = [{'id': 9876}]
                mock_lms_client.get_enterprise_customer_data.side_effect = raise_404_error
                mock_stripe.checkout.Session.create.return_value = {'id': 'test-session'}
                mock_stripe.Customer.search.return_value.data = []

                # Should be able to reserve the expired slug
                result = customer_billing_api.create_free_trial_checkout_session(
                    user=self.user,
                    admin_email='test@example.com',
                    enterprise_slug='expired-slug',
                    quantity=20,
                    stripe_price_id='price_ABC',
                )

                # Should succeed
                self.assertEqual(result, {'id': 'test-session'})

                # Should have a new active reservation
                reservation = EnterpriseSlugReservation.objects.get(user=self.user)
                self.assertEqual(reservation.slug, 'expired-slug')
                self.assertFalse(reservation.is_expired())

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
                user=self.user,  # Include user in error cases too
                admin_email='test@example.com',
                enterprise_slug=request_enterprise_slug,
                quantity=request_quantity,
                stripe_price_id=request_stripe_price_id,
            )

        actual_validation_errors = cm.exception.validation_errors_by_field
        assert actual_validation_errors == expected_validation_errors


@ddt.ddt
class TestEnterpriseSlugReservationIntegration(TestCase):
    """
    Tests for the integration between slug reservations and checkout validation.
    """

    def setUp(self):
        self.user1 = UserFactory()
        self.user2 = UserFactory()

    def tearDown(self):
        customer_billing_api._get_lms_user_id.cache_clear()  # pylint: disable=protected-access
        EnterpriseSlugReservation.objects.all().delete()

    def test_validate_free_trial_checkout_session_with_reserved_slug(self):
        """
        Test validation function correctly identifies reserved slugs.
        """
        # User1 reserves a slug
        EnterpriseSlugReservation.reserve_slug(self.user1, 'reserved-slug')

        with mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True) as mock_lms_client_class:
            mock_lms_client = mock_lms_client_class.return_value
            mock_lms_client.get_lms_user_account.return_value = [{'id': 9876}]
            mock_lms_client.get_enterprise_customer_data.side_effect = raise_404_error

            # User2 tries to validate the same slug
            validation_errors = customer_billing_api.validate_free_trial_checkout_session(
                user=self.user2,
                admin_email='test@example.com',
                enterprise_slug='reserved-slug',
                quantity=10,
                stripe_price_id='price_ABC',
            )
            # Should get slug reserved error
            self.assertIn('enterprise_slug', validation_errors)
            self.assertEqual(validation_errors['enterprise_slug']['error_code'], 'slug_reserved')

    @override_settings(SSP_PRODUCTS={
        'yearly_license_plan': {
            'stripe_price_id': 'price_ABC',
            'quantity_range': (5, 30),
        },
    })
    def test_validate_free_trial_checkout_session_user_can_reuse_own_reservation(self):
        """
        Test that users can validate their own reserved slugs.
        """
        # User reserves a slug
        EnterpriseSlugReservation.reserve_slug(self.user1, 'my-reserved-slug')

        with mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True) as mock_lms_client_class:
            mock_lms_client = mock_lms_client_class.return_value
            mock_lms_client.get_lms_user_account.return_value = [{'id': 9876}]
            mock_lms_client.get_enterprise_customer_data.side_effect = raise_404_error

            # Same user validates their reserved slug
            validation_errors = customer_billing_api.validate_free_trial_checkout_session(
                user=self.user1,
                admin_email='test@example.com',
                enterprise_slug='my-reserved-slug',
                quantity=10,
                stripe_price_id='price_ABC',
            )

            # Should pass validation (no errors)
            self.assertEqual(validation_errors, {})

    @override_settings(SSP_PRODUCTS={
        'yearly_license_plan': {
            'stripe_price_id': 'price_ABC',
            'quantity_range': (5, 30),
        },
    })
    def test_validate_free_trial_checkout_session_with_null_user(self):
        """
        Test validation executes with a null user parameter.
        """
        with mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True) as mock_lms_client_class:
            mock_lms_client = mock_lms_client_class.return_value
            mock_lms_client.get_lms_user_account.return_value = [{'id': 9876}]
            mock_lms_client.get_enterprise_customer_data.side_effect = raise_404_error

            # Validate without user parameter
            validation_errors = customer_billing_api.validate_free_trial_checkout_session(
                user=None,
                admin_email='test@example.com',
                enterprise_slug='any-slug',
                quantity=10,
                stripe_price_id='price_ABC',
            )

            # Should get user is null error
            self.assertEqual(
                CHECKOUT_SESSION_ERROR_CODES['user']['IS_NULL'][0],
                validation_errors['user']['error_code'],
            )

    @override_settings(SSP_PRODUCTS={
        'yearly_license_plan': {
            'stripe_price_id': 'price_ABC',
            'quantity_range': (5, 30),
        },
    })
    def test_checkout_session_cleanup_on_validation_failure(self):
        """
        Test that reservation isn't created if validation fails after slug check.
        """
        with mock.patch.object(customer_billing_api, 'LmsApiClient', autospec=True) as mock_lms_client_class:
            mock_lms_client = mock_lms_client_class.return_value
            # Set up email validation to pass but quantity validation to fail
            mock_lms_client.get_lms_user_account.return_value = [{'id': 9876}]
            mock_lms_client.get_enterprise_customer_data.side_effect = raise_404_error

            # Try to create checkout session with invalid quantity
            with self.assertRaises(customer_billing_api.CreateCheckoutSessionValidationError):
                customer_billing_api.create_free_trial_checkout_session(
                    user=self.user1,
                    admin_email='test@example.com',
                    enterprise_slug='test-slug',
                    quantity=100,  # Out of range
                    stripe_price_id='price_ABC',
                )

            # No reservation should be created since validation failed
            self.assertEqual(EnterpriseSlugReservation.objects.count(), 0)
