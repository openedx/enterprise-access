"""
Test content_metadata_api.py
"""
import contextlib

import ddt
from django.test import TestCase

from enterprise_access.apps.subsidy_access_policy.content_metadata_api import make_list_price_dict


@ddt.ddt
class ContentMetadataApiTests(TestCase):
    """
    Test various functions inside content_metadata_api.py
    """

    @ddt.data(
        # Standard happy path.
        {
            "decimal_dollars": 10.5,
            "integer_cents": None,
            "expected_result": {"usd": 10.5, "usd_cents": 1050},
        },
        # Standard happy path.
        {
            "decimal_dollars": None,
            "integer_cents": 1050,
            "expected_result": {"usd": 10.5, "usd_cents": 1050},
        },
        # Weird precision input just gets passed along as-is.
        {
            "decimal_dollars": 10.503,
            "integer_cents": None,
            "expected_result": {"usd": 10.503, "usd_cents": 1050},
        },
        # All None.
        {
            "decimal_dollars": None,
            "integer_cents": None,
            "expect_raises": ValueError,
        },
        # All defined.
        {
            "decimal_dollars": 10.5,
            "integer_cents": 1050,
            "expect_raises": ValueError,
        },
    )
    @ddt.unpack
    def test_make_list_price_dict(
        self,
        decimal_dollars,
        integer_cents,
        expected_result=None,
        expect_raises=None,
    ):
        cm = contextlib.nullcontext()
        if expect_raises:
            cm = self.assertRaises(expect_raises)
        with cm:
            actual_result = make_list_price_dict(
                decimal_dollars=decimal_dollars,
                integer_cents=integer_cents,
            )
        if not expect_raises:
            assert actual_result == expected_result
