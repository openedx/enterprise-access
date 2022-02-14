"""
Tests for Enterprise Access API tasks.
"""

from uuid import uuid4

import mock

from enterprise_access.apps.api.tasks import decline_enterprise_subsidy_requests_task
from enterprise_access.apps.subsidy_request.constants import (
    ENTERPRISE_BRAZE_ALIAS_LABEL,
    SubsidyRequestStates,
    SubsidyTypeChoices,
)
from enterprise_access.apps.subsidy_request.models import (
    CouponCodeRequest,
    LicenseRequest,
)
from enterprise_access.apps.subsidy_request.tests.factories import (
    CouponCodeRequestFactory,
    LicenseRequestFactory,
)
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
        for state in self.subsidy_states_to_decline:
            LicenseRequestFactory(
                enterprise_customer_uuid=self.enterprise_customer_uuid_1,
                lms_user_id=self.user.lms_user_id,
                state=state
            )
            CouponCodeRequestFactory(
                enterprise_customer_uuid=self.enterprise_customer_uuid_1,
                lms_user_id=self.user.lms_user_id,
                state=state
            )


    def test_decline_requests_task_coupons(self):
        """
        Verify all coupon subsidies are declined
        """
        assert not CouponCodeRequest.objects.filter(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            lms_user_id=self.user.lms_user_id,
            state=SubsidyRequestStates.DECLINED,
        ).exists()

        decline_enterprise_subsidy_requests_task(
            self.enterprise_customer_uuid_1,
            SubsidyTypeChoices.COUPON,
            False
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

        decline_enterprise_subsidy_requests_task(
            self.enterprise_customer_uuid_1,
            SubsidyTypeChoices.LICENSE,
            False
        )

        assert LicenseRequest.objects.filter(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            lms_user_id=self.user.lms_user_id,
            state=SubsidyRequestStates.DECLINED,
        ).count() == 3

    @mock.patch('enterprise_access.apps.api.tasks.LmsApiClient', return_value=mock.MagicMock())
    @mock.patch('enterprise_access.apps.api.tasks.BrazeApiClient', return_value=mock.MagicMock())
    def test_decline_requests_task_notification_sent_to_user(self, mock_braze_client, mock_lms_client):
        """
        Verify when send_notification is true that we hit braze client with expected args
        """
        user_email = 'example@example.com'

        mock_lms_client().get_enterprise_learner_data.return_value = {
            'user': {
                'email': user_email,
            }
        }

        # Run the task
        decline_enterprise_subsidy_requests_task(
            self.enterprise_customer_uuid_1,
            SubsidyTypeChoices.LICENSE,
            True
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
