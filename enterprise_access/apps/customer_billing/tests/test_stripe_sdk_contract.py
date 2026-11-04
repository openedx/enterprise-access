"""
Contract tests between enterprise-access's Stripe usage and the installed stripe SDK.

Stripe calls are mocked in unit tests, so nothing else fails when an SDK upgrade changes what the
Stripe API accepts or returns. A given Stripe API version never changes, and we don't set
``stripe.api_version``, so new API behavior only reaches us through an SDK upgrade (each release sends
the API version it pins) or through the webhook endpoint's API version in the Stripe Dashboard, which
tests can't see. See ``docs/references/stripe_sdk_upgrades.md``.
"""
from pathlib import Path

import ddt
import stripe
from django.test import SimpleTestCase

import enterprise_access
from enterprise_access.apps.customer_billing.tests.utils import stripe_call_problems

# The Stripe API version our Stripe usage has been reviewed against. Removals can ship in any API
# version, not only in a new named release (e.g. stripe 15.6.0 stayed on ``dahlia`` and still removed
# ``PaymentAttemptRecord...cryptogram``), so this trips on every pinned-version change.
REVIEWED_STRIPE_API_VERSION = '2026-10-28.endive'

SOURCE_ROOT = Path(enterprise_access.__file__).parent


@ddt.ddt
class StripeSdkContractTests(SimpleTestCase):
    """Fail when a stripe SDK upgrade changes the Stripe API underneath our (mocked) Stripe usage."""

    def test_installed_sdk_pins_a_reviewed_api_version(self):
        """
        An SDK upgrade that changes the pinned API version must be reviewed by a person: response fields
        and webhook payloads can change shape, and no mocked test will notice.
        """
        self.assertEqual(
            stripe.api_version, REVIEWED_STRIPE_API_VERSION,
            f'stripe=={stripe.VERSION} sends Stripe API {stripe.api_version}, not the reviewed '
            f'{REVIEWED_STRIPE_API_VERSION}. Review the warning (⚠️) entries in the stripe-python CHANGELOG '
            'for every release in between against the Stripe response fields and webhook payloads '
            'customer_billing reads, then update REVIEWED_STRIPE_API_VERSION. '
            'See docs/references/stripe_sdk_upgrades.md.',
        )

    def test_stripe_call_sites_match_installed_sdk(self):
        """
        Every ``stripe.<Resource>.<method>(...)`` call in app code must still exist in the installed SDK,
        bind to its signature, and only pass params (including nested literal keys) that it declares.
        """
        problems = []
        for path in sorted(SOURCE_ROOT.rglob('*.py')):
            if {'tests', 'migrations'} & set(path.relative_to(SOURCE_ROOT).parts):
                continue
            problems += stripe_call_problems(path.read_text(), str(path.relative_to(SOURCE_ROOT.parent)))
        self.assertEqual(problems, [])

    @ddt.data(
        ("stripe.Subscription.modify('sub_1', cancel_at_period_end=True)", []),
        ("stripe.Subscription.modify('sub_1', not_a_param=True)", ['does not accept param not_a_param']),
        (
            "stripe.Subscription.modify('sub_1', trial_settings={'end_behavior': {'missing_payment_methd': 'x'}})",
            ['does not accept param trial_settings.end_behavior.missing_payment_methd'],
        ),
        ("stripe.Coupon.delete('co_1')", []),
        ("stripe.Coupon.delete('co_1', not_a_param=True)", ['does not accept param not_a_param']),
        ("stripe.NotAResource.retrieve('x')", ['does not exist in stripe==']),
        ("isinstance(obj, stripe.NotAResource)", ['stripe.NotAResource: does not exist in stripe==']),
        ("stripe.Subscription.modify(id='sub_1')", ['passes positional-only id by keyword']),
        ("stripe.Coupon.delete(sid='co_1')", ['passes positional-only sid by keyword']),
        ("stripe.Webhook.construct_event('payload', 'sig', 'secret', not_an_arg=1)", ['not_an_arg']),
    )
    @ddt.unpack
    def test_call_site_check_flags_breakages(self, source, expected_problems):
        """The call-site check must actually flag each kind of breakage, so a clean scan means something."""
        problems = stripe_call_problems(source)

        self.assertEqual(len(problems), len(expected_problems), problems)
        for problem, expected in zip(problems, expected_problems):
            self.assertIn(expected, problem)
