"""
Tests for customer_billing tasks.
"""
from datetime import datetime
from unittest import mock

import stripe
from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.constants import BRAZE_TIMESTAMP_FORMAT
from enterprise_access.apps.customer_billing.models import CheckoutIntent, StripeEventData, StripeEventSummary
from enterprise_access.apps.customer_billing.tasks import (
    send_enterprise_provision_signup_confirmation_email,
    send_payment_receipt_email,
    send_trial_cancellation_email_task,
    send_trial_end_and_subscription_started_email_task,
    send_trial_ending_reminder_email_task
)
from enterprise_access.utils import format_datetime_obj


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

        self.trial_end_datetime = datetime(2021, 1, 1)
        self.trial_end_timestamp = int(self.trial_end_datetime.timestamp())

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
        self.trial_start = timezone.make_aware(datetime(2025, 1, 1))
        self.trial_end = timezone.make_aware(datetime(2026, 1, 1))
        self.test_data = {
            'subscription_start_date': self.trial_start,
            'subscription_end_date': self.trial_end,
            'number_of_licenses': 100,
            'organization_name': 'Test Corp',
            'enterprise_slug': 'test-corp',
        }
        self.mock_subscription = {
            'trial_start': int(self.trial_start.timestamp()),
            'trial_end': int(self.trial_end.timestamp()),
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
            'subscription_start_date': 'Jan 01, 2025',
            'subscription_end_date': 'Jan 01, 2026',
            'number_of_licenses': 100,
            'organization': 'Test Corp',
            'enterprise_admin_portal_url': f'{settings.ENTERPRISE_ADMIN_PORTAL_URL}/test-corp',
            'trial_start_datetime': format_datetime_obj(self.trial_start, output_pattern=BRAZE_TIMESTAMP_FORMAT),
            'trial_end_datetime': format_datetime_obj(self.trial_end, output_pattern=BRAZE_TIMESTAMP_FORMAT),
            'plan_amount': 100.00,
            'total_amount': 100.00 * 100,
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

        with self.assertRaisesRegex(Exception, 'No admin users'):
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
        self.user = UserFactory(email='hello@world.com')
        self.checkout_intent = CheckoutIntent.create_intent(
            user=self.user,
            slug="test-enterprise",
            name="Test Enterprise",
            quantity=5,
        )
        self.checkout_intent.stripe_customer_id = "cus_test_123"
        self.checkout_intent.save()

        self.invoice_id = 'in_1SNvVOQ60jNALKNUMk8TZucs'
        self.mock_invoice_data = {
            'id': self.invoice_id,
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

        # Create StripeEventData and StripeEventSummary for the invoice
        self.stripe_event_data = StripeEventData.objects.create(
            event_id="evt_test_payment_receipt",
            event_type="invoice.paid",
            checkout_intent=self.checkout_intent,
        )
        self.invoice_summary = StripeEventSummary.objects.create(
            stripe_event_data=self.stripe_event_data,
            event_type="invoice.paid",
            stripe_invoice_id=self.invoice_id,
            invoice_amount_paid=198000,  # $1,980.00 (5 licenses * $396.00)
            invoice_unit_amount=39600,   # $396.00 per license
            invoice_quantity=5,
            invoice_currency='usd',
        )

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
            invoice_id=self.invoice_id,
            invoice_data=self.mock_invoice_data,
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
        # Note: total_paid_amount comes from invoice_summary.invoice_amount_paid (198000 cents = $1980.00)
        # price_per_license comes from invoice_summary.invoice_unit_amount (39600 cents = $396.00)
        expected_properties = {
            'total_paid_amount': 1980.0,  # $396.00 * 5 licenses = $1,980.00
            'date_paid': '03 November 2025',  # Based on mock timestamp
            'payment_method': 'visa - 4242',
            'license_count': 5,
            'price_per_license': 396.0,
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
        Test that exception is not raised when no admin users are found, and instead the email is
        sent to the email address of the CheckoutIntent user.
        """
        mock_lms_client.return_value.get_enterprise_customer_data.return_value = {
            'admin_users': []
        }
        mock_braze = mock_braze_client.return_value

        send_payment_receipt_email(
            invoice_id=self.invoice_id,
            invoice_data=self.mock_invoice_data,
            enterprise_customer_name=self.enterprise_customer_name,
            enterprise_slug=self.enterprise_slug,
        )

        mock_braze.create_braze_recipient.assert_called_once_with(
            user_email=self.checkout_intent.user.email,
            lms_user_id=self.checkout_intent.user.lms_user_id,
        )
        mock_lms_client.return_value.get_enterprise_customer_data.assert_called_once()
        mock_braze_client.return_value.send_campaign_message.assert_called_once_with(
            settings.BRAZE_ENTERPRISE_PROVISION_PAYMENT_RECEIPT_CAMPAIGN,
            recipients=[mock_braze.create_braze_recipient.return_value],
            trigger_properties=mock.ANY,
        )

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
            invoice_id=self.invoice_id,
            invoice_data=self.mock_invoice_data,
            enterprise_customer_name=self.enterprise_customer_name,
            enterprise_slug=self.enterprise_slug,
        )

        # Verify campaign was still sent for the successful recipient
        mock_braze.send_campaign_message.assert_called_once()
        actual_recipients = mock_braze.send_campaign_message.call_args[1]['recipients']
        self.assertEqual(len(actual_recipients), 1)
        self.assertEqual(actual_recipients[0]['external_id'], 'braze_2')

    @mock.patch('enterprise_access.apps.customer_billing.tasks.BrazeApiClient')
    @mock.patch('enterprise_access.apps.customer_billing.tasks.LmsApiClient')
    def test_payment_receipt_no_invoice_summary(self, mock_lms_client, mock_braze_client):
        """
        Test that email is not sent when no invoice summary is found.
        """
        mock_lms_client.return_value.get_enterprise_customer_data.return_value = {
            'admin_users': self.mock_admin_users
        }

        # Use a different invoice ID that doesn't have a summary
        send_payment_receipt_email(
            invoice_id='in_nonexistent_invoice',
            invoice_data=self.mock_invoice_data,
            enterprise_customer_name=self.enterprise_customer_name,
            enterprise_slug=self.enterprise_slug,
        )

        # Verify Braze campaign was not sent
        mock_braze_client.return_value.send_campaign_message.assert_not_called()


class TestSendTrialEndingReminderEmailTask(TestCase):
    """Tests for send_trial_ending_reminder_email_task."""

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

        self.mock_subscription = mock.Mock(
            id="sub_test_123",
            default_payment_method="pm_test_456",
            latest_invoice="in_test_789",
        )
        self.mock_subscription.__getitem__ = mock.Mock(return_value=mock.Mock(
            data=[
                mock.Mock(
                    current_period_end=int(datetime(2022, 1, 1).timestamp()),
                    quantity=10,
                )
            ]
        ))

    @mock.patch("enterprise_access.apps.customer_billing.tasks.stripe.PaymentMethod.retrieve")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.get_stripe_trialing_subscription")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.BrazeApiClient")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.LmsApiClient")
    def test_send_trial_ending_reminder_email_success(
        self, mock_lms_client, mock_braze_client, mock_get_subscription, mock_payment_method
    ):
        """Test successful trial ending reminder email send."""
        mock_lms_instance = mock_lms_client.return_value
        mock_lms_instance.get_enterprise_customer_data.return_value = {
            "admin_users": [
                {"email": "admin1@example.com", "lms_user_id": 123},
                {"email": "admin2@example.com", "lms_user_id": 456},
            ]
        }

        mock_get_subscription.return_value = self.mock_subscription

        mock_payment_method.return_value = mock.Mock(
            type="card",
            card=mock.Mock(brand="visa", last4="4242"),
        )

        stripe_event_data = StripeEventData.objects.create(
            event_id="evt_test_123",
            event_type="customer.subscription.created",
            checkout_intent=self.checkout_intent,
        )
        StripeEventSummary.objects.create(
            stripe_event_data=stripe_event_data,
            event_type="customer.subscription.created",
            stripe_subscription_id="sub_test_123",
            upcoming_invoice_amount_due=633600,
        )

        mock_braze_instance = mock_braze_client.return_value
        mock_braze_instance.create_braze_recipient.side_effect = [
            {"external_user_id": "123"},
            {"external_user_id": "456"},
        ]

        send_trial_ending_reminder_email_task(
            checkout_intent_id=self.checkout_intent.id,
        )

        mock_braze_instance.send_campaign_message.assert_called_once()
        call_args = mock_braze_instance.send_campaign_message.call_args

        self.assertEqual(
            call_args[0][0], settings.BRAZE_ENTERPRISE_PROVISION_TRIAL_ENDING_SOON_CAMPAIGN
        )

        recipients = call_args[1]["recipients"]
        self.assertEqual(len(recipients), 2)

        trigger_props = call_args[1]["trigger_properties"]
        self.assertIn("renewal_datetime", trigger_props)
        self.assertEqual(
            trigger_props["renewal_datetime"],
            format_datetime_obj(datetime(2022, 1, 1), output_pattern=BRAZE_TIMESTAMP_FORMAT),
        )
        self.assertIn("subscription_management_url", trigger_props)
        self.assertEqual(trigger_props["license_count"], 10)
        self.assertEqual(trigger_props["payment_method"], "Visa ending in 4242")
        self.assertEqual(trigger_props["total_paid_amount"], "$6,336.00 USD")

    @mock.patch("enterprise_access.apps.customer_billing.tasks.get_stripe_trialing_subscription")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.LmsApiClient")
    def test_checkout_intent_not_found(self, mock_lms_client, mock_get_subscription):
        """Test handling of non-existent checkout intent."""
        send_trial_ending_reminder_email_task(checkout_intent_id=99999)

        mock_lms_client.return_value.get_enterprise_customer_data.assert_not_called()
        mock_get_subscription.assert_not_called()

    @mock.patch("enterprise_access.apps.customer_billing.tasks.get_stripe_trialing_subscription")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.BrazeApiClient")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.LmsApiClient")
    def test_no_admin_users_found(
        self, mock_lms_client, mock_braze_client, mock_get_subscription
    ):
        """Test when no admin users are found."""
        mock_lms_instance = mock_lms_client.return_value
        mock_lms_instance.get_enterprise_customer_data.return_value = {
            "admin_users": []
        }

        with self.assertRaisesRegex(Exception, 'No admin users'):
            send_trial_ending_reminder_email_task(
                checkout_intent_id=self.checkout_intent.id,
            )

        mock_get_subscription.assert_not_called()
        mock_braze_client.return_value.send_campaign_message.assert_not_called()

    @mock.patch("enterprise_access.apps.customer_billing.tasks.get_stripe_trialing_subscription")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.BrazeApiClient")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.LmsApiClient")
    def test_no_stripe_customer_id(
        self, mock_lms_client, mock_braze_client, mock_get_subscription
    ):
        """Test when checkout intent has no Stripe customer ID."""
        self.checkout_intent.stripe_customer_id = None
        self.checkout_intent.save()

        mock_lms_instance = mock_lms_client.return_value
        mock_lms_instance.get_enterprise_customer_data.return_value = {
            "admin_users": [{"email": "admin@example.com", "lms_user_id": 123}]
        }

        send_trial_ending_reminder_email_task(
            checkout_intent_id=self.checkout_intent.id,
        )

        mock_get_subscription.assert_not_called()
        mock_braze_client.return_value.send_campaign_message.assert_not_called()

    @mock.patch("enterprise_access.apps.customer_billing.tasks.get_stripe_trialing_subscription")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.BrazeApiClient")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.LmsApiClient")
    def test_no_trialing_subscription_found(
        self, mock_lms_client, mock_braze_client, mock_get_subscription
    ):
        """Test when no trialing subscription is found."""
        mock_lms_instance = mock_lms_client.return_value
        mock_lms_instance.get_enterprise_customer_data.return_value = {
            "admin_users": [{"email": "admin@example.com", "lms_user_id": 123}]
        }

        mock_get_subscription.return_value = None

        send_trial_ending_reminder_email_task(
            checkout_intent_id=self.checkout_intent.id,
        )

        mock_braze_client.return_value.send_campaign_message.assert_not_called()

    @mock.patch("enterprise_access.apps.customer_billing.tasks.get_stripe_trialing_subscription")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.BrazeApiClient")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.LmsApiClient")
    def test_subscription_has_no_items(
        self, mock_lms_client, mock_braze_client, mock_get_subscription
    ):
        """Test when subscription has no items."""
        mock_lms_instance = mock_lms_client.return_value
        mock_lms_instance.get_enterprise_customer_data.return_value = {
            "admin_users": [{"email": "admin@example.com", "lms_user_id": 123}]
        }

        mock_subscription = mock.Mock(id="sub_test_123")
        mock_subscription.__getitem__ = mock.Mock(return_value=mock.Mock(data=[]))
        mock_get_subscription.return_value = mock_subscription

        send_trial_ending_reminder_email_task(
            checkout_intent_id=self.checkout_intent.id,
        )

        mock_braze_client.return_value.send_campaign_message.assert_not_called()

    @mock.patch("enterprise_access.apps.customer_billing.tasks.stripe.PaymentMethod.retrieve")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.get_stripe_trialing_subscription")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.BrazeApiClient")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.LmsApiClient")
    def test_no_payment_method(
        self, mock_lms_client, mock_braze_client, mock_get_subscription, mock_payment_method
    ):
        """Test when subscription has no payment method."""
        mock_lms_instance = mock_lms_client.return_value
        mock_lms_instance.get_enterprise_customer_data.return_value = {
            "admin_users": [{"email": "admin@example.com", "lms_user_id": 123}]
        }

        mock_subscription = mock.Mock(
            id="sub_test_123",
            default_payment_method=None,
            latest_invoice=None,
        )
        mock_subscription.__getitem__ = mock.Mock(return_value=mock.Mock(
            data=[
                mock.Mock(
                    current_period_end=1640995200,
                    quantity=10,
                )
            ]
        ))
        mock_get_subscription.return_value = mock_subscription

        mock_braze_instance = mock_braze_client.return_value
        mock_braze_instance.create_braze_recipient.return_value = {
            "external_user_id": "123"
        }

        send_trial_ending_reminder_email_task(
            checkout_intent_id=self.checkout_intent.id,
        )

        mock_payment_method.assert_not_called()
        mock_braze_instance.send_campaign_message.assert_called_once()
        call_args = mock_braze_instance.send_campaign_message.call_args
        trigger_props = call_args[1]["trigger_properties"]
        self.assertEqual(trigger_props["payment_method"], "")
        self.assertEqual(trigger_props["total_paid_amount"], "$0.00 USD")

    @mock.patch("enterprise_access.apps.customer_billing.tasks.stripe.PaymentMethod.retrieve")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.get_stripe_trialing_subscription")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.BrazeApiClient")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.LmsApiClient")
    def test_stripe_error_during_subscription_retrieval(
        self, mock_lms_client, mock_braze_client, mock_get_subscription, _mock_payment_method
    ):
        """Test handling of Stripe API errors."""
        mock_lms_instance = mock_lms_client.return_value
        mock_lms_instance.get_enterprise_customer_data.return_value = {
            "admin_users": [{"email": "admin@example.com", "lms_user_id": 123}]
        }

        mock_get_subscription.side_effect = stripe.StripeError("API error")

        send_trial_ending_reminder_email_task(
            checkout_intent_id=self.checkout_intent.id,
        )

        mock_braze_client.return_value.send_campaign_message.assert_not_called()

    @mock.patch("enterprise_access.apps.customer_billing.tasks.stripe.PaymentMethod.retrieve")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.get_stripe_trialing_subscription")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.BrazeApiClient")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.LmsApiClient")
    def test_braze_exception(
        self, mock_lms_client, mock_braze_client, mock_get_subscription, mock_payment_method
    ):
        """Test that Braze API exception is raised and logged."""
        mock_lms_instance = mock_lms_client.return_value
        mock_lms_instance.get_enterprise_customer_data.return_value = {
            "admin_users": [{"email": "admin@example.com", "lms_user_id": 123}]
        }

        mock_get_subscription.return_value = self.mock_subscription

        mock_payment_method.return_value = mock.Mock(
            type="card",
            card=mock.Mock(brand="mastercard", last4="5555"),
        )

        stripe_event_data = StripeEventData.objects.create(
            event_id="evt_test_456",
            event_type="invoice.paid",
            checkout_intent=self.checkout_intent,
        )
        StripeEventSummary.objects.create(
            stripe_event_data=stripe_event_data,
            event_type="invoice.paid",
            stripe_invoice_id="in_test_789",
            invoice_amount_paid=100000,
        )

        mock_braze_instance = mock_braze_client.return_value
        mock_braze_instance.create_braze_recipient.return_value = {
            "external_user_id": "123"
        }
        mock_braze_instance.send_campaign_message.side_effect = Exception(
            "Braze API error"
        )

        with self.assertRaises(Exception) as context:
            send_trial_ending_reminder_email_task(
                checkout_intent_id=self.checkout_intent.id,
            )

        self.assertIn("Braze API error", str(context.exception))

    @mock.patch("enterprise_access.apps.customer_billing.tasks.stripe.PaymentMethod.retrieve")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.get_stripe_trialing_subscription")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.BrazeApiClient")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.LmsApiClient")
    def test_no_invoice_summary_found(
        self, mock_lms_client, mock_braze_client, mock_get_subscription, mock_payment_method
    ):
        """Test when no invoice summary is found in database."""
        mock_lms_instance = mock_lms_client.return_value
        mock_lms_instance.get_enterprise_customer_data.return_value = {
            "admin_users": [{"email": "admin@example.com", "lms_user_id": 123}]
        }

        mock_get_subscription.return_value = self.mock_subscription

        mock_payment_method.return_value = mock.Mock(
            type="card",
            card=mock.Mock(brand="amex", last4="0005"),
        )

        mock_braze_instance = mock_braze_client.return_value
        mock_braze_instance.create_braze_recipient.return_value = {
            "external_user_id": "123"
        }

        send_trial_ending_reminder_email_task(
            checkout_intent_id=self.checkout_intent.id,
        )

        mock_braze_instance.send_campaign_message.assert_called_once()
        call_args = mock_braze_instance.send_campaign_message.call_args
        trigger_props = call_args[1]["trigger_properties"]
        self.assertEqual(trigger_props["total_paid_amount"], "$0.00 USD")


class TestSendTrialEndAndSubscriptionStartedEmailTask(TestCase):
    """
    Tests for send_trial_end_and_subscription_started_email_task.
    """

    @mock.patch("enterprise_access.apps.customer_billing.tasks.get_stripe_subscription")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.CheckoutIntent")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.BrazeApiClient")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.LmsApiClient")
    def test_success_sends_to_all_admins(
        self,
        mock_lms_client,
        mock_braze_client,
        mock_checkout_intent,
        mock_get_stripe_subscription
    ):
        subscription = {
            'id': 'sub_123',
            'quantity': 5,
            'plan': {'amount': 10000},
            'items': {
                "data": [
                    {
                        'current_period_start': 1762273481,  # 05 Dec 2025
                        'current_period_end': 1793809481,  # 05 Dec 2026
                    }
                ]
            },
            'latest_invoice': {'hosted_invoice_url': 'https://invoice.url'},
        }
        checkout_intent_obj = mock.Mock()
        checkout_intent_obj.enterprise_name = 'Test Org'
        checkout_intent_obj.enterprise_slug = 'test-org'
        mock_checkout_intent.objects.get.return_value = checkout_intent_obj
        mock_get_stripe_subscription.return_value = subscription
        mock_lms_instance = mock_lms_client.return_value
        mock_lms_instance.get_enterprise_customer_data.return_value = {
            'admin_users': [
                {'email': 'admin1@test.com'},
                {'email': 'admin2@test.com'},
            ]
        }
        mock_braze_instance = mock_braze_client.return_value
        mock_braze_instance.create_braze_recipient.side_effect = [
            {'external_user_id': '1'},
            {'external_user_id': '2'},
        ]
        send_trial_end_and_subscription_started_email_task('sub_123', 1)
        assert mock_braze_instance.send_campaign_message.called
        args, kwargs = mock_braze_instance.send_campaign_message.call_args
        assert args[0] == settings.BRAZE_ENTERPRISE_PROVISION_TRIAL_END_SUBSCRIPTION_STARTED_CAMPAIGN
        assert len(kwargs['recipients']) == 2
        props = kwargs['trigger_properties']
        assert props['total_license'] == 5
        assert props['billing_amount'] == '100'
        assert 'subscription_start_period' in props
        assert 'subscription_end_period' in props
        assert 'next_payment_date' in props
        assert props['organization'] == 'Test Org'
        assert props['invoice_url'] == 'https://invoice.url'

    @mock.patch("enterprise_access.apps.customer_billing.tasks.get_stripe_subscription")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.CheckoutIntent")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.BrazeApiClient")
    @mock.patch("enterprise_access.apps.customer_billing.tasks.LmsApiClient")
    def test_no_admins_logs_and_returns(
        self,
        mock_lms_client,
        mock_braze_client,
        mock_checkout_intent,
        mock_get_stripe_subscription
    ):
        subscription = {
            'id': 'sub_123',
            'quantity': 5,
            'plan': {'amount': 10000},
            'items': {
                "data": [
                    {
                        'current_period_start': 1762273481,  # 05 Dec 2025
                        'current_period_end': 1793809481,  # 05 Dec 2026
                    }
                ]
            },
            'latest_invoice': {'hosted_invoice_url': 'https://invoice.url'},
        }
        checkout_intent_obj = mock.Mock()
        checkout_intent_obj.enterprise_name = 'Test Org'
        checkout_intent_obj.enterprise_slug = 'test-org'
        mock_checkout_intent.objects.get.return_value = checkout_intent_obj
        mock_get_stripe_subscription.return_value = subscription
        mock_lms_instance = mock_lms_client.return_value
        mock_lms_instance.get_enterprise_customer_data.return_value = {'admin_users': []}

        with self.assertRaisesRegex(Exception, 'No admin users'):
            send_trial_end_and_subscription_started_email_task('sub_123', 1)

        assert not mock_braze_client.return_value.send_campaign_message.called
