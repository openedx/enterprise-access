"""
Tests for customer_billing tasks.
"""

from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.test import TestCase

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.models import CheckoutIntent
from enterprise_access.apps.customer_billing.tasks import (
    send_enterprise_provision_signup_confirmation_email,
    send_payment_receipt_email,
    send_trial_cancellation_email_task
)


class TestSendTrialCancellationEmailTask(TestCase):
    """Tests for send_trial_cancellation_email_task."""

    def setUp(self):
        """Set up test data."""
        self.user = UserFactory()
        self.checkout_intent = CheckoutIntent.create_intent(
            user=self.user,
            slug="test-enterprise",
            name="Test Enterprise",
            quantity=10,
        )
        self.checkout_intent.stripe_customer_id = "cus_test_123"
        self.checkout_intent.save()

        self.trial_end_timestamp = 1609459200  # Jan 1, 2021

    @mock.patch(
        "enterprise_access.apps.customer_billing.tasks.BrazeApiClient"
    )
    @mock.patch("enterprise_access.apps.customer_billing.tasks.LmsApiClient")
    def test_send_trial_cancellation_email_success(
        self, mock_lms_client, mock_braze_client
    ):
        """Test successful trial cancellation email send."""
        # Mock LMS response with admin users
        mock_lms_instance = mock_lms_client.return_value
        mock_lms_instance.get_enterprise_customer_data.return_value = {
            "admin_users": [
                {"email": "admin1@example.com", "lms_user_id": 123},
                {"email": "admin2@example.com", "lms_user_id": 456},
            ]
        }

        # Mock Braze client
        mock_braze_instance = mock_braze_client.return_value
        mock_braze_instance.create_braze_recipient.side_effect = [
            {"external_user_id": "123"},
            {"external_user_id": "456"},
        ]

        # Run the task
        send_trial_cancellation_email_task(
            checkout_intent_id=str(self.checkout_intent.id),
            trial_end_timestamp=self.trial_end_timestamp,
        )

        # Verify Braze campaign was sent
        mock_braze_instance.send_campaign_message.assert_called_once()
        call_args = mock_braze_instance.send_campaign_message.call_args

        # Check campaign ID
        self.assertEqual(
            call_args[0][0], settings.BRAZE_TRIAL_CANCELLATION_CAMPAIGN
        )

        # Check recipients
        recipients = call_args[1]["recipients"]
        self.assertEqual(len(recipients), 2)

        # Check trigger properties
        trigger_props = call_args[1]["trigger_properties"]
        self.assertIn("trial_end_date", trigger_props)
        self.assertIn("restart_subscription_url", trigger_props)

    @mock.patch(
        "enterprise_access.apps.customer_billing.tasks.BrazeApiClient"
    )
    @mock.patch("enterprise_access.apps.customer_billing.tasks.LmsApiClient")
    def test_send_trial_cancellation_email_braze_exception(
        self, mock_lms_client, mock_braze_client
    ):
        """Test that Braze API exception is raised and logged."""
        # Mock LMS response with admin users
        mock_lms_instance = mock_lms_client.return_value
        mock_lms_instance.get_enterprise_customer_data.return_value = {
            "admin_users": [
                {"email": "admin1@example.com", "lms_user_id": 123},
            ]
        }

        # Mock Braze client to raise exception when sending campaign
        mock_braze_instance = mock_braze_client.return_value
        mock_braze_instance.create_braze_recipient.return_value = {
            "external_user_id": "123"
        }
        mock_braze_instance.send_campaign_message.side_effect = Exception(
            "Braze API error"
        )

        # Run the task and expect exception to be raised
        with self.assertRaises(Exception) as context:
            send_trial_cancellation_email_task(
                checkout_intent_id=self.checkout_intent.id,
                trial_end_timestamp=self.trial_end_timestamp,
            )

        # Verify the exception message
        self.assertIn("Braze API error", str(context.exception))


class TestSendEnterpriseProvisionSignupConfirmationEmail(TestCase):
    """
    Tests for send_enterprise_provision_signup_confirmation_email task.
    """
    def setUp(self):
        super().setUp()
        self.test_data = {
            'subscription_start_date': '2025-01-01',
            'subscription_end_date': '2026-01-01',
            'number_of_licenses': 100,
            'organization_name': 'Test Corp',
            'enterprise_slug': 'test-corp',
        }
        self.mock_subscription = {
            'trial_start': '2025-01-01',
            'trial_end': '2025-02-01',
            'plan': {
                'amount': 10000  # $100.00 in cents
            }
        }
        self.mock_admin_users = [
            {
                'email': 'admin1@test.com',
                'lms_user_id': 1,
            },
            {
                'email': 'admin2@test.com',
                'lms_user_id': 2,
            }
        ]
        self.expected_braze_properties = {
            'subscription_start_date': '2025-01-01',
            'subscription_end_date': '2026-01-01',
            'number_of_licenses': 100,
            'organization': 'Test Corp',
            'enterprise_admin_portal_url': f'{settings.ENTERPRISE_ADMIN_PORTAL_URL}/test-corp',
            'trial_start_date': '2025-01-01',
            'trial_end_date': '2025-02-01',
            'plan_amount': Decimal('100'),
        }

    @mock.patch('enterprise_access.apps.customer_billing.tasks.validate_trial_subscription')
    def test_no_valid_trial_subscription(self, mock_validate_trial):
        """
        Test that task exits early when no valid trial subscription exists.
        """
        mock_validate_trial.return_value = (False, None)
        send_enterprise_provision_signup_confirmation_email(**self.test_data)
        mock_validate_trial.assert_called_once_with(self.test_data['enterprise_slug'])

    @mock.patch('enterprise_access.apps.customer_billing.tasks.BrazeApiClient')
    @mock.patch('enterprise_access.apps.customer_billing.tasks.LmsApiClient')
    @mock.patch('enterprise_access.apps.customer_billing.tasks.validate_trial_subscription')
    def test_no_admin_users(self, mock_validate_trial, mock_lms_client, mock_braze_client):
        """
        Test that task exits when no admin users are found.
        """
        mock_validate_trial.return_value = (True, self.mock_subscription)
        mock_lms_client.return_value.get_enterprise_customer_data.return_value = {
            'admin_users': []
        }
        send_enterprise_provision_signup_confirmation_email(**self.test_data)
        mock_validate_trial.assert_called_once_with(self.test_data['enterprise_slug'])
        mock_lms_client.return_value.get_enterprise_customer_data.assert_called_once_with(
            enterprise_customer_slug=self.test_data['enterprise_slug']
        )
        mock_braze_client.return_value.send_campaign_message.assert_not_called()

    @mock.patch('enterprise_access.apps.customer_billing.tasks.BrazeApiClient')
    @mock.patch('enterprise_access.apps.customer_billing.tasks.LmsApiClient')
    @mock.patch('enterprise_access.apps.customer_billing.tasks.validate_trial_subscription')
    def test_successful_email_send(self, mock_validate_trial, mock_lms_client, mock_braze_client):
        """
        Test successful email sending to multiple admin users.
        """
        mock_validate_trial.return_value = (True, self.mock_subscription)
        mock_lms_client.return_value.get_enterprise_customer_data.return_value = {
            'admin_users': self.mock_admin_users
        }

        mock_braze = mock_braze_client.return_value
        braze_recipients = []
        actual_calls = []

        def create_recipient_side_effect(user_email, lms_user_id):
            actual_calls.append(mock.call(user_email=user_email, lms_user_id=lms_user_id))
            recipient = {'external_id': f'braze_{lms_user_id}'}
            braze_recipients.append(recipient)
            return recipient
        mock_braze.create_braze_recipient.side_effect = create_recipient_side_effect
        send_enterprise_provision_signup_confirmation_email(**self.test_data)
        expected_calls = [
            mock.call(user_email=admin['email'], lms_user_id=admin.get('lms_user_id'))
            for admin in self.mock_admin_users
        ]
        mock_validate_trial.assert_called_once_with(self.test_data['enterprise_slug'])
        mock_lms_client.return_value.get_enterprise_customer_data.assert_called_once_with(
            enterprise_customer_slug=self.test_data['enterprise_slug']
        )
        mock_braze.create_braze_recipient.assert_has_calls(expected_calls, any_order=True)
        self.assertEqual(mock_braze.create_braze_recipient.call_count, len(self.mock_admin_users))
        mock_braze.send_campaign_message.assert_called_once_with(
            settings.BRAZE_ENTERPRISE_PROVISION_SIGNUP_CONFIRMATION_CAMPAIGN,
            recipients=braze_recipients,
            trigger_properties=self.expected_braze_properties,
        )

    @mock.patch('enterprise_access.apps.customer_billing.tasks.BrazeApiClient')
    @mock.patch('enterprise_access.apps.customer_billing.tasks.LmsApiClient')
    @mock.patch('enterprise_access.apps.customer_billing.tasks.validate_trial_subscription')
    def test_braze_campaign_send_failure(self, mock_validate_trial, mock_lms_client, mock_braze_client):
        """
        Test that Braze campaign sending failures raise exceptions.
        """
        mock_validate_trial.return_value = (True, self.mock_subscription)
        mock_lms_client.return_value.get_enterprise_customer_data.return_value = {
            'admin_users': self.mock_admin_users
        }
        mock_braze = mock_braze_client.return_value
        mock_braze.create_braze_recipient.side_effect = [
            {'external_id': 'braze1'},
            {'external_id': 'braze2'},
        ]
        mock_braze.send_campaign_message.side_effect = Exception("Braze Campaign Error")
        with self.assertRaises(Exception) as context:
            send_enterprise_provision_signup_confirmation_email(**self.test_data)

        self.assertEqual(str(context.exception), "Braze Campaign Error")


class TestSendPaymentReceiptEmail(TestCase):
    """
    Tests for send_payment_receipt_email task.
    """
    def setUp(self):
        super().setUp()
        self.mock_invoice_data = {
            'id': 'in_1SNvVOQ60jNALKNUMk8TZucs',
            'created': 1761829387,
            'payment_intent': {
                'payment_method': {
                    'card': {
                        'brand': 'visa',
                        'last4': '4242'
                    },
                    'billing_details': {
                        'name': 'Test User',
                        'address': {
                            'line1': '123 Test St',
                            'line2': 'Suite 100',
                            'city': 'Test City',
                            'state': 'TS',
                            'postal_code': '12345',
                            'country': 'US'
                        }
                    }
                }
            }
        }
        self.mock_subscription_data = {
            'quantity': 5,
            'plan': {
                'amount': 39600  # $396.00 in cents
            }
        }
        self.mock_admin_users = [
            {
                'email': 'admin1@test.com',
                'lms_user_id': 1,
            },
            {
                'email': 'admin2@test.com',
                'lms_user_id': 2,
            }
        ]
        self.enterprise_customer_name = 'Test Enterprise'
        self.enterprise_slug = 'test-enterprise'

    @mock.patch('enterprise_access.apps.customer_billing.tasks.format_datetime_obj')
    @mock.patch('enterprise_access.apps.customer_billing.tasks.BrazeApiClient')
    @mock.patch('enterprise_access.apps.customer_billing.tasks.LmsApiClient')
    def test_successful_payment_receipt_email(self, mock_lms_client, mock_braze_client, mock_format_datetime):
        """
        Test successful payment receipt email sending.
        """
        # Mock the date formatting function
        mock_format_datetime.return_value = '03 November 2025'

        mock_lms_client.return_value.get_enterprise_customer_data.return_value = {
            'admin_users': self.mock_admin_users
        }

        mock_braze = mock_braze_client.return_value
        braze_recipients = []
        actual_calls = []

        def create_recipient_side_effect(user_email, lms_user_id):
            actual_calls.append(mock.call(user_email=user_email, lms_user_id=lms_user_id))
            recipient = {'external_id': f'braze_{lms_user_id}'}
            braze_recipients.append(recipient)
            return recipient
        mock_braze.create_braze_recipient.side_effect = create_recipient_side_effect

        # Call the task
        send_payment_receipt_email(
            invoice_data=self.mock_invoice_data,
            subscription_data=self.mock_subscription_data,
            enterprise_customer_name=self.enterprise_customer_name,
            enterprise_slug=self.enterprise_slug,
        )

        # Verify LMS API was called to get admin users
        mock_lms_client.return_value.get_enterprise_customer_data.assert_called_once_with(
            enterprise_customer_slug=self.enterprise_slug
        )

        # Verify Braze recipients were created for each admin
        expected_recipient_calls = [
            mock.call(user_email=admin['email'], lms_user_id=admin.get('lms_user_id'))
            for admin in self.mock_admin_users
        ]
        mock_braze.create_braze_recipient.assert_has_calls(expected_recipient_calls, any_order=True)

        # Verify the campaign was sent with correct properties
        expected_properties = {
            'total_paid_amount': Decimal('1980.00'),  # $396.00 * 5 licenses = $1,980.00
            'date_paid': '03 November 2025',  # Based on mock timestamp
            'payment_method': 'visa - 4242',
            'license_count': 5,
            'price_per_license': Decimal('396.00'),
            'customer_name': 'Test User',
            'organization': 'Test Enterprise',
            'billing_address': '123 Test St\nSuite 100\nTest City, TS 12345\nUS',
            'enterprise_admin_portal_url': f'{settings.ENTERPRISE_ADMIN_PORTAL_URL}/test-enterprise',
            'receipt_number': 'in_1SNvVOQ60jNALKNUMk8TZucs',
        }

        mock_braze.send_campaign_message.assert_called_once_with(
            settings.BRAZE_ENTERPRISE_PROVISION_PAYMENT_RECEIPT_CAMPAIGN,
            recipients=braze_recipients,
            trigger_properties=expected_properties,
        )

    @mock.patch('enterprise_access.apps.customer_billing.tasks.BrazeApiClient')
    @mock.patch('enterprise_access.apps.customer_billing.tasks.LmsApiClient')
    def test_payment_receipt_no_admin_users(self, mock_lms_client, mock_braze_client):
        """
        Test that email is not sent when no admin users are found.
        """
        mock_lms_client.return_value.get_enterprise_customer_data.return_value = {
            'admin_users': []
        }

        send_payment_receipt_email(
            invoice_data=self.mock_invoice_data,
            subscription_data=self.mock_subscription_data,
            enterprise_customer_name=self.enterprise_customer_name,
            enterprise_slug=self.enterprise_slug,
        )

        # Verify LMS API was called but Braze API was not
        mock_lms_client.return_value.get_enterprise_customer_data.assert_called_once()
        mock_braze_client.return_value.send_campaign_message.assert_not_called()

    @mock.patch('enterprise_access.apps.customer_billing.tasks.BrazeApiClient')
    @mock.patch('enterprise_access.apps.customer_billing.tasks.LmsApiClient')
    def test_payment_receipt_braze_recipient_error(self, mock_lms_client, mock_braze_client):
        """
        Test handling of Braze recipient creation errors.
        """
        mock_lms_client.return_value.get_enterprise_customer_data.return_value = {
            'admin_users': self.mock_admin_users
        }

        # Make first recipient creation fail, second one succeed
        mock_braze = mock_braze_client.return_value
        mock_braze.create_braze_recipient.side_effect = [
            Exception("Failed to create recipient"),
            {'external_id': 'braze_2'}
        ]

        send_payment_receipt_email(
            invoice_data=self.mock_invoice_data,
            subscription_data=self.mock_subscription_data,
            enterprise_customer_name=self.enterprise_customer_name,
            enterprise_slug=self.enterprise_slug,
        )

        # Verify campaign was still sent for the successful recipient
        mock_braze.send_campaign_message.assert_called_once()
        actual_recipients = mock_braze.send_campaign_message.call_args[1]['recipients']
        self.assertEqual(len(actual_recipients), 1)
        self.assertEqual(actual_recipients[0]['external_id'], 'braze_2')
