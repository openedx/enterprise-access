"""
Tests for Enterprise Access API v1 views.
"""

import random
from uuid import uuid4

import ddt
import mock
from django.conf import settings
from pytest import mark
from rest_framework import status
from rest_framework.reverse import reverse

from enterprise_access.apps.core.constants import (
    ALL_ACCESS_CONTEXT,
    SYSTEM_ENTERPRISE_ADMIN_ROLE,
    SYSTEM_ENTERPRISE_LEARNER_ROLE,
    SYSTEM_ENTERPRISE_OPERATOR_ROLE
)
from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates, SubsidyTypeChoices
from enterprise_access.apps.subsidy_request.models import (
    CouponCodeRequest,
    LicenseRequest,
    SubsidyRequestCustomerConfiguration
)
from enterprise_access.apps.subsidy_request.tests.factories import (
    CouponCodeRequestFactory,
    LicenseRequestFactory,
    SubsidyRequestCustomerConfigurationFactory
)
from test_utils import APITest

LICENSE_REQUESTS_LIST_ENDPOINT = reverse('api:v1:license-requests-list')
LICENSE_REQUESTS_APPROVE_ENDPOINT = reverse('api:v1:license-requests-approve')
LICENSE_REQUESTS_DECLINE_ENDPOINT = reverse('api:v1:license-requests-decline')
LICENSE_REQUESTS_OVERVIEW_ENDPOINT = reverse('api:v1:license-requests-overview')
COUPON_CODE_REQUESTS_LIST_ENDPOINT = reverse('api:v1:coupon-code-requests-list')
COUPON_CODE_REQUESTS_APPROVE_ENDPOINT = reverse('api:v1:coupon-code-requests-approve')
COUPON_CODE_REQUESTS_DECLINE_ENDPOINT = reverse('api:v1:coupon-code-requests-decline')
CUSTOMER_CONFIGURATIONS_LIST_ENDPOINT = reverse('api:v1:customer-configurations-list')


@ddt.ddt
@mark.django_db
class TestSubsidyRequestViewSet(APITest):
    """
    Tests for SubsidyRequestViewSet.
    """

    def setUp(self):
        super().setUp()
        self.set_jwt_cookie([
            {
                'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE,
                'context': ALL_ACCESS_CONTEXT
            }
        ])

        self.enterprise_customer_uuid_1 = uuid4()
        self.enterprise_customer_uuid_2 = uuid4()

@ddt.ddt
class TestLicenseRequestViewSet(TestSubsidyRequestViewSet):
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

    @mock.patch('enterprise_access.apps.api.v1.views.LicenseManagerApiClient.get_subscription_overview')
    def test_approve_not_enough_subs_remaining_in_lm(self, mock_get_sub):
        """ 422 thrown if not enough subs remaining in license """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        mock_get_sub.return_value = {
            'results': [
                {
                    'status': 'assigned',
                    'count': 13,
                },
                {
                    'status': 'unassigned',
                    'count': 0,
                }
            ]
        }
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

    @mock.patch('enterprise_access.apps.api.v1.views.LicenseManagerApiClient.get_subscription_overview')
    def test_approve_subsidy_request_already_declined(self, mock_get_sub):
        """ 422 thrown if any subsidy request in payload already declined """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        mock_get_sub.return_value = {
            'results': [
                {
                    'status': 'assigned',
                    'count': 13,
                },
                {
                    'status': 'unassigned',
                    'count': 100000000,
                }
            ]
        }
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

    @mock.patch('enterprise_access.apps.api.v1.views.send_notification_emails_for_requests.si')
    @mock.patch('enterprise_access.apps.api.v1.views.assign_licenses_task')
    @mock.patch('enterprise_access.apps.api.v1.views.LicenseManagerApiClient.get_subscription_overview')
    def test_approve_subsidy_request_success(self, mock_get_sub, _, mock_notify):
        """ Test subsidy approval takes place when proper info provided"""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])
        mock_get_sub.return_value = {
            'results': [
                {
                    'status': 'assigned',
                    'count': 13,
                },
                {
                    'status': 'unassigned',
                    'count': 100000000,
                }
            ]
        }
        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 0

        payload = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'subsidy_request_uuids': [self.user_license_request_1.uuid],
            'subscription_plan_uuid': self.user_license_request_1.subscription_plan_uuid,
            'send_notification': True,
        }
        response = self.client.post(LICENSE_REQUESTS_APPROVE_ENDPOINT, payload)
        assert response.status_code == status.HTTP_200_OK
        self.user_license_request_1.refresh_from_db()
        assert self.user_license_request_1.state == SubsidyRequestStates.PENDING

        assert LicenseRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 1

        mock_notify.assert_called_with(
            [self.user_license_request_1.uuid],
            settings.BRAZE_APPROVE_NOTIFICATION_CAMPAIGN,
            LicenseRequest,
        )

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

    @mock.patch('enterprise_access.apps.api.v1.views.send_notification_emails_for_requests.apply_async')
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
            [self.user_license_request_1.uuid],
            settings.BRAZE_DECLINE_NOTIFICATION_CAMPAIGN,
            LicenseRequest,
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
class TestCouponCodeRequestViewSet(TestSubsidyRequestViewSet):
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

    @mock.patch('enterprise_access.apps.api.v1.views.EcommerceApiClient.get_coupon_overview')
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


    @mock.patch('enterprise_access.apps.api.v1.views.EcommerceApiClient.get_coupon_overview')
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

    @mock.patch('enterprise_access.apps.api.v1.views.send_notification_emails_for_requests.si')
    @mock.patch('enterprise_access.apps.api.v1.views.assign_coupon_codes_task')
    @mock.patch('enterprise_access.apps.api.v1.views.EcommerceApiClient.get_coupon_overview')
    def test_approve_subsidy_request_success(self, mock_get_coupon, _, mock_notify):
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

        assert CouponCodeRequest.objects.filter(
            state=SubsidyRequestStates.PENDING
        ).count() == 1

        mock_notify.assert_called_with(
            [self.coupon_code_request_1.uuid],
            settings.BRAZE_APPROVE_NOTIFICATION_CAMPAIGN,
            CouponCodeRequest,
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

    @mock.patch('enterprise_access.apps.api.v1.views.send_notification_emails_for_requests.apply_async')
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
            [self.coupon_code_request_1.uuid],
            settings.BRAZE_DECLINE_NOTIFICATION_CAMPAIGN,
            CouponCodeRequest,
        )

@ddt.ddt
class TestSubsidyRequestCustomerConfigurationViewSet(APITest):
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

    @mock.patch('enterprise_access.apps.api.tasks.send_notification_emails_for_requests.si')
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
        mock_send_notification_emails_for_requests,
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
        mock_send_notification_emails_for_requests.assert_called_with(
            [str(expected_declined_subsidy.uuid)],
            'test-campaign-id',
            previous_subsidy_type,
        )

    @mock.patch('enterprise_access.apps.api.tasks.send_notification_emails_for_requests.si')
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
        mock_send_notification_emails_for_requests,
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
        mock_send_notification_emails_for_requests.assert_not_called()
