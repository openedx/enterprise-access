"""
Tests for Enterprise Access Browse and Request app API v1 views.
"""
import random
from unittest import mock
from uuid import uuid4

import ddt
from django.conf import settings
from rest_framework import status
from rest_framework.reverse import reverse

from enterprise_access.apps.core.constants import (
    ALL_ACCESS_CONTEXT,
    SYSTEM_ENTERPRISE_ADMIN_ROLE,
    SYSTEM_ENTERPRISE_LEARNER_ROLE,
    SYSTEM_ENTERPRISE_OPERATOR_ROLE
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import AssignedLearnerCreditAccessPolicyFactory
from enterprise_access.apps.subsidy_request.constants import SegmentEvents, SubsidyRequestStates, SubsidyTypeChoices
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
class TestLicenseRequestViewSet(BaseEnterpriseAccessTestCase):
    """
    Tests for LicenseRequestViewSet.
    """

    def setUp(self):
        super().setUp()

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(self.enterprise_customer_uuid_1),
        }])

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

        # license request with no associations to the user
        self.other_license_request = LicenseRequestFactory()

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
class TestLearnerCreditRequestViewSet(BaseEnterpriseAccessTestCase):
    """
    Tests for LearnerCreditRequestViewSet.
    """

    def setUp(self):
        super().setUp()

        # Setup test policy and config
        self.learner_credit_config = LearnerCreditRequestConfigurationFactory(active=True)
        self.policy = AssignedLearnerCreditAccessPolicyFactory(
            learner_credit_request_config=self.learner_credit_config,
            enterprise_customer_uuid=self.enterprise_customer_uuid_1
        )

        # Setup test requests
        self.user_request_1 = LearnerCreditRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            user=self.user,
            learner_credit_request_config=self.learner_credit_config,
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
        disabled_policy = AssignedLearnerCreditAccessPolicyFactory(
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
