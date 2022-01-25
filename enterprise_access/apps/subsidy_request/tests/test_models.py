""" Tests for core models. """

from datetime import datetime
from uuid import uuid4

import ddt
from django.forms import ValidationError
from django.test import TestCase

from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates
from enterprise_access.apps.subsidy_request.tests.factories import CouponCodeRequestFactory, LicenseRequestFactory

now = datetime.utcnow()
mock_lms_user_id = 1

@ddt.ddt
class LicenseRequestTests(TestCase):
    """ LicenseRequest model tests. """

    mock_subscription_plan_uuid = uuid4()
    mock_license_uuid = uuid4()

    @ddt.data(
        (None, now),
        (mock_lms_user_id, None)
    )
    @ddt.unpack
    def test_missing_review_info(self, reviewer_lms_user_id, reviewed_at):
        with self.assertRaises(ValidationError) as error:
            license_request = LicenseRequestFactory(
                state=SubsidyRequestStates.APPROVED_PENDING,
                reviewer_lms_user_id=reviewer_lms_user_id,
                reviewed_at=reviewed_at,
            )
            license_request.save()

        expected_error = 'Both reviewer_lms_user_id and reviewed_at are required for a review.'
        print(error.exception.messages)
        assert error.exception.messages[0] == expected_error


    @ddt.data(
        (mock_subscription_plan_uuid, None),
        (None, mock_license_uuid),
    )
    @ddt.unpack
    def test_missing_license_info(self, subscription_plan_uuid, license_uuid):
        with self.assertRaises(ValidationError) as error:
            license_request = LicenseRequestFactory(
                state=SubsidyRequestStates.APPROVED_FULFILLED,
                subscription_plan_uuid=subscription_plan_uuid,
                license_uuid=license_uuid,
            )
            license_request.save()

        expected_error = 'Both subscription_plan_uuid and license_uuid are required for a fulfilled license request.'
        assert error.exception.messages[0] == expected_error

@ddt.ddt
class CouponCodeRequestTests(TestCase):
    """ CouponCodeRequest model tests. """

    mock_coupon_id = 123456
    mock_coupon_code = uuid4()

    @ddt.data(
        (mock_coupon_id, None),
        (None, mock_coupon_code),
    )
    @ddt.unpack
    def test_missing_coupon_info(self, coupon_id, coupon_code):
        with self.assertRaises(ValidationError) as error:
            coupon_code_request = CouponCodeRequestFactory(
                state=SubsidyRequestStates.APPROVED_FULFILLED,
                coupon_id=coupon_id,
                coupon_code=coupon_code,
            )
            coupon_code_request.save()

        expected_error = 'Both coupon_id and coupon_code are required for a fulfilled coupon request.'
        assert error.exception.messages[0] == expected_error
