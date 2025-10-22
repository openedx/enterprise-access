"""
Unit tests for customer billing tasks.
"""
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.test import TestCase

from enterprise_access.apps.customer_billing.tasks import send_enterprise_provision_signup_confirmation_email


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
