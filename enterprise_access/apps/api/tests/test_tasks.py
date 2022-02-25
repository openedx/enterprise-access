"""
Tests for Enterprise Access API tasks.
"""

from uuid import uuid4

import mock

from enterprise_access.apps.api.exceptions import MissingEnterpriseLearnerDataError
from enterprise_access.apps.api.tasks import (
    assign_coupon_codes_task,
    assign_licenses_task,
    decline_enterprise_subsidy_requests_task,
    send_notification_emails_for_requests,
    update_coupon_code_requests_after_assignments_task,
    update_license_requests_after_assignments_task
)
from enterprise_access.apps.subsidy_request.constants import (
    ENTERPRISE_BRAZE_ALIAS_LABEL,
    SubsidyRequestStates,
    SubsidyTypeChoices
)
from enterprise_access.apps.subsidy_request.models import CouponCodeRequest, LicenseRequest
from enterprise_access.apps.subsidy_request.tests.factories import CouponCodeRequestFactory, LicenseRequestFactory
from test_utils import APITest


class TestTasks(APITest):
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
                lms_user_id=self.user.lms_user_id,
                state=state
            ))
            self.coupon_code_requests.append(CouponCodeRequestFactory(
                enterprise_customer_uuid=self.enterprise_customer_uuid_1,
                lms_user_id=self.user.lms_user_id,
                state=state
            ))

    def test_decline_requests_task_coupons(self):
        """
        Verify all coupon subsidies are declined
        """
        assert not CouponCodeRequest.objects.filter(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            lms_user_id=self.user.lms_user_id,
            state=SubsidyRequestStates.DECLINED,
        ).exists()

        subsidy_request_uuids = [str(request.uuid) for request in self.coupon_code_requests]
        decline_enterprise_subsidy_requests_task(
            subsidy_request_uuids,
            SubsidyTypeChoices.COUPON,
        )

        assert CouponCodeRequest.objects.filter(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            lms_user_id=self.user.lms_user_id,
            state=SubsidyRequestStates.DECLINED,
        ).count() == 3

    def test_decline_requests_task_licenses(self):
        """
        Verify all license subsidies are declined
        """
        assert not LicenseRequest.objects.filter(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            lms_user_id=self.user.lms_user_id,
            state=SubsidyRequestStates.DECLINED,
        ).exists()

        subsidy_request_uuids = [str(request.uuid) for request in self.license_requests]
        decline_enterprise_subsidy_requests_task(
            subsidy_request_uuids,
            SubsidyTypeChoices.LICENSE,
        )

        assert LicenseRequest.objects.filter(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            lms_user_id=self.user.lms_user_id,
            state=SubsidyRequestStates.DECLINED,
        ).count() == 3

    @mock.patch('enterprise_access.apps.api.tasks.LmsApiClient', return_value=mock.MagicMock())
    @mock.patch('enterprise_access.apps.api.tasks.BrazeApiClient', return_value=mock.MagicMock())
    def test_send_notification_emails_for_requests(self, mock_braze_client, mock_lms_client):
        """
        Verify send_notification_emails_for_requests hits braze client with expected args
        """
        user_email = 'example@example.com'

        mock_lms_client().get_enterprise_learner_data.return_value = {
            self.user.lms_user_id: {
                'email': user_email,
                'enterprise_customer': {
                    'contact_email': 'example2@example.com',
                    'slug': 'test-org-for-learning'
                }
            }
        }

        # Run the task
        subsidy_request_uuids = [self.license_requests[0].uuid]  # Just use 1 to prevent flakiness
        send_notification_emails_for_requests(
            subsidy_request_uuids,
            'test-campaign-id',
            SubsidyTypeChoices.LICENSE,
        )

        # Make sure our LMS client got called correct times and with what we expected
        assert mock_lms_client().get_enterprise_learner_data.call_count == 1

        # And also the same for the Braze Client
        expected_recipient = {
            'attributes': {'email': user_email},
            'user_alias': {
                'alias_label': ENTERPRISE_BRAZE_ALIAS_LABEL,
                'alias_name': user_email,
            },
        }
        expected_course_about_page_url = (
            f'http://enterprise-learner-portal.example.com/test-org-for-learning/course/' +
            self.license_requests[0].course_id
        )
        mock_braze_client().send_campaign_message.assert_any_call(
            'test-campaign-id',
            recipients=[expected_recipient],
            trigger_properties={
                'contact_email': 'example2@example.com',
                'course_about_page_url': expected_course_about_page_url},
            )
        assert mock_braze_client().send_campaign_message.call_count == 1


class TestLicenseAssignmentTasks(APITest):
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
            lms_user_id=self.user.lms_user_id,
            state=SubsidyRequestStates.PENDING
        )

        self.mock_subscription_uuid = uuid4()
        self.mock_license_uuid = uuid4()

    @mock.patch('enterprise_access.apps.api.tasks.LmsApiClient', return_value=mock.MagicMock())
    def test_get_enterprise_learner_data_error(self, mock_lms_client):
        """
        Verify that _get_enterprise_learner_data throws an error if the the LMS API client
        does not return data for all lms_user_ids.
        """
        mock_enterprise_learner_data = {
            1: {},
        }
        mock_lms_client().get_enterprise_learner_data.return_value = mock_enterprise_learner_data
        lms_user_ids = [1,1,2]

        with self.assertRaises(MissingEnterpriseLearnerDataError):
            assign_licenses_task(
                [self.pending_license_request.uuid],
                self.mock_subscription_uuid
            )

        assert mock_lms_client().get_enterprise_learner_data.called_with(
            set(lms_user_ids)
        )

    @mock.patch('enterprise_access.apps.api.tasks.LmsApiClient')
    @mock.patch('enterprise_access.apps.api.tasks.LicenseManagerApiClient')
    def test_assign_license_task(self, mock_license_manager_client, mock_lms_client):
        """
        Verify assign_licenses_task calls License Manager to assign new licenses.
        """

        mock_lms_client().get_enterprise_learner_data.return_value = self.mock_enterprise_learner_data
        mock_license_assignments = [{ 'user_email': self.user.email, 'license': self.mock_license_uuid }]
        mock_license_manager_client().assign_licenses.return_value = {
            'num_successful_assignments': 1,
            'num_already_associated': 0,
            'license_assignments': mock_license_assignments
        }

        license_assignment_results = assign_licenses_task(
            [self.pending_license_request.uuid],
            self.mock_subscription_uuid
        )

        assert mock_lms_client().get_enterprise_learner_data.called_with([self.user.lms_user_id])
        assert mock_license_manager_client().assign_licenses.called_with([self.user.email], self.mock_subscription_uuid)
        self.assertDictEqual(
            license_assignment_results,
            {
                'license_request_uuids': [self.pending_license_request.uuid],
                'learner_data': self.mock_enterprise_learner_data,
                'assigned_licenses': {
                    assignment['user_email']: assignment['license'] for assignment in mock_license_assignments
                },
                'subscription_uuid': self.mock_subscription_uuid
            }
        )

    def test_update_license_requests_after_assignments_task(self):
        mock_license_assignments = [{ 'user_email': self.user.email, 'license': self.mock_license_uuid }]
        license_assignment_results = {
            'license_request_uuids': [self.pending_license_request.uuid],
            'learner_data': { str(key): val for key, val in self.mock_enterprise_learner_data.items() },
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

class TestCouponCodeAssignmentTasks(APITest):
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
            lms_user_id=self.user.lms_user_id,
            state=SubsidyRequestStates.PENDING
        )

        self.mock_coupon_id = 1
        self.mock_coupon_code = 'coupon_code'

    @mock.patch('enterprise_access.apps.api.tasks.LmsApiClient')
    @mock.patch('enterprise_access.apps.api.tasks.EcommerceApiClient')
    def test_assign_coupon_codes_task(self, mock_ecommerce_client, mock_lms_client):
        """
        Verify assign_coupon_codes_task calls Ecommerece to assign new coupon codes.
        """
        mock_lms_client().get_enterprise_learner_data.return_value = self.mock_enterprise_learner_data
        mock_coupon_code_assignments = [{ 'user_email': self.user.email, 'code': self.mock_coupon_code }]
        mock_ecommerce_client().assign_coupon_codes.return_value = {
            'offer_assignments': mock_coupon_code_assignments
        }

        code_assignment_results = assign_coupon_codes_task(
            [self.pending_coupon_code_request.uuid],
            self.mock_coupon_id
        )

        assert mock_lms_client().get_enterprise_learner_data.called_with([self.user.lms_user_id])
        assert mock_ecommerce_client().assign_coupon_codes.called_with([self.user.email], self.mock_coupon_id)

        self.assertDictEqual(
            code_assignment_results,
            {
                'coupon_code_request_uuids': [self.pending_coupon_code_request.uuid],
                'learner_data': self.mock_enterprise_learner_data,
                'assigned_codes': {
                    assignment['user_email']: assignment['code'] for assignment in mock_coupon_code_assignments
                },
                'coupon_id': self.mock_coupon_id
            }
        )

    def test_update_coupon_code_requests_after_assignments_task(self):
        mock_coupon_code_assignments = [{ 'user_email': self.user.email, 'code': self.mock_coupon_code }]
        coupon_code_assignment_results = {
            'coupon_code_request_uuids': [self.pending_coupon_code_request.uuid],
            'learner_data': { str(key): val for key, val in self.mock_enterprise_learner_data.items() },
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
