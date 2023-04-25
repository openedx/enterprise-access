""" Tests for core models. """

from datetime import datetime
from uuid import uuid4

import ddt
from django.forms import ValidationError
from pytest import mark

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates
from enterprise_access.apps.subsidy_request.tests.factories import CouponCodeRequestFactory, LicenseRequestFactory
from test_utils import TestCaseWithMockedDiscoveryApiClient

now = datetime.utcnow()


@ddt.ddt
@mark.django_db
class LicenseRequestTests(TestCaseWithMockedDiscoveryApiClient):
    """ LicenseRequest model tests. """

    mock_subscription_plan_uuid = uuid4()
    mock_license_uuid = uuid4()

    @ddt.data(
        (None, now),
        (1, None)
    )
    @ddt.unpack
    def test_missing_review_info(self, reviewer_id, reviewed_at):
        with self.assertRaises(ValidationError) as error:
            reviewer = UserFactory(id=reviewer_id) if reviewer_id else None
            license_request = LicenseRequestFactory(
                state=SubsidyRequestStates.PENDING,
                reviewer=reviewer,
                reviewed_at=reviewed_at,
            )
            license_request.save()

        expected_error = 'Both reviewer and reviewed_at are required for a review.'
        assert error.exception.messages[0] == expected_error

    @ddt.data(
        (mock_subscription_plan_uuid, None),
        (None, mock_license_uuid),
    )
    @ddt.unpack
    def test_missing_license_info(self, subscription_plan_uuid, license_uuid):
        with self.assertRaises(ValidationError) as error:
            license_request = LicenseRequestFactory(
                state=SubsidyRequestStates.APPROVED,
                subscription_plan_uuid=subscription_plan_uuid,
                license_uuid=license_uuid,
            )
            license_request.save()

        expected_error = 'Both subscription_plan_uuid and license_uuid are required for a fulfilled license request.'
        assert error.exception.messages[0] == expected_error

    def test_update_course_info_from_discovery(self):
        """
        course data should be fetched from discovery if not set on subsidy object
        during a save().
        """
        original_call_count = self.mock_discovery_client.call_count

        subsidy = LicenseRequestFactory(course_title=None, course_partners=None)
        assert self.mock_discovery_client.call_count == original_call_count + 1

        subsidy.refresh_from_db()
        subsidy.save()
        assert self.mock_discovery_client.call_count == original_call_count + 1


@ddt.ddt
class CouponCodeRequestTests(TestCaseWithMockedDiscoveryApiClient):
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
                state=SubsidyRequestStates.APPROVED,
                coupon_id=coupon_id,
                coupon_code=coupon_code,
            )
            coupon_code_request.save()

        expected_error = 'Both coupon_id and coupon_code are required for a fulfilled coupon request.'
        assert error.exception.messages[0] == expected_error

    def test_update_course_info_from_discovery(self):
        """
        course data should be fetched from discovery if not set on subsidy object
        during a save().
        """
        original_call_count = self.mock_discovery_client.call_count

        subsidy = CouponCodeRequestFactory(course_title=None, course_partners=None)
        assert self.mock_discovery_client.call_count == original_call_count + 1

        subsidy.refresh_from_db()
        subsidy.save()
        assert self.mock_discovery_client.call_count == original_call_count + 1
