"""
Unit tests for the provisioning utils module.
"""
from unittest import mock

from django.test import TestCase

from enterprise_access.apps.provisioning.utils import validate_trial_subscription


class TestValidateTrialSubscription(TestCase):
    """
    Tests for validate_trial_subscription function.
    """

    def test_no_checkout_intent(self):
        """
        Test when no checkout intent exists for the enterprise slug.
        """
        result_valid, result_subscription = validate_trial_subscription('test-slug')
        self.assertFalse(result_valid)
        self.assertIsNone(result_subscription)

    @mock.patch('enterprise_access.apps.provisioning.utils.CheckoutIntent')
    def test_no_stripe_customer_id(self, mock_checkout_intent):
        """
        Test when checkout intent exists but has no stripe customer ID.
        """
        mock_intent = mock.MagicMock()
        mock_intent.stripe_customer_id = None
        mock_checkout_intent.objects.filter.return_value.first.return_value = mock_intent

        result_valid, result_subscription = validate_trial_subscription('test-slug')

        self.assertFalse(result_valid)
        self.assertIsNone(result_subscription)
        mock_checkout_intent.objects.filter.assert_called_once_with(enterprise_slug='test-slug')

    @mock.patch('enterprise_access.apps.provisioning.utils.get_stripe_trialing_subscription')
    @mock.patch('enterprise_access.apps.provisioning.utils.CheckoutIntent')
    def test_no_trial_subscription(self, mock_checkout_intent, mock_get_subscription):
        """
        Test when intent and customer exist but no trial subscription is found.
        """
        mock_intent = mock.MagicMock()
        mock_intent.stripe_customer_id = 'cus_123'
        mock_checkout_intent.objects.filter.return_value.first.return_value = mock_intent
        mock_get_subscription.return_value = None

        result_valid, result_subscription = validate_trial_subscription('test-slug')

        self.assertFalse(result_valid)
        self.assertIsNone(result_subscription)
        mock_checkout_intent.objects.filter.assert_called_once_with(enterprise_slug='test-slug')
        mock_get_subscription.assert_called_once_with('cus_123')

    @mock.patch('enterprise_access.apps.provisioning.utils.get_stripe_trialing_subscription')
    @mock.patch('enterprise_access.apps.provisioning.utils.CheckoutIntent')
    def test_valid_trial_subscription(self, mock_checkout_intent, mock_get_subscription):
        """
        Test the happy path - valid trial subscription exists.
        """
        mock_intent = mock.MagicMock()
        mock_intent.stripe_customer_id = 'cus_123'
        mock_checkout_intent.objects.filter.return_value.first.return_value = mock_intent

        mock_subscription = {'id': 'sub_123', 'status': 'trialing'}
        mock_get_subscription.return_value = mock_subscription

        result_valid, result_subscription = validate_trial_subscription('test-slug')

        self.assertTrue(result_valid)
        self.assertEqual(result_subscription, mock_subscription)
        mock_checkout_intent.objects.filter.assert_called_once_with(enterprise_slug='test-slug')
        mock_get_subscription.assert_called_once_with('cus_123')

    @mock.patch('enterprise_access.apps.provisioning.utils.get_stripe_trialing_subscription')
    @mock.patch('enterprise_access.apps.provisioning.utils.CheckoutIntent')
    def test_validation_error(self, mock_checkout_intent, mock_get_subscription):
        """
        Test when an exception occurs during validation.
        """
        mock_checkout_intent.objects.filter.side_effect = Exception('Database error')

        result_valid, result_subscription = validate_trial_subscription('test-slug')

        self.assertFalse(result_valid)
        self.assertIsNone(result_subscription)
        mock_checkout_intent.objects.filter.assert_called_once_with(enterprise_slug='test-slug')
        mock_get_subscription.assert_not_called()
