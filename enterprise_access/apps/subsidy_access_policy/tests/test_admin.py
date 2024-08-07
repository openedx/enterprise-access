"""
Tests for the admin module.
"""
from datetime import date
from unittest import mock

from django.contrib.admin.sites import AdminSite
from django.test import TestCase

from ...core.tests.factories import UserFactory
from ..admin import ForcedPolicyRedemptionAdmin, ForcedPolicyRedemptionForm
from ..models import ForcedPolicyRedemption


class ForcedPolicyRedemptionAdminTests(TestCase):
    """
    Tests for the ForcedPolicyRedemptionAdmin class.
    """
    def test_force_redeem_with_extra_metadata(self):
        forced_redemption_admin = ForcedPolicyRedemptionAdmin(
            model=ForcedPolicyRedemption,
            admin_site=AdminSite(),
        )
        request = mock.Mock()
        forced_redemption_obj = mock.Mock(
            transaction_uuid=None,
            wait_to_redeem=False,
        )
        UserFactory.create(lms_user_id=123, email='foo@bar.com')

        form = ForcedPolicyRedemptionForm(data={
            'geag_first_name': 'Foo',
            'geag_last_name': 'Bar',
            'geag_date_of_birth': date(2000, 1, 1),
            'lms_user_id': 123,
        })

        forced_redemption_admin.save_model(
            obj=forced_redemption_obj,
            request=request,
            form=form,
            change=None,
        )

        forced_redemption_obj.force_redeem.assert_called_once_with(
            extra_metadata={
                'geag_first_name': 'Foo',
                'geag_last_name': 'Bar',
                'geag_date_of_birth': '2000-01-01',
                'geag_terms_accepted_at': mock.ANY,
                'geag_data_share_consent': True,
                'geag_email': 'foo@bar.com',
            }
        )
