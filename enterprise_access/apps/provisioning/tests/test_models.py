"""
Tests for the provisioning.models module.
"""
from datetime import datetime
from unittest import mock
from uuid import uuid4

from django.test import TestCase

from enterprise_access.apps.api_client.license_manager_client import LicenseManagerApiClient
from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.constants import CheckoutIntentState
from enterprise_access.apps.customer_billing.models import (
    CheckoutIntent,
    SelfServiceSubscriptionRenewal,
    StripeEventSummary
)
from enterprise_access.apps.customer_billing.tests.factories import (
    CheckoutIntentFactory,
    StripeEventDataFactory,
    StripeEventSummaryFactory
)
from enterprise_access.apps.provisioning.models import GetCreateSubscriptionPlanRenewalStep
from enterprise_access.apps.provisioning.tests.factories import ProvisionNewCustomerWorkflowFactory


class TestGetCreateSubscriptionPlanRenewalStep(TestCase):
    """
    Tests for the GetCreateSubscriptionPlanRenewalStep model and its renewal record creation.
    """

    def setUp(self):
        """Set up test data."""
        self.user = UserFactory()
        self.workflow = ProvisionNewCustomerWorkflowFactory()
        self.checkout_intent = CheckoutIntentFactory(user=self.user)

        # Link the checkout intent to the workflow
        self.checkout_intent.workflow = self.workflow
        self.checkout_intent.save()

        self.renewal_step = GetCreateSubscriptionPlanRenewalStep.objects.create(
            workflow_record_uuid=self.workflow.uuid,
            input_data={
                'title': 'Test Renewal Plan',
                'salesforce_opportunity_line_item': 'test-oli-456',
                'start_date': '2025-01-01T00:00:00Z',
                'expiration_date': '2026-01-01T00:00:00Z',
                'desired_num_licenses': 10,
            }
        )

    def tearDown(self):
        """Clean up test data."""
        SelfServiceSubscriptionRenewal.objects.all().delete()
        StripeEventSummary.objects.all().delete()
        CheckoutIntent.objects.all().delete()

    @mock.patch.object(LicenseManagerApiClient, 'create_subscription_plan_renewal')
    def test_process_input_creates_renewal_record(self, mock_create_renewal):
        """Test that processing input creates a SelfServiceSubscriptionRenewal record."""
        mock_renewal_response = {
            'id': 123,
            'title': 'Test Renewal Plan',
            'created': '2024-01-15T10:30:00Z',
            'start_date': '2025-01-01T00:00:00Z',
            'expiration_date': '2026-01-01T00:00:00Z',
            'salesforce_opportunity_id': 'test-oli-456',
            'prior_subscription_plan': str(uuid4()),
            'renewed_subscription_plan': str(uuid4()),
            'number_of_licenses': 10,
            'effective_date': '2025-01-01T00:00:00Z',
            'renewed_expiration_date': '2027-01-01T00:00:00Z',
        }
        mock_create_renewal.return_value = mock_renewal_response

        # Create an existing StripeEventSummary to provide stripe_subscription_id
        stripe_subscription_id = 'sub_test_12345'
        event_data = StripeEventDataFactory.create(checkout_intent=self.checkout_intent)
        summary = StripeEventSummaryFactory.create(
            stripe_event_data=event_data,
        )
        summary.stripe_subscription_id = stripe_subscription_id
        summary.save()

        # Create mock accumulated_output with the required structure
        mock_accumulated_output = mock.Mock()
        mock_accumulated_output.create_trial_subscription_plan_output = mock.Mock(
            uuid=mock_renewal_response['prior_subscription_plan']
        )
        mock_accumulated_output.create_first_paid_subscription_plan_output = mock.Mock(
            uuid=mock_renewal_response['renewed_subscription_plan'],
            expiration_date=datetime(2027, 1, 1),
        )

        # Process the input
        result = self.renewal_step.process_input(mock_accumulated_output)

        # Verify the license manager API was called
        mock_create_renewal.assert_called_once()

        # Verify the response matches the mock
        result_dict = result.to_dict()
        self.assertEqual(result_dict['id'], mock_renewal_response['id'])
        self.assertEqual(result_dict['prior_subscription_plan'], mock_renewal_response['prior_subscription_plan'])
        self.assertEqual(result_dict['renewed_subscription_plan'], mock_renewal_response['renewed_subscription_plan'])

        # Verify a SelfServiceSubscriptionRenewal record was created
        expected_renewal_id = mock_renewal_response['id']
        renewal_record = SelfServiceSubscriptionRenewal.objects.get(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id
        )
        self.assertEqual(renewal_record.stripe_subscription_id, stripe_subscription_id)
        self.assertIsNone(renewal_record.processed_at)
        self.assertEqual(renewal_record.stripe_event_data, summary.stripe_event_data)

    @mock.patch.object(LicenseManagerApiClient, 'create_subscription_plan_renewal')
    def test_process_input_with_existing_renewal_record(self, mock_create_renewal):
        """Test that processing with existing renewal record is idempotent."""
        mock_renewal_response = {
            'id': 123,
            'title': 'Test Renewal Plan',
            'created': '2024-01-15T10:30:00Z',
            'salesforce_opportunity_id': 'test-oli-456',
            'prior_subscription_plan': str(uuid4()),
            'renewed_subscription_plan': str(uuid4()),
            'number_of_licenses': 10,
            'effective_date': '2025-01-01T00:00:00Z',
            'renewed_expiration_date': '2027-01-01T00:00:00Z',
        }
        mock_create_renewal.return_value = mock_renewal_response

        stripe_subscription_id = 'sub_test_12345'
        event_data = StripeEventDataFactory.create(checkout_intent=self.checkout_intent)
        summary = StripeEventSummaryFactory.create(
            stripe_event_data=event_data,
            stripe_subscription_id=stripe_subscription_id,
            subscription_status='trialing',
        )

        expected_renewal_id = 123
        existing_renewal = SelfServiceSubscriptionRenewal.objects.create(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id,
            stripe_subscription_id=summary.stripe_subscription_id,
            stripe_event_data=summary.stripe_event_data,
        )

        mock_accumulated_output = mock.Mock()
        mock_accumulated_output.create_trial_subscription_plan_output = mock.Mock()
        mock_accumulated_output.create_trial_subscription_plan_output.uuid = str(uuid4())
        mock_accumulated_output.create_trial_subscription_plan_output.expiration_date = datetime(2026, 1, 1)

        mock_accumulated_output.create_first_paid_subscription_plan_output = mock.Mock()
        mock_accumulated_output.create_first_paid_subscription_plan_output.uuid = str(uuid4())
        mock_accumulated_output.create_first_paid_subscription_plan_output.expiration_date = datetime(2027, 1, 1)

        # Process the input
        self.renewal_step.process_input(mock_accumulated_output)

        # Verify the license manager API was called
        mock_create_renewal.assert_called_once()

        # Verify only one renewal record exists (no duplicate created)
        renewal_records = SelfServiceSubscriptionRenewal.objects.filter(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id
        )
        self.assertEqual(renewal_records.count(), 1)

        # Verify it's the same record
        renewal_record = renewal_records.first()
        self.assertEqual(renewal_record.id, existing_renewal.id)
        self.assertEqual(renewal_record.stripe_subscription_id, summary.stripe_subscription_id)

    @mock.patch.object(LicenseManagerApiClient, 'create_subscription_plan_renewal')
    def test_process_input_without_stripe_subscription_id(self, mock_create_renewal):
        """Test creating renewal record fails when no StripeEventSummary exists yet."""
        mock_renewal_response = {
            'id': 456,
            'title': 'Test Renewal Plan',
            'created': '2024-01-15T10:30:00Z',
            'salesforce_opportunity_id': None,
            'prior_subscription_plan': str(uuid4()),
            'renewed_subscription_plan': str(uuid4()),
            'number_of_licenses': 10,
            'effective_date': '2025-01-01T00:00:00Z',
            'renewed_expiration_date': '2027-01-01T00:00:00Z',
        }
        mock_create_renewal.return_value = mock_renewal_response

        # Don't create any StripeEventSummary records
        mock_accumulated_output = mock.Mock()
        mock_accumulated_output.create_trial_subscription_plan_output = mock.Mock()
        mock_accumulated_output.create_trial_subscription_plan_output.uuid = str(uuid4())
        mock_accumulated_output.create_trial_subscription_plan_output.expiration_date = datetime(2026, 1, 1)

        mock_accumulated_output.create_first_paid_subscription_plan_output = mock.Mock()
        mock_accumulated_output.create_first_paid_subscription_plan_output.uuid = str(uuid4())
        mock_accumulated_output.create_first_paid_subscription_plan_output.expiration_date = datetime(2027, 1, 1)

        with self.assertRaises(self.renewal_step.exception_class):
            self.renewal_step.process_input(mock_accumulated_output)

        # Verify the license manager API was called
        mock_create_renewal.assert_called_once()

        # But no renewal record is written in this exceptional case
        self.assertEqual(SelfServiceSubscriptionRenewal.objects.all().count(), 0)

    @mock.patch.object(LicenseManagerApiClient, 'create_subscription_plan_renewal')
    def test_process_input_license_manager_error(self, mock_create_renewal):
        """Test error handling when license manager API fails."""
        # Mock license manager API failure
        mock_create_renewal.side_effect = Exception("License Manager API error")

        mock_accumulated_output = mock.Mock()
        mock_accumulated_output.create_trial_subscription_plan_output = mock.Mock()
        mock_accumulated_output.create_trial_subscription_plan_output.uuid = str(uuid4())
        mock_accumulated_output.create_trial_subscription_plan_output.expiration_date = datetime(2026, 1, 1)

        mock_accumulated_output.create_first_paid_subscription_plan_output = mock.Mock()
        mock_accumulated_output.create_first_paid_subscription_plan_output.uuid = str(uuid4())
        mock_accumulated_output.create_first_paid_subscription_plan_output.expiration_date = datetime(2027, 1, 1)

        self.checkout_intent.state = CheckoutIntentState.PAID
        self.checkout_intent.save()
        # Process the input and expect the exception to propagate
        with self.assertRaises(Exception) as context:
            self.renewal_step.process_input(mock_accumulated_output)

        self.assertIn("Failed to get/create subscription plan renewal", str(context.exception))

        # Verify no SelfServiceSubscriptionRenewal record was created on failure
        renewal_records = SelfServiceSubscriptionRenewal.objects.filter(
            checkout_intent=self.checkout_intent
        )
        self.assertEqual(renewal_records.count(), 0)

    @mock.patch.object(LicenseManagerApiClient, 'create_subscription_plan_renewal')
    def test_process_input_gets_latest_stripe_subscription_id(self, mock_create_renewal):
        """Test that the latest StripeEventSummary stripe_subscription_id is used."""
        mock_renewal_response = {
            'id': 789,
            'title': 'Test Renewal Plan',
            'created': '2024-01-15T10:30:00Z',
            'salesforce_opportunity_id': None,
            'prior_subscription_plan': str(uuid4()),
            'renewed_subscription_plan': str(uuid4()),
            'number_of_licenses': 10,
            'effective_date': '2025-01-01T00:00:00Z',
            'renewed_expiration_date': '2027-01-01T00:00:00Z',
        }
        mock_create_renewal.return_value = mock_renewal_response

        # Create multiple StripeEventSummary records with different timestamps
        older_event = StripeEventDataFactory(checkout_intent=self.checkout_intent)
        older_summary = older_event.summary
        older_summary.stripe_subscription_id = 'sub_older_123'
        older_summary.stripe_event_created_at = older_summary.stripe_event_created_at.replace(hour=1)
        older_summary.save()

        latest_event = StripeEventDataFactory(checkout_intent=self.checkout_intent)
        latest_summary = latest_event.summary
        latest_summary.stripe_subscription_id = 'sub_latest_456'
        latest_summary.stripe_event_created_at = latest_summary.stripe_event_created_at.replace(hour=2)
        latest_summary.save()

        mock_accumulated_output = mock.Mock()
        mock_accumulated_output.create_trial_subscription_plan_output = mock.Mock()
        mock_accumulated_output.create_trial_subscription_plan_output.uuid = str(uuid4())
        mock_accumulated_output.create_trial_subscription_plan_output.expiration_date = datetime(2026, 1, 1)

        mock_accumulated_output.create_first_paid_subscription_plan_output = mock.Mock()
        mock_accumulated_output.create_first_paid_subscription_plan_output.uuid = str(uuid4())
        mock_accumulated_output.create_first_paid_subscription_plan_output.expiration_date = datetime(2027, 1, 1)

        # Process the input
        self.renewal_step.process_input(mock_accumulated_output)

        # Verify the renewal record uses the latest stripe_subscription_id
        expected_renewal_id = 789
        renewal_record = SelfServiceSubscriptionRenewal.objects.get(
            checkout_intent=self.checkout_intent,
            subscription_plan_renewal_id=expected_renewal_id
        )
        self.assertEqual(renewal_record.stripe_subscription_id, 'sub_latest_456')
