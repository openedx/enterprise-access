"""
Tests for the ``enterprise_access.apps.customer_billing.utils`` module.
"""

import datetime

import pytz
from django.test import TestCase
from django.utils import timezone

from enterprise_access.apps.customer_billing.utils import datetime_from_timestamp


class TestCustomerBillingUtils(TestCase):
    """
    Tests for customer billing utility functions.
    """

    def test_datetime_from_timestamp_returns_aware_datetime(self):
        """datetime_from_timestamp should return a timezone-aware datetime."""
        ts = 1767285545

        dt = datetime_from_timestamp(ts)

        self.assertTrue(timezone.is_aware(dt))

    def test_datetime_from_timestamp_uses_current_timezone(self):
        """
        datetime_from_timestamp should attach the current Django timezone
        (make_aware default behavior).
        """
        ts = 1767285545

        dt = datetime_from_timestamp(ts)

        self.assertEqual(dt.tzinfo, pytz.UTC)

    def test_datetime_from_timestamp_has_expected_components(self):
        """
        Validate that datetime_from_timestamp returns the correct *local-date*
        representation for the given timestamp.
        """
        ts = 1767285545

        # Expected value computed the same way as the function
        expected_naive = datetime.datetime.fromtimestamp(ts)
        expected = timezone.make_aware(expected_naive)

        dt = datetime_from_timestamp(ts)

        self.assertIsInstance(dt, datetime.datetime)
        self.assertTrue(timezone.is_aware(dt))
        self.assertEqual(dt.date(), expected.date())
