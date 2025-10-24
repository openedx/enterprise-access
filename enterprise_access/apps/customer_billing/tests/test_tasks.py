"""
Tests for customer_billing tasks.
"""

from unittest import mock

from django.conf import settings
from django.test import TestCase

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.models import CheckoutIntent
from enterprise_access.apps.customer_billing.tasks import (
    send_trial_cancellation_email_task,
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
