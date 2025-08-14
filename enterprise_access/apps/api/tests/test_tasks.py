"""
Tests for Enterprise Access API tasks.
"""

from unittest import mock
from uuid import uuid4

import ddt

from enterprise_access.apps.api.serializers import CouponCodeRequestSerializer, LicenseRequestSerializer
from enterprise_access.apps.api.tasks import (
    assign_coupon_codes_task,
    assign_licenses_task,
    decline_enterprise_subsidy_requests_task,
    send_notification_email_for_request,
    unlink_users_from_enterprise_task,
    update_coupon_code_requests_after_assignments_task,
    update_license_requests_after_assignments_task
)
from enterprise_access.apps.subsidy_request.constants import SegmentEvents, SubsidyRequestStates, SubsidyTypeChoices
from enterprise_access.apps.subsidy_request.models import CouponCodeRequest, LicenseRequest
from enterprise_access.apps.subsidy_request.tests.factories import CouponCodeRequestFactory, LicenseRequestFactory
from test_utils import APITestWithMocks


@ddt.ddt
class TestTasks(APITestWithMocks):
    """
    Test tasks.
    """

    def setUp(self):
        super().setUp()
        self.enterprise_customer_uuid_1 = uuid4()
        self.subsidy_states_to_decline = [
            SubsidyRequestStates.REQUESTED,
            SubsidyRequestStates.PENDING,
            SubsidyRequestStates.ERROR,
        ]
        self.license_requests = []
        self.coupon_code_requests = []
        for state in self.subsidy_states_to_decline:
            self.license_requests.append(LicenseRequestFactory(
                enterprise_customer_uuid=self.enterprise_customer_uuid_1,
                user=self.user,
                state=state
            ))
            self.coupon_code_requests.append(CouponCodeRequestFactory(
                enterprise_customer_uuid=self.enterprise_customer_uuid_1,
                user=self.user,
                state=state
            ))

    def test_decline_requests_task_coupons(self):
        """
        Verify all coupon subsidies are declined
        """
        assert not CouponCodeRequest.objects.filter(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            user=self.user,
            state=SubsidyRequestStates.DECLINED,
        ).exists()

        subsidy_request_uuids = [str(request.uuid) for request in self.coupon_code_requests]
        decline_enterprise_subsidy_requests_task(
            subsidy_request_uuids,
            SubsidyTypeChoices.COUPON,
        )

        assert CouponCodeRequest.objects.filter(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            user=self.user,
            state=SubsidyRequestStates.DECLINED,
        ).count() == 3

    def test_track_coupon_code_request_denials(self):
        """
        Verify that an event is tracked when a coupon code request is declined.
        """
        coupon_code_request = self.coupon_code_requests[0]
        decline_enterprise_subsidy_requests_task(
            [str(coupon_code_request.uuid)],
            SubsidyTypeChoices.COUPON,
        )
        coupon_code_request.refresh_from_db()
        self.mock_analytics.assert_called_once_with(
            user_id=coupon_code_request.user.lms_user_id,
            event=SegmentEvents.COUPON_CODE_REQUEST_DECLINED,
            properties=CouponCodeRequestSerializer(coupon_code_request).data
        )

    def test_decline_requests_task_licenses(self):
        """
        Verify all license subsidies are declined
        """
        assert not LicenseRequest.objects.filter(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            user=self.user,
            state=SubsidyRequestStates.DECLINED,
        ).exists()

        subsidy_request_uuids = [str(request.uuid) for request in self.license_requests]
        decline_enterprise_subsidy_requests_task(
            subsidy_request_uuids,
            SubsidyTypeChoices.LICENSE,
        )

        assert LicenseRequest.objects.filter(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            user=self.user,
            state=SubsidyRequestStates.DECLINED,
        ).count() == 3

    def test_track_license_request_denials(self):
        """
        Verify that an event is tracked when a license request is declined.
        """
        license_request = self.license_requests[0]
        decline_enterprise_subsidy_requests_task(
            [str(license_request.uuid)],
            SubsidyTypeChoices.LICENSE,
        )
        license_request.refresh_from_db()
        self.mock_analytics.assert_called_once_with(
            user_id=license_request.user.lms_user_id,
            event=SegmentEvents.LICENSE_REQUEST_DECLINED,
            properties=LicenseRequestSerializer(license_request).data
        )

    @mock.patch('enterprise_access.apps.api.tasks.LmsApiClient', return_value=mock.MagicMock())
    @mock.patch('enterprise_access.apps.api.tasks.BrazeApiClient', return_value=mock.MagicMock())
    def test_send_notification_email_for_request(self, mock_braze_client, mock_lms_client):
        """
        Verify send_notification_email_for_request hits braze client with expected args
        """

        slug = 'sluggy'
        admin_email = 'edx@example.org'
        organization = 'Test Organization'

        mock_lms_client().get_enterprise_customer_data.return_value = {
            'slug': slug,
            'admin_users': [{
                'email': admin_email,
                'lms_user_id': 1
            }],
            'name': organization
        }

        mock_recipient = {
            'external_user_id': 1
        }

        mock_admin_mailto = f'mailto:{admin_email}'
        mock_braze_client().create_recipient.return_value = mock_recipient
        mock_braze_client().generate_mailto_link.return_value = mock_admin_mailto

        send_notification_email_for_request(
            self.license_requests[0].uuid,
            'test-campaign-id',
            SubsidyTypeChoices.LICENSE,
        )

        # Make sure our LMS client got called correct times and with what we expected
        mock_lms_client().get_enterprise_customer_data.assert_called_with(
            self.license_requests[0].enterprise_customer_uuid
        )

        expected_course_about_page_url = (
            f'http://enterprise-learner-portal.example.com/{slug}/course/' +
            self.license_requests[0].course_id
        )
        expected_enterprise_dashboard_url = f'http://enterprise-learner-portal.example.com/{slug}'
        mock_braze_client().send_campaign_message.assert_any_call(
            'test-campaign-id',
            recipients=[mock_recipient],
            trigger_properties={
                'contact_admin_link': mock_admin_mailto,
                'course_title': self.license_requests[0].course_title,
                'course_about_page_url': expected_course_about_page_url,
                'enterprise_dashboard_url': expected_enterprise_dashboard_url,
                'organization': organization,
            },
        )
        assert mock_braze_client().send_campaign_message.call_count == 1

    @mock.patch('enterprise_access.apps.api.tasks.LmsApiClient', return_value=mock.MagicMock())
    def test_unlink_users_from_enterprise_task(self, mock_lms_client):
        unlink_users_from_enterprise_task(self.enterprise_customer_uuid_1, [self.user.lms_user_id])
        mock_lms_client().unlink_users_from_enterprise.assert_called_with(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            user_emails=[self.user.email],
            is_relinkable=False
        )


class TestLicenseAssignmentTasks(APITestWithMocks):
    """
    Test license assignment tasks.
    """

    def setUp(self):
        super().setUp()
        self.enterprise_customer_uuid = uuid4()
        self.mock_enterprise_learner_data = {
            self.user.lms_user_id: {
                'email': self.user.email,
            }
        }
        self.pending_license_request = LicenseRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            user=self.user,
            state=SubsidyRequestStates.PENDING
        )

        self.mock_subscription_uuid = uuid4()
        self.mock_license_uuid = uuid4()

    @mock.patch('enterprise_access.apps.api.tasks.LicenseManagerApiClient')
    def test_assign_license_task(self, mock_license_manager_client):
        """
        Verify assign_licenses_task calls License Manager to assign new licenses.
        """

        mock_license_assignments = [{'user_email': self.user.email, 'license': self.mock_license_uuid}]
        mock_license_manager_client().assign_licenses.return_value = {
            'num_successful_assignments': 1,
            'num_already_associated': 0,
            'license_assignments': mock_license_assignments
        }

        license_assignment_results = assign_licenses_task(
            [self.pending_license_request.uuid],
            self.mock_subscription_uuid
        )

        mock_license_manager_client().assign_licenses.assert_called_with([self.user.email], self.mock_subscription_uuid)
        self.assertDictEqual(
            license_assignment_results,
            {
                'license_request_uuids': [self.pending_license_request.uuid],
                'assigned_licenses': {
                    assignment['user_email']: assignment['license'] for assignment in mock_license_assignments
                },
                'subscription_uuid': self.mock_subscription_uuid
            }
        )

    def test_update_license_requests_after_assignments_task(self):
        mock_license_assignments = [{'user_email': self.user.email, 'license': self.mock_license_uuid}]
        license_assignment_results = {
            'license_request_uuids': [self.pending_license_request.uuid],
            'assigned_licenses': {
                assignment['user_email']: assignment['license'] for assignment in mock_license_assignments
            },
            'subscription_uuid': self.mock_subscription_uuid
        }

        update_license_requests_after_assignments_task(
            license_assignment_results
        )

        self.pending_license_request.refresh_from_db()
        assert self.pending_license_request.state == SubsidyRequestStates.APPROVED
        assert self.pending_license_request.license_uuid == self.mock_license_uuid
        assert self.pending_license_request.subscription_plan_uuid == self.mock_subscription_uuid

        self.mock_analytics.assert_called_with(
            user_id=self.pending_license_request.user.lms_user_id,
            event=SegmentEvents.LICENSE_REQUEST_APPROVED,
            properties=LicenseRequestSerializer(self.pending_license_request).data
        )


class TestCouponCodeAssignmentTasks(APITestWithMocks):
    """
    Test coupon code assignment tasks.
    """

    def setUp(self):
        super().setUp()
        self.enterprise_customer_uuid = uuid4()
        self.mock_enterprise_learner_data = {
            self.user.lms_user_id: {
                'email': self.user.email,
            }
        }
        self.pending_coupon_code_request = CouponCodeRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            user=self.user,
            state=SubsidyRequestStates.PENDING
        )

        self.mock_coupon_id = 1
        self.mock_coupon_code = 'coupon_code'

    @mock.patch('enterprise_access.apps.api.tasks.EcommerceApiClient')
    def test_assign_coupon_codes_task(self, mock_ecommerce_client):
        """
        Verify assign_coupon_codes_task calls Ecommerece to assign new coupon codes.
        """
        mock_coupon_code_assignments = [{'user_email': self.user.email, 'code': self.mock_coupon_code}]
        mock_ecommerce_client().assign_coupon_codes.return_value = {
            'offer_assignments': mock_coupon_code_assignments
        }

        code_assignment_results = assign_coupon_codes_task(
            [self.pending_coupon_code_request.uuid],
            self.mock_coupon_id
        )

        mock_ecommerce_client().assign_coupon_codes.assert_called_with([self.user.email], self.mock_coupon_id)

        self.assertDictEqual(
            code_assignment_results,
            {
                'coupon_code_request_uuids': [self.pending_coupon_code_request.uuid],
                'assigned_codes': {
                    assignment['user_email']: assignment['code'] for assignment in mock_coupon_code_assignments
                },
                'coupon_id': self.mock_coupon_id
            }
        )

    def test_update_coupon_code_requests_after_assignments_task(self):
        mock_coupon_code_assignments = [{'user_email': self.user.email, 'code': self.mock_coupon_code}]
        coupon_code_assignment_results = {
            'coupon_code_request_uuids': [self.pending_coupon_code_request.uuid],
            'assigned_codes': {
                assignment['user_email']: assignment['code'] for assignment in mock_coupon_code_assignments
            },
            'coupon_id': self.mock_coupon_id
        }

        update_coupon_code_requests_after_assignments_task(
            coupon_code_assignment_results
        )

        self.pending_coupon_code_request.refresh_from_db()
        assert self.pending_coupon_code_request.state == SubsidyRequestStates.APPROVED
        assert self.pending_coupon_code_request.coupon_code == self.mock_coupon_code
        assert self.pending_coupon_code_request.coupon_id == self.mock_coupon_id

        self.mock_analytics.assert_called_with(
            user_id=self.pending_coupon_code_request.user.lms_user_id,
            event=SegmentEvents.COUPON_CODE_REQUEST_APPROVED,
            properties=CouponCodeRequestSerializer(self.pending_coupon_code_request).data
        )
