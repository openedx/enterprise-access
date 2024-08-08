"""
Tests for the admin module.
"""
from datetime import date, datetime
from unittest import mock

from django.contrib.admin.sites import AdminSite
from django.test import TestCase

from ...core.tests.factories import UserFactory
from ..admin import GEAG_DATETIME_FMT, ForcedPolicyRedemptionAdmin, ForcedPolicyRedemptionForm
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
        terms_accepted_value =\
            forced_redemption_obj.force_redeem.call_args_list[0][1]['extra_metadata']['geag_terms_accepted_at']
        # we don't really care about the value, but
        # we want to know that parsing the value matches the datetime
        # format accepted by GEAG.
        assert datetime.strptime(terms_accepted_value, GEAG_DATETIME_FMT)
