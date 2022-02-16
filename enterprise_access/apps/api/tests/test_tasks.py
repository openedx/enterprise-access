"""
Tests for Enterprise Access API tasks.
"""

from uuid import uuid4

import mock

from enterprise_access.apps.api.tasks import (
    decline_enterprise_subsidy_requests_task,
    send_notification_emails_for_requests
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
            'user': {
                'email': user_email,
            }
        }

        # Run the task
        subsidy_request_uuids = [str(request.uuid) for request in self.license_requests]
        send_notification_emails_for_requests(
            subsidy_request_uuids,
            'test-campaign-id',
            SubsidyTypeChoices.LICENSE,
        )

        # Make sure our LMS client got called correct times and with what we expected
        mock_lms_client().get_enterprise_learner_data.called_with(self.user.lms_user_id)
        assert mock_lms_client().get_enterprise_learner_data.call_count == 3

        # And also the same for the Braze Client
        expected_recipient = {
            'attributes': {'email': user_email},
            'user_alias': {
                'alias_label': ENTERPRISE_BRAZE_ALIAS_LABEL,
                'alias_name': user_email,
            },
        }
        mock_braze_client().send_campaign_message.assert_called_with(
            'test-campaign-id',
            recipients=[expected_recipient],
            trigger_properties={}
            )
        assert mock_braze_client().send_campaign_message.call_count == 3
