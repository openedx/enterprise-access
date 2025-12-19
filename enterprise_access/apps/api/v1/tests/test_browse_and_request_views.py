"""
Tests for Enterprise Access Browse and Request app API v1 views.
"""
import random
import time
from unittest import mock
from unittest.mock import patch
from uuid import uuid4

import ddt
from django.conf import settings
from django.test import override_settings
from rest_framework import status
from rest_framework.reverse import reverse

from enterprise_access.apps.content_assignments.constants import LearnerContentAssignmentStateChoices
from enterprise_access.apps.content_assignments.models import LearnerContentAssignment
from enterprise_access.apps.content_assignments.tests.factories import (
    AssignmentConfigurationFactory,
    LearnerContentAssignmentFactory
)
from enterprise_access.apps.core.constants import (
    ALL_ACCESS_CONTEXT,
    SYSTEM_ENTERPRISE_ADMIN_ROLE,
    SYSTEM_ENTERPRISE_LEARNER_ROLE,
    SYSTEM_ENTERPRISE_OPERATOR_ROLE
)
from enterprise_access.apps.subsidy_access_policy.constants import (
    REASON_CONTENT_NOT_IN_CATALOG,
    REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY,
    REASON_POLICY_EXPIRED,
    REASON_POLICY_SPEND_LIMIT_REACHED,
    REASON_SUBSIDY_EXPIRED
)
from enterprise_access.apps.subsidy_access_policy.exceptions import (
    PriceValidationError,
    SubsidyAccessPolicyLockAttemptFailed
)
from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory
)
from enterprise_access.apps.subsidy_request.constants import (
    LearnerCreditRequestActionErrorReasons,
    SegmentEvents,
    SubsidyRequestStates,
    SubsidyTypeChoices
)
from enterprise_access.apps.subsidy_request.models import (
    CouponCodeRequest,
    LearnerCreditRequest,
    LearnerCreditRequestActions,
    LicenseRequest,
    SubsidyRequestCustomerConfiguration
)
from enterprise_access.apps.subsidy_request.tests.factories import (
    CouponCodeRequestFactory,
    LearnerCreditRequestConfigurationFactory,
    LearnerCreditRequestFactory,
    LicenseRequestFactory,
    SubsidyRequestCustomerConfigurationFactory
)
from enterprise_access.apps.subsidy_request.utils import (
    get_action_choice,
    get_error_reason_choice,
    get_user_message_choice
)
from test_utils import APITestWithMocks

from .utils import BaseEnterpriseAccessTestCase

LICENSE_REQUESTS_LIST_ENDPOINT = reverse('api:v1:license-requests-list')
LICENSE_REQUESTS_APPROVE_ENDPOINT = reverse('api:v1:license-requests-approve')
LICENSE_REQUESTS_DECLINE_ENDPOINT = reverse('api:v1:license-requests-decline')
LICENSE_REQUESTS_OVERVIEW_ENDPOINT = reverse('api:v1:license-requests-overview')
COUPON_CODE_REQUESTS_LIST_ENDPOINT = reverse('api:v1:coupon-code-requests-list')
COUPON_CODE_REQUESTS_APPROVE_ENDPOINT = reverse('api:v1:coupon-code-requests-approve')
COUPON_CODE_REQUESTS_DECLINE_ENDPOINT = reverse('api:v1:coupon-code-requests-decline')
CUSTOMER_CONFIGURATIONS_LIST_ENDPOINT = reverse('api:v1:customer-configurations-list')
LEARNER_CREDIT_REQUESTS_LIST_ENDPOINT = reverse('api:v1:learner-credit-requests-list')
LEARNER_CREDIT_REQUESTS_OVERVIEW_ENDPOINT = reverse('api:v1:learner-credit-requests-overview')

# shorthand constant for the path to the browse_and_request views module.
BNR_VIEW_PATH = 'enterprise_access.apps.api.v1.views.browse_and_request'


@ddt.ddt
@override_settings(SEGMENT_KEY='test_key')
class TestLicenseRequestViewSet(BaseEnterpriseAccessTestCase):
    """
    Tests for LicenseRequestViewSet.
    """
    @classmethod
    def setUpTestData(cls):
        # license request with no associations to the user
        cls.other_license_request = LicenseRequestFactory()

    def setUp(self):
        super().setUp()

        if not hasattr(self, '_original_cookies'):
            self.set_jwt_cookie(roles_and_contexts=[{
                'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
                'context': str(self.enterprise_customer_uuid_1),
            }])
            self._original_cookies = self.client.cookies
        else:
            self.client.cookies = self._original_cookies

        # license requests for the user
        self.user_license_request_1 = LicenseRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            user=self.user
        )
        self.user_license_request_2 = LicenseRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_2,
            user=self.user
        )

        # license request under the user's enterprise but not for the user
        self.enterprise_license_request = LicenseRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1
        )

    def test_list_as_enterprise_learner(self):
        """
        Test that an enterprise learner should see all their requests.
        """

        self.set_jwt_cookie(roles_and_contexts=[
            {
                'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
                'context': str(self.enterprise_customer_uuid_1)
            },
            {
                'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
                'context': str(self.enterprise_customer_uuid_2)
            }
        ])

        response = self.client.get(LICENSE_REQUESTS_LIST_ENDPOINT)
        response_json = self.load_json(response.content)
        license_request_uuids = sorted([lr['uuid'] for lr in response_json['results']])
        expected_license_request_uuids = sorted([
            str(self.user_license_request_1.uuid),
            str(self.user_license_request_2.uuid)
        ])
        assert license_request_uuids == expected_license_request_uuids

    def test_list_as_enterprise_admin(self):
        """
        Test that an enterprise admin should see all their requests and requests under their enterprise.
        """

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        response = self.client.get(LICENSE_REQUESTS_LIST_ENDPOINT)
        response_json = self.load_json(response.content)

        license_request_uuids = sorted([lr['uuid'] for lr in response_json['results']])
        expected_license_request_uuids = sorted([
            str(self.user_license_request_1.uuid),
            str(self.user_license_request_2.uuid),
            str(self.enterprise_license_request.uuid)
        ])
        assert license_request_uuids == expected_license_request_uuids

    @ddt.data(
        ('', [choice[0] for choice in SubsidyRequestStates.CHOICES]),  # empty values equate to a skipped filter
        (f'{SubsidyRequestStates.PENDING}', [SubsidyRequestStates.PENDING]),
        (f',{SubsidyRequestStates.DECLINED},', [SubsidyRequestStates.DECLINED]),
        (f'{SubsidyRequestStates.REQUESTED},{SubsidyRequestStates.ERROR}',
            [SubsidyRequestStates.REQUESTED, SubsidyRequestStates.ERROR]
         ),
    )
    @ddt.unpack
    def test_filter_by_states(self, states, expected_states):
        """
        Test that requests can be filtered by a comma-delimited list of states.
        """

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        for state, _ in SubsidyRequestStates.CHOICES:
            LicenseRequestFactory.create_batch(
                random.randint(1, 3),
                enterprise_customer_uuid=self.enterprise_customer_uuid_1,
                user=self.user,
                state=state
            )

        query_params = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'state': states
        }
        response = self.client.get(LICENSE_REQUESTS_LIST_ENDPOINT, query_params)
        response_json = self.load_json(response.content)

        license_request_uuids = sorted([lr['uuid'] for lr in response_json['results']])
        expected_license_request_uuids = [
            str(license_request.uuid) for license_request in LicenseRequest.objects.filter(
                enterprise_customer_uuid=self.enterprise_customer_uuid_1,
                state__in=expected_states
            ).order_by('uuid')
        ]
        assert license_request_uuids == expected_license_request_uuids

    def test_create_no_customer_configuration(self):
        """
        Test that a 422 response is returned when creating a request
        if no customer configuration is set up.
        """
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'course_id': 'edx-demo'
        }
        response = self.client.post(LICENSE_REQUESTS_LIST_ENDPOINT, payload)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.data == ('Customer configuration for enterprise: '
                                 f'{self.enterprise_customer_uuid_1} does not exist.')

    def test_create_subsidy_requests_not_enabled(self):
        """
        Test that a 422 response is returned when creating a request
        if subsidy requests are not enabled
        """

        SubsidyRequestCustomerConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            subsidy_requests_enabled=False
        )
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'course_id': 'edx-demo'
        }
        response = self.client.post(LICENSE_REQUESTS_LIST_ENDPOINT, payload)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.data == f'Subsidy requests for enterprise: {self.enterprise_customer_uuid_1} are disabled.'

    def test_create_subsidy_type_not_set_up(self):
        """
        Test that a 422 response is returned when creating a request if subsidy type is not set up for the enterprise.
        """

        SubsidyRequestCustomerConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            subsidy_requests_enabled=True,
            subsidy_type=None
        )
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'course_id': 'edx-demo'
        }
        response = self.client.post(LICENSE_REQUESTS_LIST_ENDPOINT, payload)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.data == (f'Subsidy request type for enterprise: {self.enterprise_customer_uuid_1} '
                                 'has not been set up.')

    def test_create_subsidy_type_mismatch(self):
        """
        Test that a 422 response is returned when creating a request if the subsidy type does not match
        the one set up by the enterprise.
        """

        SubsidyRequestCustomerConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            subsidy_requests_enabled=True,
            subsidy_type=SubsidyTypeChoices.COUPON
        )
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'course_id': 'edx-demo'
        }
        response = self.client.post(LICENSE_REQUESTS_LIST_ENDPOINT, payload)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.data == f'Subsidy request type must be {SubsidyTypeChoices.COUPON}'

    @ddt.data(SubsidyRequestStates.REQUESTED, SubsidyRequestStates.PENDING)
    def test_create_pending_license_request_exists(self, current_request_state):
        """
        Test that a 422 response is returned when creating a request if the user
        already has a pending license request.
        """
        SubsidyRequestCustomerConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            subsidy_requests_enabled=True,
            subsidy_type=SubsidyTypeChoices.LICENSE
        )
        LicenseRequestFactory(
            user=self.user,
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            state=current_request_state,
        )
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'course_id': 'edx-demo'
        }
        response = self.client.post(LICENSE_REQUESTS_LIST_ENDPOINT, payload)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.data == ('User already has an outstanding license request for enterprise: '
                                 f'{self.enterprise_customer_uuid_1}.')

    def test_create_happy_path(self):
        """
        Test that a license request can be created.
        """
        LicenseRequest.objects.all().delete()

        SubsidyRequestCustomerConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            subsidy_requests_enabled=True,
            subsidy_type=SubsidyTypeChoices.LICENSE
        )
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'course_id': 'edx-demo'
        }
        response = self.client.post(LICENSE_REQUESTS_LIST_ENDPOINT, payload)
        assert response.status_code == status.HTTP_201_CREATED

        self.mock_analytics.assert_called_with(
            user_id=self.user.lms_user_id,
            event=SegmentEvents.LICENSE_REQUEST_CREATED,
            properties=response.data
        )

    def test_create_403(self):
        """
        Test that a 403 response is returned if the user does not belong to the enterprise.
        """
        SubsidyRequestCustomerConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            subsidy_requests_enabled=True,
            subsidy_type=SubsidyTypeChoices.LICENSE
        )
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_2,
            'course_id': 'edx-demo'
        }
        response = self.client.post(LICENSE_REQUESTS_LIST_ENDPOINT, payload)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_approve_no_subsidy_request_uuids(self):
        """ 400 thrown if no subsidy requests provided """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [],
            'subscription_plan_uuid': self.user_license_request_1.subscription_plan_uuid,
        }
        response = self.client.post(LICENSE_REQUESTS_APPROVE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

    def test_approve_invalid_subsidy_request_uuid(self):
        """ 400 thrown if any subsidy request uuids invalid """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.user_license_request_1.uuid, 'hehe-im-not-a-uuid'],
            'subscription_plan_uuid': self.user_license_request_1.subscription_plan_uuid,
        }
        response = self.client.post(LICENSE_REQUESTS_APPROVE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

    def test_approve_no_subscription_plan_uuid(self):
        """ 400 thrown if no subscription plan uuid provided """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.user_license_request_1.uuid],
            'subscription_plan_uuid': '',
        }
        response = self.client.post(LICENSE_REQUESTS_APPROVE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

    def test_approve_invalid_subscription_plan_uuid(self):
        """ 400 thrown if subscription plan uuid invalid """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.user_license_request_1.uuid],
            'subscription_plan_uuid': 'hehe-im-just-a-reggo-string',
        }
        response = self.client.post(LICENSE_REQUESTS_APPROVE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

    @mock.patch(BNR_VIEW_PATH + '.LicenseManagerApiClient.get_subscription_overview')
    def test_approve_not_enough_subs_remaining_in_lm(self, mock_get_sub):
        """ 422 thrown if not enough subs remaining in license """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        mock_get_sub.return_value = [
            {
                'status': 'assigned',
                'count': 13,
            },
            {
                'status': 'unassigned',
                'count': 0,
            },
        ]
        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.user_license_request_1.uuid],
            'subscription_plan_uuid': self.user_license_request_1.subscription_plan_uuid,
        }
        response = self.client.post(LICENSE_REQUESTS_APPROVE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

    @mock.patch(BNR_VIEW_PATH + '.LicenseManagerApiClient.get_subscription_overview')
    def test_approve_subsidy_request_already_declined(self, mock_get_sub):
        """ 422 thrown if any subsidy request in payload already declined """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        mock_get_sub.return_value = [
            {
                'status': 'assigned',
                'count': 13,
            },
            {
                'status': 'unassigned',
                'count': 100000000,
            },
        ]
        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

        self.user_license_request_1.state = SubsidyRequestStates.DECLINED
        self.user_license_request_1.save()
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.user_license_request_1.uuid],
            'subscription_plan_uuid': self.user_license_request_1.subscription_plan_uuid,
        }
        response = self.client.post(LICENSE_REQUESTS_APPROVE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

    @mock.patch(BNR_VIEW_PATH + '.send_notification_email_for_request.si')
    @mock.patch(BNR_VIEW_PATH + '.assign_licenses_task')
    @mock.patch(BNR_VIEW_PATH + '.LicenseManagerApiClient.get_subscription_overview')
    def test_approve_license_request_success(self, mock_get_sub, _, mock_notify):
        """ Test subsidy approval takes place when proper info provided"""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        mock_get_sub.return_value = [
            {
                'status': 'assigned',
                'count': 13,
            },
            {
                'status': 'unassigned',
                'count': 100000000,
            },
        ]
        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.user_license_request_1.uuid, self.enterprise_license_request.uuid],
            'subscription_plan_uuid': self.user_license_request_1.subscription_plan_uuid,
            'send_notification': True,
        }
        response = self.client.post(LICENSE_REQUESTS_APPROVE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_200_OK

        self.user_license_request_1.refresh_from_db()
        self.enterprise_license_request.refresh_from_db()

        assert self.user_license_request_1.state == SubsidyRequestStates.PENDING
        assert self.enterprise_license_request.state == SubsidyRequestStates.PENDING

        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 2

        assert mock_notify.call_count == 2
        mock_notify.assert_has_calls([
            mock.call(
                str(self.user_license_request_1.uuid),
                settings.BRAZE_APPROVE_NOTIFICATION_CAMPAIGN,
                SubsidyTypeChoices.LICENSE
            ),
            mock.call(
                str(self.enterprise_license_request.uuid),
                settings.BRAZE_APPROVE_NOTIFICATION_CAMPAIGN,
                SubsidyTypeChoices.LICENSE
            )
        ], True)

    def test_decline_no_subsidy_request_uuids(self):
        """ 400 thrown if no subsidy requests provided """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.DECLINED
        ).count() == 0

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [],
        }
        response = self.client.post(LICENSE_REQUESTS_DECLINE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.DECLINED
        ).count() == 0

    def test_decline_invalid_subsidy_request_uuid(self):
        """ 400 thrown if any subsidy request uuids invalid """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.DECLINED
        ).count() == 0

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.user_license_request_1.uuid, 'hehe-im-not-a-uuid'],
        }
        response = self.client.post(LICENSE_REQUESTS_DECLINE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.DECLINED
        ).count() == 0

    def test_decline_subsidy_request_already_approved(self):
        """ 422 thrown if any subsidy request in payload already declined """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.DECLINED
        ).count() == 0

        self.user_license_request_1.state = SubsidyRequestStates.PENDING
        self.user_license_request_1.save()
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.user_license_request_1.uuid],
            'subscription_plan_uuid': self.user_license_request_1.subscription_plan_uuid,
        }
        response = self.client.post(LICENSE_REQUESTS_DECLINE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.DECLINED
        ).count() == 0

    def test_decline_request_success(self):
        """ Test 200 returned if successful """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.DECLINED
        ).count() == 0

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.user_license_request_1.uuid],
        }
        response = self.client.post(LICENSE_REQUESTS_DECLINE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_200_OK
        self.user_license_request_1.refresh_from_db()
        assert self.user_license_request_1.state == SubsidyRequestStates.DECLINED

        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.DECLINED
        ).count() == 1

        self.mock_analytics.assert_called_with(
            user_id=self.user_license_request_1.user.lms_user_id,
            event=SegmentEvents.LICENSE_REQUEST_DECLINED,
            properties={
                **response.data[0],
                'unlinked_from_enterprise': False,
                'notification_sent': False
            }
        )

    @mock.patch(BNR_VIEW_PATH + '.send_notification_email_for_request.delay')
    def test_decline_send_notification(self, mock_notify):
        """ Test braze task called if send_notification is True """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.user_license_request_1.uuid],
            'send_notification': True,
        }
        response = self.client.post(LICENSE_REQUESTS_DECLINE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_200_OK
        mock_notify.assert_called_with(
            str(self.user_license_request_1.uuid),
            settings.BRAZE_DECLINE_NOTIFICATION_CAMPAIGN,
            SubsidyTypeChoices.LICENSE,
            {
                'unlinked_from_enterprise': False
            }
        )

    @mock.patch(BNR_VIEW_PATH + '.unlink_users_from_enterprise_task.delay')
    def test_decline_unlink_users(self, mock_unlink_users_from_enterprise_task):
        """ Test unlink_users_from_enterprise_task called if unlink_users_from_enterprise is True """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.user_license_request_1.uuid],
            'send_notification': False,
            'unlink_users_from_enterprise': True
        }
        response = self.client.post(LICENSE_REQUESTS_DECLINE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_200_OK
        mock_unlink_users_from_enterprise_task.assert_called_with(
            str(self.enterprise_customer_uuid_1),
            [self.user_license_request_1.user.lms_user_id],
        )

    def test_overview_superuser_bad_request(self):
        """
        Test that a 400 response is returned if enterprise_customer_uuid
        is not passed in as a query param when called by a superuser.
        """
        self.user.is_superuser = True
        self.user.save()

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE,
            'context': ALL_ACCESS_CONTEXT
        }])

        url = f'{LICENSE_REQUESTS_OVERVIEW_ENDPOINT}'
        response = self.client.get(url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_overview_happy_path(self):
        """
        Test that counts of requests by state is returned.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        LicenseRequest.objects.all().delete()
        for state, _ in SubsidyRequestStates.CHOICES:
            LicenseRequestFactory.create_batch(
                random.randint(1, 5),
                enterprise_customer_uuid=self.enterprise_customer_uuid_1,
                user=self.user,
                state=state
            )

        url = f'{LICENSE_REQUESTS_OVERVIEW_ENDPOINT}?enterprise_customer_uuid={self.enterprise_customer_uuid_1}'
        response = self.client.get(url)

        assert response.status_code == status.HTTP_200_OK
        for overview in response.data:
            state = overview['state']
            count = overview['count']
            assert count == LicenseRequest.objects.filter(
                enterprise_customer_uuid=self.enterprise_customer_uuid_1,
                state=state
            ).count()


@ddt.ddt
@override_settings(SEGMENT_KEY='test_key')
class TestCouponCodeRequestViewSet(BaseEnterpriseAccessTestCase):
    """
    Tests for CouponCodeRequestViewSet.
    """

    def setUp(self):
        super().setUp()

        # coupon code requests for the user
        self.coupon_code_request_1 = CouponCodeRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            user=self.user
        )
        self.coupon_code_request_2 = CouponCodeRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_2,
            user=self.user
        )

        # coupon code request under the user's enterprise but not for the user
        self.enterprise_coupon_code_request = CouponCodeRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1
        )

        # coupon code request with no associations to the user
        self.other_coupon_code_request = CouponCodeRequestFactory()

    def test_list_as_enterprise_learner(self):
        """
        Test that an enterprise learner should see all their requests.
        """

        self.set_jwt_cookie(roles_and_contexts=[
            {
                'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
                'context': str(self.enterprise_customer_uuid_1)
            },
            {
                'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
                'context': str(self.enterprise_customer_uuid_2)
            }
        ])

        response = self.client.get(COUPON_CODE_REQUESTS_LIST_ENDPOINT)
        response_json = self.load_json(response.content)
        coupon_code_request_uuids = sorted([lr['uuid'] for lr in response_json['results']])
        expected_coupon_code_request_uuids = sorted([
            str(self.coupon_code_request_1.uuid),
            str(self.coupon_code_request_2.uuid)
        ])
        assert coupon_code_request_uuids == expected_coupon_code_request_uuids

    def test_list_as_enterprise_admin(self):
        """
        Test that an enterprise admin should see all their requests and requests under their enterprise.
        """

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        response = self.client.get(COUPON_CODE_REQUESTS_LIST_ENDPOINT)
        response_json = self.load_json(response.content)

        coupon_code_request_uuids = sorted([lr['uuid'] for lr in response_json['results']])
        expected_coupon_code_request_uuids = sorted([
            str(self.coupon_code_request_1.uuid),
            str(self.coupon_code_request_2.uuid),
            str(self.enterprise_coupon_code_request.uuid)
        ])
        assert coupon_code_request_uuids == expected_coupon_code_request_uuids

    @ddt.data(
        (True, False),
        (False, True),
        (True, True)
    )
    @ddt.unpack
    def test_list_as_staff_or_superuser(self, is_staff, is_superuser):
        """
        Test that a staff/superuser should see all requests.
        """
        self.user.is_staff = is_staff
        self.user.is_superuser = is_superuser

        self.user.save()

        response = self.client.get(COUPON_CODE_REQUESTS_LIST_ENDPOINT)
        response_json = self.load_json(response.content)
        coupon_code_request_uuids = sorted([lr['uuid'] for lr in response_json['results']])
        expected_coupon_code_request_uuids = sorted([
            str(self.coupon_code_request_1.uuid),
            str(self.coupon_code_request_2.uuid),
            str(self.enterprise_coupon_code_request.uuid),
            str(self.other_coupon_code_request.uuid)
        ])
        assert coupon_code_request_uuids == expected_coupon_code_request_uuids

    def test_list_as_openedx_operator(self):
        """
        Test that an openedx operator should see all requests.
        """

        self.set_jwt_cookie(roles_and_contexts=[
            {
                'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE,
                'context': '*'
            },
        ])

        response = self.client.get(COUPON_CODE_REQUESTS_LIST_ENDPOINT)
        response_json = self.load_json(response.content)
        coupon_code_request_uuids = sorted([lr['uuid'] for lr in response_json['results']])
        expected_coupon_code_request_uuids = sorted([
            str(self.coupon_code_request_1.uuid),
            str(self.coupon_code_request_2.uuid),
            str(self.enterprise_coupon_code_request.uuid),
            str(self.other_coupon_code_request.uuid)
        ])
        assert coupon_code_request_uuids == expected_coupon_code_request_uuids

    def test_create_pending_coupon_code_request_exists(self):
        """
        Test that a 422 response is returned when creating a request if the user
        already has a pending coupon code request for the course.
        """

        SubsidyRequestCustomerConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            subsidy_requests_enabled=True,
            subsidy_type=SubsidyTypeChoices.COUPON
        )
        CouponCodeRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            user=self.user,
            course_id='edx-demo'
        )
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'course_id': 'edx-demo'
        }
        response = self.client.post(COUPON_CODE_REQUESTS_LIST_ENDPOINT, payload)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.data == ('User already has an outstanding coupon code request for course: edx-demo '
                                 f'under enterprise: {self.enterprise_customer_uuid_1}.')

    def test_create_happy_path(self):
        """
        Test that a coupon code request can be created.
        """
        SubsidyRequestCustomerConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            subsidy_requests_enabled=True,
            subsidy_type=SubsidyTypeChoices.COUPON
        )
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'course_id': 'edx-demo'
        }
        response = self.client.post(COUPON_CODE_REQUESTS_LIST_ENDPOINT, payload)
        assert response.status_code == status.HTTP_201_CREATED

        self.mock_analytics.assert_called_with(
            user_id=self.user.lms_user_id,
            event=SegmentEvents.COUPON_CODE_REQUEST_CREATED,
            properties=response.data
        )

    def test_approve_no_subsidy_request_uuids(self):
        """ 400 thrown if no subsidy requests provided """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [],
            'coupon_id': self.coupon_code_request_1.coupon_id,
        }
        response = self.client.post(COUPON_CODE_REQUESTS_APPROVE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

    def test_approve_invalid_subsidy_request_uuid(self):
        """ 400 thrown if any subsidy request uuids invalid """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.coupon_code_request_1.uuid, 'lol-not-a-uuid'],
            'coupon_id': self.coupon_code_request_1.coupon_id,
        }
        response = self.client.post(COUPON_CODE_REQUESTS_APPROVE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

    @mock.patch(BNR_VIEW_PATH + '.EcommerceApiClient.get_coupon_overview')
    def test_approve_not_enough_codes_left_in_coupon(self, mock_get_coupon):
        """ 422 thrown if not enough codes remaining in coupon """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        mock_get_coupon.return_value = {
            "id": 123,
            "title": "Test coupon",
            "start_date": "2022-01-06T00:00:00Z",
            "end_date": "2023-05-31T00:00:00Z",
            "num_uses": 0,
            "usage_limitation": "Multi-use",
            "num_codes": 100,
            "max_uses": 200,
            "num_unassigned": 0,
            "errors": [],
            "available": True
        }
        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.coupon_code_request_1.uuid],
            'coupon_id': self.coupon_code_request_1.coupon_id,
        }
        response = self.client.post(COUPON_CODE_REQUESTS_APPROVE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

    @mock.patch(BNR_VIEW_PATH + '.EcommerceApiClient.get_coupon_overview')
    def test_approve_subsidy_request_already_declined(self, mock_get_coupon):
        """ 422 thrown if any subsidy request in payload already declined """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        mock_get_coupon.return_value = {"num_unassigned": 1000000000}
        self.coupon_code_request_1.state = SubsidyRequestStates.DECLINED
        self.coupon_code_request_1.save()
        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.coupon_code_request_1.uuid],
            'coupon_id': self.coupon_code_request_1.coupon_id,
        }
        response = self.client.post(COUPON_CODE_REQUESTS_APPROVE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

    @mock.patch(BNR_VIEW_PATH + '.send_notification_email_for_request.si')
    @mock.patch(BNR_VIEW_PATH + '.assign_coupon_codes_task')
    @mock.patch(BNR_VIEW_PATH + '.EcommerceApiClient.get_coupon_overview')
    def test_approve_coupon_code_request_success(self, mock_get_coupon, _, mock_notify):
        """ Test subsidy approval takes place when proper info provided"""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        mock_get_coupon.return_value = {'num_unassigned': 1000000000}
        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.coupon_code_request_1.uuid],
            'coupon_id': self.coupon_code_request_1.coupon_id,
            'send_notification': True,
        }
        response = self.client.post(COUPON_CODE_REQUESTS_APPROVE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_200_OK
        self.coupon_code_request_1.refresh_from_db()

        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 1

        mock_notify.assert_called_with(
            str(self.coupon_code_request_1.uuid),
            settings.BRAZE_APPROVE_NOTIFICATION_CAMPAIGN,
            SubsidyTypeChoices.COUPON,
        )

    def test_decline_no_subsidy_request_uuids(self):
        """ 400 thrown if no subsidy requests provided """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.DECLINED
        ).count() == 0

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [],
        }
        response = self.client.post(COUPON_CODE_REQUESTS_DECLINE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.DECLINED
        ).count() == 0

    def test_decline_invalid_subsidy_request_uuid(self):
        """ 400 thrown if any subsidy request uuids invalid """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.DECLINED
        ).count() == 0

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.coupon_code_request_1.uuid, 'hehe-im-not-a-uuid'],
        }
        response = self.client.post(COUPON_CODE_REQUESTS_DECLINE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.DECLINED
        ).count() == 0

    def test_decline_subsidy_request_already_approved(self):
        """ 422 thrown if any subsidy request in payload already approved """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        self.coupon_code_request_1.state = SubsidyRequestStates.PENDING
        self.coupon_code_request_1.save()
        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.DECLINED
        ).count() == 0

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.coupon_code_request_1.uuid],
            'coupon_id': self.coupon_code_request_1.coupon_id,
        }
        response = self.client.post(COUPON_CODE_REQUESTS_DECLINE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.DECLINED
        ).count() == 0

    def test_decline_request_success(self):
        """ Test 200 returned if successful """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.DECLINED
        ).count() == 0

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.coupon_code_request_1.uuid],
        }
        response = self.client.post(COUPON_CODE_REQUESTS_DECLINE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_200_OK
        self.coupon_code_request_1.refresh_from_db()
        assert self.coupon_code_request_1.state == SubsidyRequestStates.DECLINED

        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.DECLINED
        ).count() == 1

        self.mock_analytics.assert_called_with(
            user_id=self.coupon_code_request_1.user.lms_user_id,
            event=SegmentEvents.COUPON_CODE_REQUEST_DECLINED,
            properties={
                **response.data[0],
                'unlinked_from_enterprise': False,
                'notification_sent': False
            }
        )

    @mock.patch(BNR_VIEW_PATH + '.send_notification_email_for_request.delay')
    def test_decline_send_notification(self, mock_notify):
        """ Test braze task called if send_notification is True """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.coupon_code_request_1.uuid],
            'send_notification': True,
        }
        response = self.client.post(COUPON_CODE_REQUESTS_DECLINE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_200_OK
        mock_notify.assert_called_with(
            str(self.coupon_code_request_1.uuid),
            settings.BRAZE_DECLINE_NOTIFICATION_CAMPAIGN,
            SubsidyTypeChoices.COUPON,
            {
                'unlinked_from_enterprise': False
            }
        )

    @mock.patch(BNR_VIEW_PATH + '.unlink_users_from_enterprise_task.delay')
    def test_decline_unlink_users(self, mock_unlink_users_from_enterprise_task):
        """ Test unlink_users_from_enterprise_task called if unlink_users_from_enterprise is True """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.coupon_code_request_1.uuid],
            'send_notification': False,
            'unlink_users_from_enterprise': True
        }
        response = self.client.post(COUPON_CODE_REQUESTS_DECLINE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_200_OK
        mock_unlink_users_from_enterprise_task.assert_called_with(
            str(self.enterprise_customer_uuid_1),
            [self.coupon_code_request_1.user.lms_user_id],
        )


@ddt.ddt
@override_settings(SEGMENT_KEY='test_key')
class TestSubsidyRequestCustomerConfigurationViewSet(APITestWithMocks):
    """
    Tests for SubsidyRequestCustomerConfigurationViewSet.
    """

    enterprise_customer_uuid_1 = uuid4()
    enterprise_customer_uuid_2 = uuid4()

    def setUp(self):
        super().setUp()
        self.set_jwt_cookie([
            {
                'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE,
                'context': ALL_ACCESS_CONTEXT
            }
        ])
        self.customer_configuration_1 = SubsidyRequestCustomerConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1
        )
        self.customer_configuration_2 = SubsidyRequestCustomerConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_2
        )

    @ddt.data(
        [{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': enterprise_customer_uuid_2
        }],
        [{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': enterprise_customer_uuid_1
        }]
    )
    def test_create_403(self, roles_and_contexts):
        """
        Test that a 403 response is returned if the user is not an admin of the enterprise.
        """
        self.set_jwt_cookie(roles_and_contexts)
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
        }
        response = self.client.post(CUSTOMER_CONFIGURATIONS_LIST_ENDPOINT, payload)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_create_happy_path(self):
        """
        Test that a customer configuration can be created.
        """
        SubsidyRequestCustomerConfiguration.objects.all().delete()

        self.set_jwt_cookie(roles_and_contexts=[
            {
                'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
                'context': self.enterprise_customer_uuid_1
            },
        ])
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_requests_enabled': True,
            'subsidy_type': SubsidyTypeChoices.COUPON
        }
        response = self.client.post(CUSTOMER_CONFIGURATIONS_LIST_ENDPOINT, payload)
        assert response.status_code == status.HTTP_201_CREATED

        customer_configuration = SubsidyRequestCustomerConfiguration.objects.get(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1
        )
        assert customer_configuration.subsidy_requests_enabled
        assert customer_configuration.subsidy_type == SubsidyTypeChoices.COUPON

        self.mock_analytics.assert_called_with(
            user_id=self.user.lms_user_id,
            event=SegmentEvents.SUBSIDY_REQUEST_CONFIGURATION_CREATED,
            properties=response.data
        )

    @ddt.data(
        (True, False),
        (False, True),
        (True, True)
    )
    @ddt.unpack
    def test_list_as_staff_or_superuser(self, is_staff, is_superuser):
        self.user.is_staff = is_staff
        self.user.is_superuser = is_superuser

        self.user.save()

        response = self.client.get(CUSTOMER_CONFIGURATIONS_LIST_ENDPOINT)
        response_json = self.load_json(response.content)

        configuration_enterprise_customer_uuids = sorted(
            [lr['enterprise_customer_uuid'] for lr in response_json['results']]
        )
        expected_configuration_enterprise_customer_uuids = sorted([
            str(self.customer_configuration_1.enterprise_customer_uuid),
            str(self.customer_configuration_2.enterprise_customer_uuid),
        ])

        assert configuration_enterprise_customer_uuids == expected_configuration_enterprise_customer_uuids

    @ddt.data(
        [{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': enterprise_customer_uuid_1
        }],
        [{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': enterprise_customer_uuid_1
        }]
    )
    def test_list_as_admin_or_learner(self, roles_and_contexts):
        self.set_jwt_cookie(roles_and_contexts)

        response = self.client.get(CUSTOMER_CONFIGURATIONS_LIST_ENDPOINT)
        response_json = self.load_json(response.content)

        configuration_enterprise_customer_uuids = [lr['enterprise_customer_uuid'] for lr in response_json['results']]
        expected_configuration_enterprise_customer_uuids = [str(self.customer_configuration_1.enterprise_customer_uuid)]

        assert configuration_enterprise_customer_uuids == expected_configuration_enterprise_customer_uuids

    @ddt.data(
        [{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': enterprise_customer_uuid_2
        }],
        [{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': enterprise_customer_uuid_1
        }]
    )
    def test_partial_update_403(self, roles_and_contexts):
        """
        Test that a 403 response is returned if the user is not an admin of the enterprise.
        """

        self.set_jwt_cookie(roles_and_contexts)

        response = self.client.patch(f'{CUSTOMER_CONFIGURATIONS_LIST_ENDPOINT}{self.enterprise_customer_uuid_1}/', {})
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_partial_update_happy_path(self):
        """
        Test that a customer configuration can be patched.
        """

        self.set_jwt_cookie(roles_and_contexts=[
            {
                'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
                'context': self.enterprise_customer_uuid_1
            },
        ])

        customer_config = SubsidyRequestCustomerConfiguration.objects.get(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1
        )

        customer_config.subsidy_requests_enabled = False
        customer_config.subsidy_type = None
        customer_config.save()

        payload = {
            'subsidy_requests_enabled': True,
            'subsidy_type': SubsidyTypeChoices.COUPON,
            'send_notification': False,
        }
        response = self.client.patch(
            f'{CUSTOMER_CONFIGURATIONS_LIST_ENDPOINT}{self.enterprise_customer_uuid_1}/',
            payload
        )
        assert response.status_code == status.HTTP_200_OK

        customer_config = SubsidyRequestCustomerConfiguration.objects.get(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1
        )

        assert customer_config.subsidy_requests_enabled
        assert customer_config.subsidy_type == SubsidyTypeChoices.COUPON

        self.mock_analytics.assert_called_with(
            user_id=self.user.lms_user_id,
            event=SegmentEvents.SUBSIDY_REQUEST_CONFIGURATION_UPDATED,
            properties=response.data
        )

    @mock.patch('enterprise_access.apps.api.tasks.decline_enterprise_subsidy_requests_task.si')
    @ddt.data(
        (SubsidyTypeChoices.LICENSE, LicenseRequest, SubsidyTypeChoices.COUPON),
        (SubsidyTypeChoices.COUPON, CouponCodeRequest, SubsidyTypeChoices.LICENSE),
        (SubsidyTypeChoices.LICENSE, LicenseRequest, None),
        (SubsidyTypeChoices.COUPON, CouponCodeRequest, None),
    )
    @ddt.unpack
    def test_partial_update_declines_old_requests(
        self,
        previous_subsidy_type,
        previous_subsidy_object_type,
        new_subsidy_type,
        mock_decline_enterprise_subsidy_requests_task
    ):
        """
        Test that old requests are declined if the subsidy type changes.
        """

        self.set_jwt_cookie(roles_and_contexts=[
            {
                'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
                'context': self.enterprise_customer_uuid_1
            },
        ])

        customer_config = SubsidyRequestCustomerConfiguration.objects.get(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1
        )

        customer_config.subsidy_requests_enabled = False
        customer_config.subsidy_type = previous_subsidy_type
        customer_config.save()

        expected_declined_subsidy = previous_subsidy_object_type.objects.create(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            state=SubsidyRequestStates.REQUESTED,
            user=self.user,
        )

        payload = {
            'subsidy_requests_enabled': True,
            'subsidy_type': new_subsidy_type,
            'send_notification': False,
        }
        response = self.client.patch(
            f'{CUSTOMER_CONFIGURATIONS_LIST_ENDPOINT}{self.enterprise_customer_uuid_1}/',
            payload
        )
        assert response.status_code == status.HTTP_200_OK

        mock_decline_enterprise_subsidy_requests_task.assert_called_with(
            [str(expected_declined_subsidy.uuid)],
            previous_subsidy_type,
        )

    @mock.patch('enterprise_access.apps.api.tasks.send_notification_email_for_request.si')
    @mock.patch('enterprise_access.apps.api.tasks.decline_enterprise_subsidy_requests_task.si')
    @ddt.data(
        (SubsidyTypeChoices.LICENSE, LicenseRequest, SubsidyTypeChoices.COUPON),
    )
    @ddt.unpack
    def test_partial_update_send_notification(
        self,
        previous_subsidy_type,
        previous_subsidy_object_type,
        new_subsidy_type,
        mock_decline_enterprise_subsidy_requests_task,
        mock_send_notification_email_for_request,
    ):
        """
        Test that partial_updates runs send_notification task with correct args
        """

        self.set_jwt_cookie(roles_and_contexts=[
            {
                'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
                'context': self.enterprise_customer_uuid_1
            },
        ])

        customer_config = SubsidyRequestCustomerConfiguration.objects.get(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1
        )

        customer_config.subsidy_requests_enabled = False
        customer_config.subsidy_type = previous_subsidy_type
        customer_config.save()

        expected_declined_subsidy = previous_subsidy_object_type.objects.create(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            state=SubsidyRequestStates.REQUESTED,
            user=self.user,
        )

        payload = {
            'subsidy_requests_enabled': True,
            'subsidy_type': new_subsidy_type,
            'send_notification': True,
        }
        response = self.client.patch(
            f'{CUSTOMER_CONFIGURATIONS_LIST_ENDPOINT}{self.enterprise_customer_uuid_1}/',
            payload
        )
        assert response.status_code == status.HTTP_200_OK

        mock_decline_enterprise_subsidy_requests_task.assert_called_once()
        mock_send_notification_email_for_request.assert_called_with(
            str(expected_declined_subsidy.uuid),
            'test-campaign-id',
            previous_subsidy_type,
        )

    @mock.patch('enterprise_access.apps.api.tasks.send_notification_email_for_request.si')
    @mock.patch('enterprise_access.apps.api.tasks.decline_enterprise_subsidy_requests_task.si')
    @ddt.data(
        (None, SubsidyTypeChoices.LICENSE),
    )
    @ddt.unpack
    def test_partial_update_no_tasks(
        self,
        previous_subsidy_type,
        new_subsidy_type,
        mock_decline_enterprise_subsidy_requests_task,
        mock_send_notification_email_for_request,
    ):
        """
        Test that partial_updates runs no tasks if subsidy_type hasn't been set yet.
        """

        self.set_jwt_cookie(roles_and_contexts=[
            {
                'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
                'context': self.enterprise_customer_uuid_1
            },
        ])

        customer_config = SubsidyRequestCustomerConfiguration.objects.get(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1
        )

        customer_config.subsidy_requests_enabled = False
        customer_config.subsidy_type = previous_subsidy_type
        customer_config.save()

        payload = {
            'subsidy_requests_enabled': True,
            'subsidy_type': new_subsidy_type,
            'send_notification': True,
        }
        response = self.client.patch(
            f'{CUSTOMER_CONFIGURATIONS_LIST_ENDPOINT}{self.enterprise_customer_uuid_1}/',
            payload
        )
        assert response.status_code == status.HTTP_200_OK

        mock_decline_enterprise_subsidy_requests_task.assert_not_called()
        mock_send_notification_email_for_request.assert_not_called()


@ddt.ddt
@override_settings(SEGMENT_KEY='test_key')
class TestLearnerCreditRequestViewSet(BaseEnterpriseAccessTestCase):
    """
    Tests for LearnerCreditRequestViewSet.
    """

    def setUp(self):
        super().setUp()

        # Setup a subsidy client
        subsidy_client_patcher = patch.object(
            SubsidyAccessPolicy, 'subsidy_client'
        )
        self.mock_subsidy_client = subsidy_client_patcher.start()

        # Setup test policy and request config and assignment config
        self.learner_credit_config = LearnerCreditRequestConfigurationFactory(active=True)
        self.assignment_config = AssignmentConfigurationFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
        )
        self.policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            learner_credit_request_config=self.learner_credit_config,
            assignment_configuration=self.assignment_config,
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            active=True,
            retired=False,
            per_learner_spend_limit=0,  # For B&R budget, limit should be set to 0.
            spend_limit=4000,
        )

        # Setup test requests
        self.user_request_1 = LearnerCreditRequestFactory(
            course_id='edx-demo',
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            user=self.user,
            learner_credit_request_config=self.learner_credit_config,
            course_price=1000,
            state=SubsidyRequestStates.REQUESTED,
            assignment=None
        )
        self.user_request_2 = LearnerCreditRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_2,
            user=self.user,
            learner_credit_request_config=self.learner_credit_config,
        )
        self.enterprise_request = LearnerCreditRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            learner_credit_request_config=self.learner_credit_config,
        )

        # LearnerCreditrequest with no associations to the user and enterprise
        self.other_learner_credit_request = LearnerCreditRequestFactory()

        # Set up existing assignments (approved requests)
        self.assignment_1 = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_config,
            content_quantity=-500,
            state='allocated'
        )
        self.assignment_2 = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_config,
            content_quantity=-500,
            state='allocated'
        )

        # Set up existing transactions
        self.mock_transaction_record_1 = {
            'uuid': str(uuid4()),
            'state': "committed",
            'content_key': 'something',
            'subsidy_access_policy_uuid': str(self.policy.uuid),
            'quantity': -500,
            'other': True,
        }
        self.mock_transaction_record_2 = {
            'uuid': str(uuid4()),
            'state': "committed",
            'content_key': 'something',
            'subsidy_access_policy_uuid': str(self.policy.uuid),
            'quantity': -500,
            'other': True,
        }

        # cleanups
        self.addCleanup(subsidy_client_patcher.stop)

    def test_list_as_enterprise_learner(self):
        """
        Test that an enterprise learner sees only their own requests.
        """
        self.set_jwt_cookie(roles_and_contexts=[
            {
                'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
                'context': str(self.enterprise_customer_uuid_1)
            },
            {
                'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
                'context': str(self.enterprise_customer_uuid_2)
            }
        ])

        response = self.client.get(LEARNER_CREDIT_REQUESTS_LIST_ENDPOINT)
        response_json = self.load_json(response.content)

        request_uuids = sorted([lr['uuid'] for lr in response_json['results']])
        expected_uuids = sorted([
            str(self.user_request_1.uuid),
            str(self.user_request_2.uuid)
        ])
        assert request_uuids == expected_uuids

    def test_list_as_enterprise_admin(self):
        """
        Test that an enterprise admin sees all requests for their enterprise.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        response = self.client.get(LEARNER_CREDIT_REQUESTS_LIST_ENDPOINT)
        response_json = self.load_json(response.content)

        request_uuids = sorted([lr['enterprise_customer_uuid'] for lr in response_json['results']])
        expected_uuids = sorted([
            str(self.user_request_1.enterprise_customer_uuid),
            str(self.user_request_2.enterprise_customer_uuid),
            str(self.enterprise_request.enterprise_customer_uuid)
        ])
        assert request_uuids == expected_uuids

    @ddt.data(
        ('', [choice[0] for choice in SubsidyRequestStates.CHOICES]),
        (f'{SubsidyRequestStates.PENDING}', [SubsidyRequestStates.PENDING]),
        (f',{SubsidyRequestStates.DECLINED},', [SubsidyRequestStates.DECLINED]),
        (f'{SubsidyRequestStates.REQUESTED},{SubsidyRequestStates.ERROR}',
            [SubsidyRequestStates.REQUESTED, SubsidyRequestStates.ERROR]),
    )
    @ddt.unpack
    def test_filter_by_states(self, states, expected_states):
        """
        Test filtering requests by state.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        # Create requests in various states
        for state, _ in SubsidyRequestStates.CHOICES:
            LearnerCreditRequestFactory(
                enterprise_customer_uuid=self.enterprise_customer_uuid_1,
                user=self.user,
                state=state,
                learner_credit_request_config=self.learner_credit_config
            )

        query_params = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'state': states
        }
        response = self.client.get(LEARNER_CREDIT_REQUESTS_LIST_ENDPOINT, query_params)
        response_json = self.load_json(response.content)

        request_uuids = sorted([lr['uuid'] for lr in response_json['results']])
        expected_uuids = sorted([
            str(req.uuid) for req in LearnerCreditRequest.objects.filter(
                enterprise_customer_uuid=self.enterprise_customer_uuid_1,
                state__in=expected_states
            ).order_by('uuid')
        ])
        assert request_uuids == expected_uuids

    @ddt.data(
        'state_sort_order',
    )
    def test_list_ordering_by_state_sort_order(self, ordering_key):
        """
        Verify that the list view can be sorted by the custom state priority.
        The expected order is REQUESTED, DECLINED, CANCELLED, then others.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        # Create requests in a non-sorted order to ensure sorting is effective.
        req_approved = LearnerCreditRequestFactory(
            state=SubsidyRequestStates.APPROVED, enterprise_customer_uuid=self.enterprise_customer_uuid_1
        )
        req_declined = LearnerCreditRequestFactory(
            state=SubsidyRequestStates.DECLINED, enterprise_customer_uuid=self.enterprise_customer_uuid_1
        )
        req_requested = LearnerCreditRequestFactory(
            state=SubsidyRequestStates.REQUESTED, enterprise_customer_uuid=self.enterprise_customer_uuid_1
        )
        req_cancelled = LearnerCreditRequestFactory(
            state=SubsidyRequestStates.CANCELLED, enterprise_customer_uuid=self.enterprise_customer_uuid_1
        )

        # Make the API call with the specified ordering
        response = self.client.get(
            LEARNER_CREDIT_REQUESTS_LIST_ENDPOINT,
            {'ordering': ordering_key}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = response.json()['results']
        # Extract UUIDs from the response to check the order
        actual_uuids = [item['uuid'] for item in results]

        # Define the expected order of UUIDs based on your business logic
        expected_order = [
            str(req_requested.uuid),
            str(req_declined.uuid),
            str(req_cancelled.uuid),
            str(req_approved.uuid),
        ]

        # Filter the actual UUIDs to only include the ones we created for this test.
        filtered_actual_uuids = [uuid for uuid in actual_uuids if uuid in expected_order]

        self.assertEqual(filtered_actual_uuids, expected_order)

    def test_create_missing_policy_uuid(self):
        """
        Test that policy_uuid is required.
        """
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'course_id': 'course-v1:edX+DemoX+Demo_Course'
        }
        response = self.client.post(LEARNER_CREDIT_REQUESTS_LIST_ENDPOINT, payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {'detail': 'policy_uuid is required.'}

    def test_create_bnr_not_enabled(self):
        """
        Test that request creation fails when BNR is not active for the policy.
        """
        disabled_config = LearnerCreditRequestConfigurationFactory(active=False)
        disabled_policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            learner_credit_request_config=disabled_config,
            enterprise_customer_uuid=self.enterprise_customer_uuid_1
        )

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'course_id': 'course-v1:edX+DemoX+Demo_Course',
            'policy_uuid': disabled_policy.uuid,
            'course_price': 1000
        }
        response = self.client.post(LEARNER_CREDIT_REQUESTS_LIST_ENDPOINT, payload)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {
            'detail': f'Browse & Request is not active for policy UUID: {disabled_policy.uuid}.'
        }

    def test_create_duplicate_request(self):
        """
        Test that duplicate pending requests are prevented.
        """
        # Create initial request
        LearnerCreditRequestFactory(
            user=self.user,
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            course_id='course-v1:edX+DemoX+Demo_Course',
            state=SubsidyRequestStates.REQUESTED,
            learner_credit_request_config=self.learner_credit_config
        )

        # Try to create duplicate
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'course_id': 'course-v1:edX+DemoX+Demo_Course',
            'policy_uuid': self.policy.uuid,
            'course_price': 1000
        }
        response = self.client.post(LEARNER_CREDIT_REQUESTS_LIST_ENDPOINT, payload)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.data == {
            "detail": "You already have an active learner credit request for course course-v1:edX+DemoX+Demo_Course "
            f"under policy UUID: {self.policy.uuid}."
        }

    def test_create_success(self):
        """
        Test successful request creation.
        """
        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'course_id': 'course-v1:edX+DemoX+Demo_Course',
            'policy_uuid': self.policy.uuid,
            'course_price': 1000
        }
        response = self.client.post(LEARNER_CREDIT_REQUESTS_LIST_ENDPOINT, payload)
        assert response.status_code == status.HTTP_201_CREATED

        # Verify the request was created with correct fields
        request = LearnerCreditRequest.objects.get(uuid=response.data['uuid'])
        action = LearnerCreditRequestActions.objects.filter(
            learner_credit_request=request,
            recent_action='requested'
        ).first()
        assert request.user == self.user
        assert request.enterprise_customer_uuid == self.enterprise_customer_uuid_1
        assert request.course_id == 'course-v1:edX+DemoX+Demo_Course'
        assert request.state == SubsidyRequestStates.REQUESTED
        assert request.learner_credit_request_config == self.learner_credit_config
        assert request.course_price == 1000
        assert action is not None

    @ddt.data(
        SubsidyRequestStates.CANCELLED,
        SubsidyRequestStates.EXPIRED,
        SubsidyRequestStates.REVERSED,
    )
    @mock.patch(BNR_VIEW_PATH + '.send_learner_credit_bnr_admins_email_with_new_requests_task.delay')
    def test_create_reuse_existing_request_success(self, reusable_state, mock_email_task):
        """
        Test that an existing request in reusable states (CANCELLED, EXPIRED, REVERSED)
        gets reused instead of creating a new one.
        """
        course_id = 'course-v1:edX+DemoX+Demo_Course'
        original_price = 1000
        new_price = 1500

        # Create an existing assignment for the request
        existing_assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_config,
            content_key=course_id,
            learner_email=self.user.email,
            lms_user_id=self.user.lms_user_id,
            state='allocated',
            content_quantity=-original_price
        )

        # Create an existing request in reusable state with assignment
        existing_request = LearnerCreditRequestFactory(
            user=self.user,
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            course_id=course_id,
            course_price=original_price,
            state=reusable_state,
            learner_credit_request_config=self.learner_credit_config,
            assignment=existing_assignment,
            reviewer=self.user,  # Set reviewer to verify it gets cleared
        )

        # Set reviewed_at to verify it gets cleared
        existing_request.reviewed_at = '2023-01-01T00:00:00Z'
        existing_request.save()

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'course_id': course_id,
            'policy_uuid': self.policy.uuid,
            'course_price': new_price
        }

        # Should have only one request before the API call
        initial_count = LearnerCreditRequest.objects.filter(
            user=self.user,
            course_id=course_id,
            learner_credit_request_config=self.learner_credit_config
        ).count()
        assert initial_count == 1

        response = self.client.post(LEARNER_CREDIT_REQUESTS_LIST_ENDPOINT, payload)

        # Should return 200 OK for reuse instead of 201 CREATED
        assert response.status_code == status.HTTP_200_OK
        assert response.data['uuid'] == str(existing_request.uuid)

        # Should still have only one request (reused, not created new)
        final_count = LearnerCreditRequest.objects.filter(
            user=self.user,
            course_id=course_id,
            learner_credit_request_config=self.learner_credit_config
        ).count()
        assert final_count == 1

        # Verify the existing request was properly reset
        existing_request.refresh_from_db()
        assert existing_request.state == SubsidyRequestStates.REQUESTED
        assert existing_request.assignment is None  # Assignment should be cleared
        assert existing_request.course_price == new_price  # Price should be updated
        assert existing_request.reviewer is None  # Reviewer should be cleared
        assert existing_request.reviewed_at is None  # Reviewed_at should be cleared

        # Verify action was created for the reused request
        action = LearnerCreditRequestActions.objects.filter(
            learner_credit_request=existing_request,
            recent_action=get_action_choice(SubsidyRequestStates.REQUESTED)
        ).first()
        assert action is not None
        assert action.status == get_user_message_choice(SubsidyRequestStates.REQUESTED)

        # Verify email notification task was called
        mock_email_task.assert_called_once_with(
            str(self.policy.uuid),
            str(self.policy.learner_credit_request_config.uuid),
            str(existing_request.enterprise_customer_uuid)
        )

    def test_overview_happy_path(self):
        """
        Test the overview endpoint returns correct state counts.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        # Clear existing and create test requests
        LearnerCreditRequest.objects.all().delete()
        for state, _ in SubsidyRequestStates.CHOICES:
            LearnerCreditRequestFactory.create_batch(
                random.randint(1, 3),
                enterprise_customer_uuid=self.enterprise_customer_uuid_1,
                user=self.user,
                state=state,
                learner_credit_request_config=self.learner_credit_config
            )

        url = f'{LEARNER_CREDIT_REQUESTS_OVERVIEW_ENDPOINT}?enterprise_customer_uuid={self.enterprise_customer_uuid_1}'
        response = self.client.get(url)

        assert response.status_code == status.HTTP_200_OK
        for overview in response.data:
            state = overview['state']
            count = overview['count']
            assert count == LearnerCreditRequest.objects.filter(
                enterprise_customer_uuid=self.enterprise_customer_uuid_1,
                state=state
            ).count()

    def test_overview_missing_enterprise_param(self):
        """
        Test overview requires enterprise_customer_uuid param.
        """
        # Need to be an admin to access overview
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        response = self.client.get(LEARNER_CREDIT_REQUESTS_OVERVIEW_ENDPOINT)
        # Depending on implementation, this could be 400 or 403
        # Check that it's one of the expected status codes
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN]

    @mock.patch('enterprise_access.apps.api.v1.views.browse_and_request.get_enterprise_uuid_from_request_data')
    def test_decline_success(self, mock_get_enterprise_uuid):
        """
        Test successful decline of a learner credit request.
        """
        # Make sure the mock returns the UUID consistently
        mock_get_enterprise_uuid.return_value = str(self.enterprise_customer_uuid_1)

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-decline')
        data = {
            'enterprise_customer_uuid': str(self.enterprise_customer_uuid_1),
            'subsidy_request_uuid': str(self.user_request_1.uuid),
            'send_notification': False,
            'disassociate_from_org': False
        }

        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_200_OK

        # Verify request was declined
        self.user_request_1.refresh_from_db()
        assert self.user_request_1.state == SubsidyRequestStates.DECLINED
        assert self.user_request_1.reviewer == self.user

    @mock.patch('enterprise_access.apps.api.v1.views.browse_and_request.get_enterprise_uuid_from_request_data')
    def test_decline_with_invalid_uuid(self, mock_get_enterprise_uuid):
        """
        Test decline with non-existent UUID returns 400.
        """
        mock_get_enterprise_uuid.return_value = str(self.enterprise_customer_uuid_1)

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-decline')
        data = {
            'enterprise_customer_uuid': str(self.enterprise_customer_uuid_1),  # Add this line
            'subsidy_request_uuid': str(uuid4()),
            'send_notification': False,
            'disassociate_from_org': False
        }

        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'not found' in str(response.data)

    @mock.patch('enterprise_access.apps.api.v1.views.browse_and_request.get_enterprise_uuid_from_request_data')
    @mock.patch(BNR_VIEW_PATH + '.unlink_users_from_enterprise_task.delay')
    def test_decline_with_disassociate_from_org(self, mock_unlink_task, mock_get_enterprise_uuid):
        """
        Test decline with disassociate_from_org=True triggers unlinking task.
        """
        mock_get_enterprise_uuid.return_value = str(self.enterprise_customer_uuid_1)

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-decline')
        data = {
            'enterprise_customer_uuid': str(self.enterprise_customer_uuid_1),
            'subsidy_request_uuid': str(self.user_request_1.uuid),
            'send_notification': False,
            'disassociate_from_org': True
        }

        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_200_OK

        # Verify unlinking task was called
        mock_unlink_task.assert_called_once_with(
            str(self.enterprise_customer_uuid_1),
            [self.user_request_1.user.lms_user_id]
        )

    @mock.patch('enterprise_access.apps.api.v1.views.browse_and_request.get_enterprise_uuid_from_request_data')
    def test_decline_reason_saved(self, mock_get_enterprise_uuid):
        """
        Test decline reason is persisted when learner credit request is declined.
        """
        # Mock enterprise UUID
        mock_get_enterprise_uuid.return_value = str(self.enterprise_customer_uuid_1)

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-decline')
        data = {
            'enterprise_customer_uuid': str(self.enterprise_customer_uuid_1),
            'subsidy_request_uuid': str(self.user_request_1.uuid),
            'send_notification': False,
            'decline_reason': 'Request outside program scope'
        }

        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_200_OK

        # Verify request was declined and reason saved
        self.user_request_1.refresh_from_db()
        assert self.user_request_1.state == SubsidyRequestStates.DECLINED
        assert self.user_request_1.reviewer == self.user
        assert self.user_request_1.decline_reason == 'Request outside program scope'

    @mock.patch('enterprise_access.apps.api.v1.views.browse_and_request.get_enterprise_uuid_from_request_data')
    @mock.patch(
        'enterprise_access.apps.api_client.enterprise_catalog_client.'
        'EnterpriseCatalogApiClient.catalog_content_metadata'
    )
    def test_approve_happy_path(
        self,
        mock_catalog_content_metadata,
        mock_get_enterprise_uuid
    ):
        """
        Test successful approve of a learner credit request.
        """
        mock_get_enterprise_uuid.return_value = str(self.enterprise_customer_uuid_1)

        # Set up mock client and its methods
        self.mock_subsidy_client.retrieve_subsidy.return_value = {
            'uuid': str(uuid4()),
            'title': 'Test Subsidy',
            'enterprise_customer_uuid': str(self.enterprise_customer_uuid_1),
            'expiration_datetime': '2030-01-01 12:00:00Z',
            'active_datetime': '2020-01-01 12:00:00Z',
            'current_balance': 3000,
            'is_active': True,
        }

        total_quantity_transactions = (
            self.mock_transaction_record_1['quantity'] +
            self.mock_transaction_record_2['quantity']
        )
        self.mock_subsidy_client.list_subsidy_transactions.return_value = {
            'results': [
                self.mock_transaction_record_1,
                self.mock_transaction_record_2,
            ],
            'aggregates': {
                'total_quantity': total_quantity_transactions,
            }
        }

        mock_catalog_content_metadata.return_value = {
            'results': [{
                'key': self.user_request_1.course_id,
                'title': 'Demo Course',
                'content_type': 'courserun',
                'course_run_key': 'course-v1:edX+DemoX+Demo_Course',
                'first_enrollable_paid_seat_price': '10',
                'content_price': 10,
                'uuid': str(uuid4()),
            }]
        }

        # set up some approved requests with allocated assignments
        LearnerCreditRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            learner_credit_request_config=self.learner_credit_config,
            course_price=500,
            state=SubsidyRequestStates.APPROVED,
            assignment=self.assignment_1,
        )
        LearnerCreditRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            learner_credit_request_config=self.learner_credit_config,
            course_price=500,
            state=SubsidyRequestStates.APPROVED,
            assignment=self.assignment_2,
        )

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-approve')
        data = {
            "enterprise_customer_uuid": str(self.enterprise_customer_uuid_1),
            "policy_uuid": str(self.policy.uuid),
            "learner_credit_request_uuids": [str(self.user_request_1.uuid)]
        }
        response = self.client.post(url, data)
        assert response.status_code == status.HTTP_200_OK

        # Verify request was approved
        self.user_request_1.refresh_from_db()
        assert self.user_request_1.state == SubsidyRequestStates.APPROVED
        assert self.user_request_1.reviewer == self.user
        # Verify assignment was created with correct fields
        assert self.user_request_1.assignment is not None
        assert self.user_request_1.assignment.content_quantity == self.user_request_1.course_price * -1
        assert self.user_request_1.assignment.assignment_configuration == self.assignment_config
        assert self.user_request_1.assignment.state == 'allocated'
        assert self.user_request_1.assignment.learner_email == self.user_request_1.user.email
        assert self.user_request_1.assignment.content_key == self.user_request_1.course_id

        # Verify RequestAction was created
        self.assertIsNotNone(
            self.user_request_1.actions.get(
                learner_credit_request=self.user_request_1,
                recent_action=get_action_choice(SubsidyRequestStates.APPROVED),
                status=get_user_message_choice(
                    SubsidyRequestStates.APPROVED
                ),
            )
        )

    @ddt.data(
        LearnerContentAssignmentStateChoices.CANCELLED,
        LearnerContentAssignmentStateChoices.EXPIRED,
        LearnerContentAssignmentStateChoices.REVERSED,
    )
    @mock.patch('enterprise_access.apps.api.v1.views.browse_and_request.get_enterprise_uuid_from_request_data')
    @mock.patch(
        'enterprise_access.apps.api_client.enterprise_catalog_client.'
        'EnterpriseCatalogApiClient.catalog_content_metadata'
    )
    def test_approve_reallocate_assignment(
        self,
        reallocate_state,
        mock_catalog_content_metadata,
        mock_get_enterprise_uuid
    ):
        """
        Test that an existing assignment in reusable states (CANCELLED, EXPIRED, REVERSED)
        gets reallocated instead of creating a new one.
        """
        mock_get_enterprise_uuid.return_value = str(self.enterprise_customer_uuid_1)

        # Set up mock client and its methods
        self.mock_subsidy_client.retrieve_subsidy.return_value = {
            'uuid': str(uuid4()),
            'title': 'Test Subsidy',
            'enterprise_customer_uuid': str(self.enterprise_customer_uuid_1),
            'expiration_datetime': '2030-01-01 12:00:00Z',
            'active_datetime': '2020-01-01 12:00:00Z',
            'current_balance': 3000,
            'is_active': True,
        }

        self.mock_subsidy_client.list_subsidy_transactions.return_value = {
            'results': [],
            'aggregates': {
                'total_quantity': 0,
            }
        }

        mock_catalog_content_metadata.return_value = {
            'results': [{
                'key': self.user_request_1.course_id,
                'title': 'Demo Course',
                'content_type': 'courserun',
                'course_run_key': 'course-v1:edX+DemoX+Demo_Course',
                'first_enrollable_paid_seat_price': '10',
                'content_price': 10,
                'uuid': str(uuid4()),
            }]
        }

        # set up a content assignment in REALLOCATE state
        existing_assignment = LearnerContentAssignmentFactory(
            learner_email=self.user_request_1.user.email,
            lms_user_id=self.user_request_1.user.lms_user_id,
            content_key=self.user_request_1.course_id,
            assignment_configuration=self.assignment_config,
            content_quantity=-998,
            state=reallocate_state,
        )

        # Should have only one assignment before the API call
        initial_count = LearnerContentAssignment.objects.filter(
            learner_email=self.user_request_1.user.email,
            lms_user_id=self.user_request_1.user.lms_user_id,
            content_key=self.user_request_1.course_id,
            assignment_configuration=self.assignment_config,
        ).count()
        assert initial_count == 1

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-approve')
        data = {
            "enterprise_customer_uuid": str(self.enterprise_customer_uuid_1),
            "policy_uuid": str(self.policy.uuid),
            "learner_credit_request_uuids": [str(self.user_request_1.uuid)]
        }
        response = self.client.post(url, data)
        assert response.status_code == status.HTTP_200_OK

        # Verify request was approved
        self.user_request_1.refresh_from_db()
        existing_assignment.refresh_from_db()
        assert self.user_request_1.state == SubsidyRequestStates.APPROVED
        assert self.user_request_1.reviewer == self.user

        # Verify count
        final_count = LearnerContentAssignment.objects.filter(
            learner_email=self.user_request_1.user.email,
            lms_user_id=self.user_request_1.user.lms_user_id,
            content_key=self.user_request_1.course_id,
            assignment_configuration=self.assignment_config,
        ).count()
        assert final_count == 1  # Should still be one assignment after reallocation

        # Verify assignment was reallocated with correct fields
        assert self.user_request_1.assignment is not None
        assert self.user_request_1.assignment.uuid == existing_assignment.uuid
        assert self.user_request_1.assignment.content_quantity == self.user_request_1.course_price * -1
        assert self.user_request_1.assignment.assignment_configuration == self.assignment_config
        assert self.user_request_1.assignment.state == LearnerContentAssignmentStateChoices.ALLOCATED
        assert self.user_request_1.assignment.learner_email == self.user_request_1.user.email
        assert self.user_request_1.assignment.lms_user_id == self.user_request_1.user.lms_user_id
        assert self.user_request_1.assignment.content_key == self.user_request_1.course_id

        # Verify RequestAction was created
        self.assertIsNotNone(
            self.user_request_1.actions.get(
                learner_credit_request=self.user_request_1,
                recent_action=get_action_choice(SubsidyRequestStates.APPROVED),
                status=get_user_message_choice(
                    SubsidyRequestStates.APPROVED
                ),
            )
        )

    @mock.patch('enterprise_access.apps.api.v1.views.browse_and_request.get_enterprise_uuid_from_request_data')
    def test_approve_policy_expired(self, mock_get_enterprise_uuid):
        """
        Test approve when policy is expired/inactive (is_redemption_enabled = False).
        """
        mock_get_enterprise_uuid.return_value = str(self.enterprise_customer_uuid_1)

        # Set up mock subsidy client to return proper values
        self.mock_subsidy_client.retrieve_subsidy.return_value = {
            'uuid': str(uuid4()),
            'title': 'Test Subsidy',
            'enterprise_customer_uuid': str(self.enterprise_customer_uuid_1),
            'expiration_datetime': '2030-01-01 12:00:00Z',
            'active_datetime': '2020-01-01 12:00:00Z',
            'current_balance': 3000,
            'is_active': True,
        }
        self.mock_subsidy_client.list_subsidy_transactions.return_value = {
            'results': [],
            'aggregates': {
                'total_quantity': 0,
            }
        }

        # Set policy to inactive
        self.policy.active = False
        self.policy.save()

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-approve')
        data = {
            "enterprise_customer_uuid": str(self.enterprise_customer_uuid_1),
            "policy_uuid": str(self.policy.uuid),
            "learner_credit_request_uuids": [str(self.user_request_1.uuid)]
        }
        response = self.client.post(url, data)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert REASON_POLICY_EXPIRED in response.data['detail'].lower()

        # Verify request was not approved
        self.user_request_1.refresh_from_db()
        assert self.user_request_1.state == SubsidyRequestStates.REQUESTED
        assert self.user_request_1.assignment is None

        # Verify error action was tracked
        actions = LearnerCreditRequestActions.objects.filter(
            learner_credit_request=self.user_request_1
        ).order_by('-created')
        assert actions.exists()
        latest_action = actions.first()
        assert latest_action.status == get_user_message_choice(SubsidyRequestStates.REQUESTED)
        assert latest_action.error_reason is not None

    @mock.patch('enterprise_access.apps.api.v1.views.browse_and_request.get_enterprise_uuid_from_request_data')
    @mock.patch(
        'enterprise_access.apps.api_client.enterprise_catalog_client.'
        'EnterpriseCatalogApiClient.catalog_content_metadata'
    )
    def test_approve_content_not_in_catalog(
        self, mock_catalog_content_metadata, mock_get_enterprise_uuid
    ):
        """
        Test approve when content is not in the policy's catalog.
        """
        mock_get_enterprise_uuid.return_value = str(self.enterprise_customer_uuid_1)

        mock_catalog_content_metadata.return_value = {
            'results': [{
                'key': 'some-content-key',
            }]
        }

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-approve')
        data = {
            "enterprise_customer_uuid": str(self.enterprise_customer_uuid_1),
            "policy_uuid": str(self.policy.uuid),
            "learner_credit_request_uuids": [str(self.user_request_1.uuid)]
        }
        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert 'failed to approve' in response.data['detail'].lower()

        # Verify request was not approved
        self.user_request_1.refresh_from_db()
        assert self.user_request_1.state == SubsidyRequestStates.REQUESTED
        assert self.user_request_1.assignment is None

        # Verify error action was tracked with proper details
        actions = LearnerCreditRequestActions.objects.filter(
            learner_credit_request=self.user_request_1
        ).order_by('-created')
        assert actions.exists()

        latest_action = actions.first()
        assert latest_action.status == get_user_message_choice(SubsidyRequestStates.REQUESTED)
        assert latest_action.error_reason is not None
        assert latest_action.traceback is not None
        assert REASON_CONTENT_NOT_IN_CATALOG in latest_action.traceback.lower()

    @mock.patch('enterprise_access.apps.api.v1.views.browse_and_request.get_enterprise_uuid_from_request_data')
    @mock.patch(
        'enterprise_access.apps.api_client.enterprise_catalog_client.'
        'EnterpriseCatalogApiClient.catalog_content_metadata'
    )
    def test_approve_subsidy_inactive(self, mock_catalog_content_metadata, mock_get_enterprise_uuid):
        """
        Test approve when subsidy is inactive.
        """
        mock_get_enterprise_uuid.return_value = str(self.enterprise_customer_uuid_1)
        mock_catalog_content_metadata.return_value = {
            'results': [{
                'key': 'some-content-key',
            }]
        }

        # Mock inactive subsidy
        self.mock_subsidy_client.retrieve_subsidy.return_value = {
            'uuid': str(uuid4()),
            'title': 'Test Subsidy',
            'enterprise_customer_uuid': str(self.enterprise_customer_uuid_1),
            'expiration_datetime': '2030-01-01 12:00:00Z',
            'active_datetime': '2020-01-01 12:00:00Z',
            'current_balance': 3000,
            'is_active': False,  # Inactive subsidy
        }

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-approve')
        data = {
            "enterprise_customer_uuid": str(self.enterprise_customer_uuid_1),
            "policy_uuid": str(self.policy.uuid),
            "learner_credit_request_uuids": [str(self.user_request_1.uuid)]
        }
        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert REASON_SUBSIDY_EXPIRED in response.data['detail'].lower()

        # Verify request was not approved
        self.user_request_1.refresh_from_db()
        assert self.user_request_1.state == SubsidyRequestStates.REQUESTED
        assert self.user_request_1.assignment is None

    @mock.patch('enterprise_access.apps.api.v1.views.browse_and_request.get_enterprise_uuid_from_request_data')
    @mock.patch(
        'enterprise_access.apps.api_client.enterprise_catalog_client.'
        'EnterpriseCatalogApiClient.catalog_content_metadata'
    )
    def test_approve_insufficient_subsidy_balance(
        self, mock_catalog_content_metadata, mock_get_enterprise_uuid
    ):
        """
        Test approve when subsidy has insufficient balance.
        """
        mock_get_enterprise_uuid.return_value = str(self.enterprise_customer_uuid_1)
        mock_catalog_content_metadata.return_value = {
            'results': [{
                'key': self.user_request_1.course_id,
                'title': 'Demo Course',
                'content_type': 'courserun',
                'course_run_key': 'course-v1:edX+DemoX+Demo_Course',
                'first_enrollable_paid_seat_price': '10',
                'content_price': 10,
                'uuid': str(uuid4()),
            }]
        }

        # Mock subsidy with insufficient balance
        self.mock_subsidy_client.retrieve_subsidy.return_value = {
            'uuid': str(uuid4()),
            'title': 'Test Subsidy',
            'enterprise_customer_uuid': str(self.enterprise_customer_uuid_1),
            'expiration_datetime': '2030-01-01 12:00:00Z',
            'active_datetime': '2020-01-01 12:00:00Z',
            'current_balance': 1500,  # Insufficient balance (1000 needed, 1500 available)
            'is_active': True,
        }

        self.mock_subsidy_client.list_subsidy_transactions.return_value = {
            'results': [],
            'aggregates': {
                'total_quantity': 0,
            }
        }

        # set up some approved requests with allocated assignments
        LearnerCreditRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            learner_credit_request_config=self.learner_credit_config,
            course_price=500,
            state=SubsidyRequestStates.APPROVED,
            assignment=self.assignment_1,
        )
        LearnerCreditRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            learner_credit_request_config=self.learner_credit_config,
            course_price=500,
            state=SubsidyRequestStates.APPROVED,
            assignment=self.assignment_2,
        )

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-approve')
        data = {
            "enterprise_customer_uuid": str(self.enterprise_customer_uuid_1),
            "policy_uuid": str(self.policy.uuid),
            "learner_credit_request_uuids": [str(self.user_request_1.uuid)]
        }
        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY in response.data['detail'].lower()

        # Verify request was not approved
        self.user_request_1.refresh_from_db()
        assert self.user_request_1.state == SubsidyRequestStates.REQUESTED
        assert self.user_request_1.assignment is None

    @mock.patch('enterprise_access.apps.api.v1.views.browse_and_request.get_enterprise_uuid_from_request_data')
    @mock.patch(
        'enterprise_access.apps.api_client.enterprise_catalog_client.'
        'EnterpriseCatalogApiClient.catalog_content_metadata'
    )
    def test_approve_policy_spend_limit_exceeded(
        self, mock_catalog_content_metadata, mock_get_enterprise_uuid
    ):
        """
        Test approve when policy spend limit would be exceeded.
        """
        mock_get_enterprise_uuid.return_value = str(self.enterprise_customer_uuid_1)
        self.mock_subsidy_client.retrieve_subsidy.return_value = {
            'uuid': str(uuid4()),
            'title': 'Test Subsidy',
            'enterprise_customer_uuid': str(self.enterprise_customer_uuid_1),
            'expiration_datetime': '2030-01-01 12:00:00Z',
            'active_datetime': '2020-01-01 12:00:00Z',
            'current_balance': 5000,
            'is_active': True,
        }

        request_exceed_spend_limit = LearnerCreditRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            user=self.user,
            learner_credit_request_config=self.learner_credit_config,
            course_price=2500,  # balance=5000, spend_limit=4000, so this request would exceed the limit
            state=SubsidyRequestStates.REQUESTED,
            assignment=None
        )

        mock_catalog_content_metadata.return_value = {
            'results': [{
                'key': request_exceed_spend_limit.course_id,
                'title': 'Demo Course',
                'content_type': 'courserun',
                'course_run_key': 'course-v1:edX+DemoX+Demo_Course',
                'first_enrollable_paid_seat_price': '25',
                'content_price': 25,
                'uuid': str(uuid4()),
            }]
        }

        total_quantity_transactions = self.mock_transaction_record_1['quantity'] + \
            self.mock_transaction_record_2['quantity']
        self.mock_subsidy_client.list_subsidy_transactions.return_value = {
            'results': [
                self.mock_transaction_record_1,
                self.mock_transaction_record_2,
            ],
            'aggregates': {
                'total_quantity': total_quantity_transactions,
            }
        }

        # set up some approved requests with allocated assignments
        LearnerCreditRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            learner_credit_request_config=self.learner_credit_config,
            course_price=500,
            state=SubsidyRequestStates.APPROVED,
            assignment=self.assignment_1,
        )
        LearnerCreditRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            learner_credit_request_config=self.learner_credit_config,
            course_price=500,
            state=SubsidyRequestStates.APPROVED,
            assignment=self.assignment_2,
        )

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-approve')
        data = {
            "enterprise_customer_uuid": str(self.enterprise_customer_uuid_1),
            "policy_uuid": str(self.policy.uuid),
            "learner_credit_request_uuids": [str(request_exceed_spend_limit.uuid)]
        }
        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert REASON_POLICY_SPEND_LIMIT_REACHED in response.data['detail'].lower()

        # Verify request was not approved
        request_exceed_spend_limit.refresh_from_db()
        assert request_exceed_spend_limit.state == SubsidyRequestStates.REQUESTED
        assert request_exceed_spend_limit.assignment is None

    @mock.patch('enterprise_access.apps.api.v1.views.browse_and_request.get_enterprise_uuid_from_request_data')
    @mock.patch(
        'enterprise_access.apps.api_client.enterprise_catalog_client.'
        'EnterpriseCatalogApiClient.catalog_content_metadata'
    )
    def test_approve_invalid_price_validation(
        self, mock_catalog_content_metadata, mock_get_enterprise_uuid
    ):
        """
        Test approve when content price validation fails.
        """
        course_canonical_price = 200
        mock_get_enterprise_uuid.return_value = str(self.enterprise_customer_uuid_1)
        mock_catalog_content_metadata.return_value = {
            'results': [{
                'key': self.user_request_1.course_id,
                'title': 'Demo Course',
                'content_type': 'courserun',
                'course_run_key': 'course-v1:edX+DemoX+Demo_Course',
                'first_enrollable_paid_seat_price': str(course_canonical_price),
                'content_price': course_canonical_price,  # request price way beyond canonical price range.
                'uuid': str(uuid4()),
            }]
        }

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-approve')
        data = {
            "enterprise_customer_uuid": str(self.enterprise_customer_uuid_1),
            "policy_uuid": str(self.policy.uuid),
            "learner_credit_request_uuids": [str(self.user_request_1.uuid)]
        }
        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        # Verify request was not approved
        self.user_request_1.refresh_from_db()
        assert self.user_request_1.state == SubsidyRequestStates.REQUESTED
        assert self.user_request_1.assignment is None

        actions = LearnerCreditRequestActions.objects.filter(
            learner_credit_request=self.user_request_1
        ).order_by('-created')
        latest_action = actions.first()
        assert PriceValidationError.__name__ in latest_action.traceback

    def test_approve_nonexistent_policy(self):
        """
        Test approve with nonexistent policy UUID.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-approve')
        non_existent_policy_uuid = str(uuid4())
        data = {
            "enterprise_customer_uuid": str(self.enterprise_customer_uuid_1),
            "policy_uuid": non_existent_policy_uuid,  # Nonexistent UUID
            "learner_credit_request_uuids": [str(self.user_request_1.uuid)]
        }
        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_approve_missing_required_fields(self):
        """
        Test approve with missing required fields.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-approve')

        # Test missing policy_uuid
        data = {
            "enterprise_customer_uuid": str(self.enterprise_customer_uuid_1),
            "learner_credit_request_uuids": [str(self.user_request_1.uuid)]
        }
        response = self.client.post(url, data)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "required" in response.data['policy_uuid'][0].lower()

        # Test missing learner_credit_request_uuid
        data = {
            "enterprise_customer_uuid": str(self.enterprise_customer_uuid_1),
            "policy_uuid": str(self.policy.uuid),
        }
        response = self.client.post(url, data)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "required" in response.data['learner_credit_request_uuids'][0].lower()

    def test_approve_unauthorized_access(self):
        """
        Test approve without proper admin permissions.
        """
        # Set learner role instead of admin
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-approve')
        data = {
            "enterprise_customer_uuid": str(self.enterprise_customer_uuid_1),
            "policy_uuid": str(self.policy.uuid),
            "learner_credit_request_uuids": [str(self.user_request_1.uuid)]
        }
        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_approve_wrong_enterprise_context(self):
        """
        Test approve with admin permissions for wrong enterprise.
        """
        # Set admin role for different enterprise
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_2)  # Different enterprise
        }])

        url = reverse('api:v1:learner-credit-requests-approve')
        data = {
            "enterprise_customer_uuid": str(self.enterprise_customer_uuid_1),
            "policy_uuid": str(self.policy.uuid),
            "learner_credit_request_uuids": [str(self.user_request_1.uuid)]
        }
        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @mock.patch('enterprise_access.apps.api.v1.views.browse_and_request.get_enterprise_uuid_from_request_data')
    @mock.patch(
        'enterprise_access.apps.api_client.enterprise_catalog_client.'
        'EnterpriseCatalogApiClient.catalog_content_metadata'
    )
    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.lock')
    def test_approve_policy_lock_failure(
        self, mock_lock, mock_catalog_content_metadata, mock_get_enterprise_uuid
    ):
        """
        Test approve when policy lock acquisition fails.
        """
        mock_get_enterprise_uuid.return_value = str(self.enterprise_customer_uuid_1)
        mock_catalog_content_metadata.return_value = {
            'results': [{
                'key': self.user_request_1.course_id,
                'title': 'Demo Course',
                'content_type': 'courserun',
                'course_run_key': 'course-v1:edX+DemoX+Demo_Course',
                'first_enrollable_paid_seat_price': '10',
                'content_price': 10,
                'uuid': str(uuid4()),
            }]
        }

        # Mock lock failure
        mock_lock.side_effect = SubsidyAccessPolicyLockAttemptFailed("Lock failed")

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-approve')
        data = {
            "enterprise_customer_uuid": str(self.enterprise_customer_uuid_1),
            "policy_uuid": str(self.policy.uuid),
            "learner_credit_request_uuids": [str(self.user_request_1.uuid)]
        }
        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "failed to acquire lock" in response.data['detail'].lower()

        # Verify request was not approved
        self.user_request_1.refresh_from_db()
        assert self.user_request_1.state == SubsidyRequestStates.REQUESTED
        assert self.user_request_1.assignment is None

    def test_cancel_invalid_request_uuid(self):
        """
        Test cancel with invalid UUID format returns 400.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-cancel')
        data = {
            'enterprise_customer_uuid': str(self.enterprise_customer_uuid_1),
            'request_uuid': 'invalid-uuid-format'
        }
        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_cancel_nonexistent_request(self):
        """
        Test cancel with non-existent request UUID returns 400.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-cancel')
        data = {
            'enterprise_customer_uuid': str(self.enterprise_customer_uuid_1),
            'request_uuid': str(uuid4())
        }
        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @mock.patch('enterprise_access.apps.content_assignments.api.cancel_assignments')
    def test_cancel_success(self, mock_cancel_assignments):
        """
        Test successful cancellation of an approved learner credit request.
        """
        # Set up approved request with assignment
        assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_config,
            content_quantity=-1000,
            state='allocated'
        )
        approved_request = LearnerCreditRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            user=self.user,
            learner_credit_request_config=self.learner_credit_config,
            course_price=1000,
            state=SubsidyRequestStates.APPROVED,
            assignment=assignment
        )

        # Mock successful assignment cancellation
        mock_cancel_assignments.return_value = {'non_cancelable': []}

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-cancel')
        data = {
            'enterprise_customer_uuid': str(self.enterprise_customer_uuid_1),
            'request_uuid': str(approved_request.uuid)
        }
        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_200_OK

        # Verify request was cancelled
        approved_request.refresh_from_db()
        assert approved_request.state == SubsidyRequestStates.CANCELLED
        assert approved_request.reviewer == self.user

        # Verify assignment cancellation was called
        mock_cancel_assignments.assert_called_once_with([assignment], False)

        # Verify successful action record was created
        success_action = LearnerCreditRequestActions.objects.filter(
            learner_credit_request=approved_request,
            recent_action=get_action_choice(SubsidyRequestStates.CANCELLED),
            status=get_user_message_choice(SubsidyRequestStates.CANCELLED),
            error_reason=None,
            traceback=None
        ).first()
        assert success_action is not None

    @mock.patch('enterprise_access.apps.content_assignments.api.cancel_assignments')
    def test_cancel_failed_assignment_cancellation(self, mock_cancel_assignments):
        """
        Test cancel failure when assignment cannot be cancelled.
        """
        # Set up approved request with assignment
        assignment = LearnerContentAssignmentFactory(
            assignment_configuration=self.assignment_config,
            content_quantity=-1000,
            state='allocated'
        )
        approved_request = LearnerCreditRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            user=self.user,
            learner_credit_request_config=self.learner_credit_config,
            course_price=1000,
            state=SubsidyRequestStates.APPROVED,
            assignment=assignment
        )

        # Mock failed assignment cancellation
        mock_cancel_assignments.return_value = {'non_cancelable': [assignment.uuid]}

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        url = reverse('api:v1:learner-credit-requests-cancel')
        data = {
            'enterprise_customer_uuid': str(self.enterprise_customer_uuid_1),
            'request_uuid': str(approved_request.uuid)
        }
        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        expected_error = (
            f"Failed to cancel associated assignment with uuid: {assignment.uuid}"
            f" for request: {approved_request.uuid}."
        )
        assert response.data == expected_error

        # Verify request was NOT cancelled
        approved_request.refresh_from_db()
        assert approved_request.state == SubsidyRequestStates.APPROVED

        # Verify assignment cancellation was called
        mock_cancel_assignments.assert_called_once_with([assignment], False)

        # Verify error action record was created
        error_action = LearnerCreditRequestActions.objects.filter(
            learner_credit_request=approved_request,
            recent_action=get_action_choice(SubsidyRequestStates.CANCELLED),
            status=get_user_message_choice(SubsidyRequestStates.APPROVED),
            error_reason=get_error_reason_choice(LearnerCreditRequestActionErrorReasons.FAILED_CANCELLATION)
        ).first()
        assert error_action is not None
        assert error_action.traceback == expected_error

    @ddt.data(
        'latest_action_status',
        '-latest_action_status',
    )
    def test_list_ordering_latest_action_status(self, ordering_key):
        """
        Test that the list view returns objects in the correct order when latest_action_status is the ordering key.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        # Create actions with different statuses
        LearnerCreditRequestActions.create_action(
            learner_credit_request=self.user_request_1,
            recent_action=SubsidyRequestStates.APPROVED,
            status=SubsidyRequestStates.APPROVED,
        )
        LearnerCreditRequestActions.create_action(
            learner_credit_request=self.user_request_2,
            recent_action=SubsidyRequestStates.REQUESTED,
            status=SubsidyRequestStates.REQUESTED,
        )

        # Test ordering
        response = self.client.get(
            reverse('api:v1:learner-credit-requests-list'),
            {'ordering': ordering_key}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.json()['results']

        # Find test requests and verify ordering
        first_learner_credit_request_result = next(
            (r for r in results if r['uuid'] == str(self.user_request_1.uuid)), None
        )
        second_learner_credit_request_result = next(
            (r for r in results if r['uuid'] == str(self.user_request_2.uuid)), None
        )

        self.assertIsNotNone(first_learner_credit_request_result)
        self.assertIsNotNone(second_learner_credit_request_result)

        first_learner_credit_request_position = results.index(first_learner_credit_request_result)
        second_learner_credit_request_position = results.index(second_learner_credit_request_result)

        if ordering_key == 'latest_action_status':
            self.assertLess(first_learner_credit_request_position, second_learner_credit_request_position)
        else:
            self.assertGreater(first_learner_credit_request_position, second_learner_credit_request_position)

    @ddt.data(
        [SubsidyRequestStates.APPROVED, SubsidyRequestStates.REQUESTED],
        [SubsidyRequestStates.APPROVED],
    )
    def test_latest_action_status_query_param_filter(self, statuses_to_query):
        """
        Test that the list view supports filtering by latest_action_status.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        # Create test actions
        LearnerCreditRequestActions.create_action(
            learner_credit_request=self.user_request_1,
            recent_action=SubsidyRequestStates.APPROVED,
            status=SubsidyRequestStates.APPROVED,
        )
        LearnerCreditRequestActions.create_action(
            learner_credit_request=self.user_request_2,
            recent_action=SubsidyRequestStates.REQUESTED,
            status=SubsidyRequestStates.REQUESTED,
        )

        # Test filtering
        response = self.client.get(
            reverse('api:v1:learner-credit-requests-list'),
            {'latest_action_status__in': ",".join(statuses_to_query)}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify filtered results
        for result in response.json()['results']:
            latest_action = result.get('latest_action')
            if latest_action:
                self.assertIn(latest_action['status'], statuses_to_query)

    def test_latest_action_status_exact_filter(self):
        """
        Test that the list view supports exact filtering by latest_action_status.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        # Create test actions
        LearnerCreditRequestActions.create_action(
            learner_credit_request=self.user_request_1,
            recent_action=SubsidyRequestStates.APPROVED,
            status=SubsidyRequestStates.APPROVED,
        )
        LearnerCreditRequestActions.create_action(
            learner_credit_request=self.user_request_2,
            recent_action=SubsidyRequestStates.REQUESTED,
            status=SubsidyRequestStates.REQUESTED,
        )

        # Test exact filtering
        response = self.client.get(
            reverse('api:v1:learner-credit-requests-list'),
            {'latest_action_status': SubsidyRequestStates.APPROVED}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify only approved results
        for result in response.json()['results']:
            latest_action = result.get('latest_action')
            if latest_action:
                self.assertEqual(latest_action['status'], SubsidyRequestStates.APPROVED)

    @ddt.data(
        'latest_action_time',
        '-latest_action_time',
    )
    def test_list_ordering_latest_action_time(self, ordering_key):
        """
        Test that the list view returns objects in the correct order when latest_action_time is the ordering key.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        # Create actions with time difference
        LearnerCreditRequestActions.create_action(
            learner_credit_request=self.user_request_1,
            recent_action=SubsidyRequestStates.REQUESTED,
            status=SubsidyRequestStates.REQUESTED,
        )
        time.sleep(0.001)
        LearnerCreditRequestActions.create_action(
            learner_credit_request=self.user_request_2,
            recent_action=SubsidyRequestStates.APPROVED,
            status=SubsidyRequestStates.APPROVED,
        )

        # Test ordering
        response = self.client.get(
            reverse('api:v1:learner-credit-requests-list'),
            {'ordering': ordering_key}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.json()['results']

        # Find test requests and verify ordering
        first_learner_credit_request_result = next(
            (r for r in results if r['uuid'] == str(self.user_request_1.uuid)), None
        )
        second_learner_credit_request_result = next(
            (r for r in results if r['uuid'] == str(self.user_request_2.uuid)), None
        )

        self.assertIsNotNone(first_learner_credit_request_result)
        self.assertIsNotNone(second_learner_credit_request_result)

        first_learner_credit_request_position = results.index(first_learner_credit_request_result)
        second_learner_credit_request_position = results.index(second_learner_credit_request_result)
        if ordering_key == 'latest_action_time':
            self.assertLess(first_learner_credit_request_position, second_learner_credit_request_position)
        else:
            self.assertGreater(first_learner_credit_request_position, second_learner_credit_request_position)

    @ddt.data(
        'latest_action_type',
        '-latest_action_type',
    )
    def test_list_ordering_latest_action_type(self, ordering_key):
        """
        Test that the list view returns objects in the correct order when latest_action_type is the ordering key.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        LearnerCreditRequestActions.create_action(
            learner_credit_request=self.user_request_1,
            recent_action=SubsidyRequestStates.APPROVED,
            status=SubsidyRequestStates.APPROVED,
        )

        LearnerCreditRequestActions.create_action(
            learner_credit_request=self.user_request_2,
            recent_action=SubsidyRequestStates.REQUESTED,
            status=SubsidyRequestStates.REQUESTED,
        )

        query_params = {'ordering': ordering_key}
        response = self.client.get(reverse('api:v1:learner-credit-requests-list'), data=query_params)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.json()['results']
        self.assertGreaterEqual(len(results), 2)

        # Find our test requests in results
        first_learner_credit_request_result = next(
            (r for r in results if r['uuid'] == str(self.user_request_1.uuid)), None
        )
        second_learner_credit_request_result = next(
            (r for r in results if r['uuid'] == str(self.user_request_2.uuid)), None
        )

        self.assertIsNotNone(first_learner_credit_request_result, "Request 1 should be in results")
        self.assertIsNotNone(second_learner_credit_request_result, "Request 2 should be in results")

        # Get positions in results to verify ordering
        first_learner_credit_request_position = results.index(first_learner_credit_request_result)
        second_learner_credit_request_position = results.index(second_learner_credit_request_result)

        if ordering_key == 'latest_action_type':
            self.assertLess(first_learner_credit_request_position, second_learner_credit_request_position,
                            "'approved' action type should sort before 'requested' in ascending order")
        else:
            self.assertGreater(first_learner_credit_request_position, second_learner_credit_request_position,
                               "'approved' action type should sort after 'requested' in descending order")
