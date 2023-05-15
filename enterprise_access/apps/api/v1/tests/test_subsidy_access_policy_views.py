"""
Tests for Enterprise Access Subsidy Access Policy app API v1 views.
"""
import re
from operator import itemgetter
from unittest import mock
from uuid import UUID, uuid4

import ddt
from django.conf import settings
from requests.exceptions import HTTPError
from rest_framework import status
from rest_framework.reverse import reverse

from enterprise_access.apps.api.v1.views.subsidy_access_policy import SubsidyAccessPolicyRedeemViewset
from enterprise_access.apps.core.constants import (
    POLICY_REDEMPTION_PERMISSION,
    SYSTEM_ENTERPRISE_ADMIN_ROLE,
    SYSTEM_ENTERPRISE_LEARNER_ROLE,
    SYSTEM_ENTERPRISE_OPERATOR_ROLE
)
from enterprise_access.apps.subsidy_access_policy.constants import (
    REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY,
    MissingSubsidyAccessReasonUserMessages,
    TransactionStateChoices
)
from enterprise_access.apps.subsidy_access_policy.exceptions import ContentPriceNullException
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory,
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory
)
from test_utils import APITestWithMocks

SUBSIDY_ACCESS_POLICY_LIST_ENDPOINT = reverse('api:v1:policy-list')
SUBSIDY_ACCESS_POLICY_ADMIN_LIST_ENDPOINT = reverse('api:v1:admin-policy-list')

TEST_ENTERPRISE_UUID = uuid4()


# pylint: disable=missing-function-docstring
class CRUDViewTestMixin:
    """
    Mixin to set some basic state for test classes that cover the
    subsidy access policy CRUD views.
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.enterprise_uuid = TEST_ENTERPRISE_UUID

        cls.redeemable_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=cls.enterprise_uuid,
            spend_limit=3,
            active=True,
        )
        cls.non_redeemable_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=cls.enterprise_uuid,
            spend_limit=0,
            active=True,
        )

    def setUp(self):
        super().setUp()
        # Start in an unauthenticated state.
        self.client.logout()


@ddt.ddt
class TestPolicyCRUDAuthNAndPermissionChecks(CRUDViewTestMixin, APITestWithMocks):
    """
    Tests Authentication and Permission checking for Subsidy Access Policy CRUD views.
    """
    @ddt.data(
        # A role that's not mapped to any feature perms will get you a 403.
        (
            {'system_wide_role': 'some-other-role', 'context': str(TEST_ENTERPRISE_UUID)},
            status.HTTP_403_FORBIDDEN,
        ),
        # A good admin role, but in a context/customer we're not aware of, gets you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(uuid4())},
            status.HTTP_403_FORBIDDEN,
        ),
        # A good learner role, but in a context/customer we're not aware of, gets you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(uuid4())},
            status.HTTP_403_FORBIDDEN,
        ),
        # An operator role, but in a context/customer we're not aware of, gets you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(uuid4())},
            status.HTTP_403_FORBIDDEN,
        ),
        # No JWT based auth, no soup for you.
        (
            None,
            status.HTTP_401_UNAUTHORIZED,
        ),
    )
    @ddt.unpack
    def test_policy_crud_views_unauthorized_forbidden(self, role_context_dict, expected_response_code):
        """
        Tests that we get expected 40x responses for all of the policy readonly views.
        """
        # Set the JWT-based auth that we'll use for every request
        if role_context_dict:
            self.set_jwt_cookie([role_context_dict])

        request_kwargs = {'uuid': str(self.redeemable_policy.uuid)}

        # Test the retrieve endpoint
        response = self.client.get(reverse('api:v1:subsidy-access-policies-detail', kwargs=request_kwargs))
        self.assertEqual(response.status_code, expected_response_code)

        # Test the list endpoint
        response = self.client.get(reverse('api:v1:subsidy-access-policies-list'))
        self.assertEqual(response.status_code, expected_response_code)

        # Test all the deprecated viewset actions.
        detail_url = reverse('api:v1:admin-policy-detail', kwargs=request_kwargs)
        list_url = reverse('api:v1:admin-policy-list')

        # Test the deprecated retrieve endpoint
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, expected_response_code)

        # Test the deprecated list endpoint
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, expected_response_code)

        # Test the deprecated create action
        response = self.client.post(list_url, data={'any': 'payload'})
        self.assertEqual(response.status_code, expected_response_code)

        # Test the deprecated patch action
        response = self.client.patch(detail_url, data={'any': 'other payload'})
        self.assertEqual(response.status_code, expected_response_code)

        # Test the deprecated destroy action
        response = self.client.delete(detail_url)
        self.assertEqual(response.status_code, expected_response_code)


@ddt.ddt
class TestAuthenticatedPolicyReadOnlyViews(CRUDViewTestMixin, APITestWithMocks):
    """
    Test the list and detail views for subsidy access policy records.
    """
    @ddt.data(
        # A good admin role, but for a context/customer that doesn't match anything we're aware of, gets you a 403.
        {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good learner role, but for a context/customer that doesn't match anything we're aware of, gets you a 403.
        {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good operator role, but for a context/customer that doesn't match anything we're aware of, gets you a 403.
        {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
    )
    def test_detail_view(self, role_context_dict):
        """
        Test that the detail view returns a 200 response code and the expected results of serialization.
        """
        # Set the JWT-based auth that we'll use for every request
        self.set_jwt_cookie([role_context_dict])

        request_kwargs = {'uuid': str(self.redeemable_policy.uuid)}

        # Test the retrieve endpoint
        response = self.client.get(reverse('api:v1:subsidy-access-policies-detail', kwargs=request_kwargs))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual({
            'access_method': 'direct',
            'active': True,
            'catalog_uuid': str(self.redeemable_policy.catalog_uuid),
            'description': '',
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'per_learner_enrollment_limit': self.redeemable_policy.per_learner_enrollment_limit,
            'per_learner_spend_limit': self.redeemable_policy.per_learner_spend_limit,
            'policy_type': 'PerLearnerEnrollmentCreditAccessPolicy',
            'spend_limit': 3,
            'subsidy_uuid': str(self.redeemable_policy.subsidy_uuid),
            'uuid': str(self.redeemable_policy.uuid),
        }, response.json())

    @ddt.data(
        # A good admin role, but for a context/customer that doesn't match anything we're aware of, gets you a 403.
        {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good learner role, but for a context/customer that doesn't match anything we're aware of, gets you a 403.
        {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
        # A good operator role, but for a context/customer that doesn't match anything we're aware of, gets you a 403.
        {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
    )
    def test_list_view(self, role_context_dict):
        """
        Test that the list view returns a 200 response code and the expected (list) results of serialization.
        """
        # Set the JWT-based auth that we'll use for every request
        self.set_jwt_cookie([role_context_dict])

        # Test the retrieve endpoint
        response = self.client.get(
            reverse('api:v1:subsidy-access-policies-list'),
            {'enterprise_customer_uuid': str(self.enterprise_uuid)},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_json = response.json()
        self.assertEqual(response_json['count'], 2)

        expected_results = [
            {
                'access_method': 'direct',
                'active': True,
                'catalog_uuid': str(self.non_redeemable_policy.catalog_uuid),
                'description': '',
                'enterprise_customer_uuid': str(self.enterprise_uuid),
                'per_learner_enrollment_limit': self.non_redeemable_policy.per_learner_enrollment_limit,
                'per_learner_spend_limit': self.non_redeemable_policy.per_learner_spend_limit,
                'policy_type': 'PerLearnerEnrollmentCreditAccessPolicy',
                'spend_limit': 0,
                'subsidy_uuid': str(self.non_redeemable_policy.subsidy_uuid),
                'uuid': str(self.non_redeemable_policy.uuid),
            },
            {
                'access_method': 'direct',
                'active': True,
                'catalog_uuid': str(self.redeemable_policy.catalog_uuid),
                'description': '',
                'enterprise_customer_uuid': str(self.enterprise_uuid),
                'per_learner_enrollment_limit': self.redeemable_policy.per_learner_enrollment_limit,
                'per_learner_spend_limit': self.redeemable_policy.per_learner_spend_limit,
                'policy_type': 'PerLearnerEnrollmentCreditAccessPolicy',
                'spend_limit': 3,
                'subsidy_uuid': str(self.redeemable_policy.subsidy_uuid),
                'uuid': str(self.redeemable_policy.uuid),
            },
        ]

        sort_key = itemgetter('spend_limit')
        self.assertEqual(
            sorted(expected_results, key=sort_key),
            sorted(response_json['results'], key=sort_key),
        )


@ddt.ddt
class TestPolicyRedemptionAuthNAndPermissionChecks(APITestWithMocks):
    """
    Tests Authentication and Permission checking for Subsidy Access Policy views.
    Specifically, test all the non-happy-path conditions.
    """
    def setUp(self):
        super().setUp()
        self.enterprise_uuid = TEST_ENTERPRISE_UUID
        self.redeemable_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            spend_limit=3,
        )
        self.non_redeemable_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory()

    @ddt.data(
        # A role that's not mapped to any feature perms will get you a 403.
        (
            {'system_wide_role': 'some-other-role', 'context': str(TEST_ENTERPRISE_UUID)},
            status.HTTP_403_FORBIDDEN,
        ),
        # The right role, but in a context/customer we don't have, get's you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(uuid4())},
            status.HTTP_403_FORBIDDEN,
        ),
        # A learner role is also fine, but in a context/customer we don't have, get's you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(uuid4())},
            status.HTTP_403_FORBIDDEN,
        ),
        # An operator role is fine, too, but in a context/customer we don't have, get's you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(uuid4())},
            status.HTTP_403_FORBIDDEN,
        ),
        # No JWT based auth, no soup for you.
        (
            None,
            status.HTTP_401_UNAUTHORIZED,
        ),
    )
    @ddt.unpack
    def test_policy_redemption_forbidden_requests(self, role_context_dict, expected_response_code):
        """
        Tests that we get expected 403s for all of the policy redemption endpoints.
        """
        # Set the JWT-based auth that we'll use for every request
        if role_context_dict:
            self.set_jwt_cookie([role_context_dict])

        # The policy redemption list endpoint
        query_params = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'lms_user_id': '1234',
            'content_key': 'course-v1:edX+edXPrivacy101+3T2020',
        }
        response = self.client.get(reverse('api:v1:policy-redemption'), query_params)
        self.assertEqual(response.status_code, expected_response_code)

        # The redeem endpoint
        url = reverse('api:v1:policy-redeem', kwargs={'policy_uuid': self.redeemable_policy.uuid})
        payload = {
            'lms_user_id': '1234',
            'content_key': 'course-v1:edX+edXPrivacy101+3T2020',
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, expected_response_code)

        # The credits_available endpoint
        query_params = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'lms_user_id': 1234,
        }
        response = self.client.get(reverse('api:v1:policy-credits-available'), query_params)
        self.assertEqual(response.status_code, expected_response_code)

        # The can_redeem endpoint
        url = reverse(
            "api:v1:policy-can-redeem",
            kwargs={"enterprise_customer_uuid": self.enterprise_uuid},
        )
        query_params = {
            'content_key': ['course-v1:edX+edXPrivacy101+3T2020', 'course-v1:edX+edXPrivacy101+3T2020_2'],
        }
        response = self.client.get(url, query_params)
        self.assertEqual(response.status_code, expected_response_code)


@ddt.ddt
class TestSubsidyAccessPolicyRedeemViewset(APITestWithMocks):
    """
    Tests for SubsidyAccessPolicyRedeemViewset.
    """

    def setUp(self):
        super().setUp()

        self.enterprise_uuid = '12aacfee-8ffa-4cb3-bed1-059565a57f06'

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': self.enterprise_uuid,
        }])

        self.redeemable_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            spend_limit=500000,
        )
        self.non_redeemable_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory()

        self.subsidy_access_policy_redeem_endpoint = reverse(
            'api:v1:policy-redeem',
            kwargs={'policy_uuid': self.redeemable_policy.uuid}
        )
        self.subsidy_access_policy_redemption_endpoint = reverse('api:v1:policy-redemption')
        self.subsidy_access_policy_credits_available_endpoint = reverse('api:v1:policy-credits-available')
        self.subsidy_access_policy_can_redeem_endpoint = reverse(
            "api:v1:policy-can-redeem",
            kwargs={"enterprise_customer_uuid": self.enterprise_uuid},
        )
        self.setup_mocks()

    def setup_mocks(self):
        """
        Setup mocks for different api clients.
        """
        subsidy_client_path = (
            'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.subsidy_client'
        )
        subsidy_client_patcher = mock.patch(subsidy_client_path)
        subsidy_client = subsidy_client_patcher.start()
        subsidy_client.can_redeem.return_value = True
        subsidy_client.list_subsidy_transactions.return_value = {"results": [], "aggregates": {}}
        subsidy_client.retrieve_subsidy_transaction.side_effect = (
            NotImplementedError("unit test must override retrieve_subsidy_transaction to use.")
        )
        subsidy_client.create_subsidy_transaction.side_effect = (
            NotImplementedError("unit test must override create_subsidy_transaction to use.")
        )

        catalog_client_path = 'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.catalog_client'
        enterprise_catalog_client_patcher = mock.patch(catalog_client_path)
        enterprise_catalog_client = enterprise_catalog_client_patcher.start()
        enterprise_catalog_client.contains_content_items.return_value = True

        lms_client_patcher = mock.patch('enterprise_access.apps.subsidy_access_policy.models.LmsApiClient')
        lms_client = lms_client_patcher.start()
        lms_client_instance = lms_client.return_value
        lms_client_instance.enterprise_contains_learner.return_value = True

        self.addCleanup(lms_client_patcher.stop)
        self.addCleanup(subsidy_client_patcher.stop)
        self.addCleanup(enterprise_catalog_client_patcher.stop)

    @mock.patch(
        'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.get_content_price',
        return_value=100,
    )
    def test_list(self, mock_get_content_price):
        """
        list endpoint should return only the redeemable policy, and also check the serialized output fields.
        """
        query_params = {
            'enterprise_customer_uuid': self.enterprise_uuid,
            'lms_user_id': '1234',
            'content_key': 'course-v1:edX+edXPrivacy101+3T2020',
        }
        response = self.client.get(SUBSIDY_ACCESS_POLICY_LIST_ENDPOINT, query_params)
        response_json = self.load_json(response.content)

        # Response should only include the one redeemable policy.
        assert len(response_json) == 1
        assert response_json[0]["uuid"] == str(self.redeemable_policy.uuid)

        # Check remainder of serialized fields.
        assert response_json[0]["policy_type"] == 'PerLearnerEnrollmentCreditAccessPolicy'
        assert response_json[0]["access_method"] == self.redeemable_policy.access_method
        assert response_json[0]["active"] == self.redeemable_policy.active
        assert response_json[0]["catalog_uuid"] == str(self.redeemable_policy.catalog_uuid)
        assert response_json[0]["description"] == self.redeemable_policy.description
        assert response_json[0]["enterprise_customer_uuid"] == str(self.enterprise_uuid)
        assert response_json[0]["group_uuid"] == str(self.redeemable_policy.group_uuid)
        assert response_json[0]["per_learner_enrollment_limit"] == self.redeemable_policy.per_learner_enrollment_limit
        assert response_json[0]["per_learner_spend_limit"] == self.redeemable_policy.per_learner_spend_limit
        assert response_json[0]["spend_limit"] == self.redeemable_policy.spend_limit
        assert response_json[0]["subsidy_uuid"] == str(self.redeemable_policy.subsidy_uuid)
        assert re.fullmatch(
            f"http.*/api/v1/policy/{self.redeemable_policy.uuid}/redeem/",
            response_json[0]["policy_redemption_url"],
        )
        mock_get_content_price.assert_called_once_with(query_params['content_key'])

    @ddt.data(
        (
            {
                'enterprise_customer_uuid': '12aacfee-8ffa-4cb3-bed1-059565a57f06'
            },
            {
                'content_key': ['This field is required.'],
                'lms_user_id': ['This field is required.']
            }
        ),
        (
            {
                'enterprise_customer_uuid': '12aacfee-8ffa-4cb3-bed1-059565a57f06',
                'lms_user_id': '1234',
                'content_key': 'invalid_content_key',
            },
            {'content_key': ['Invalid content_key: invalid_content_key']}
        ),
        (
            {
                'lms_user_id': '1234',
                'content_key': 'content_key',
            },
            {'detail': f'MISSING: {POLICY_REDEMPTION_PERMISSION}'}
        )
    )
    @ddt.unpack
    def test_list_endpoint_with_invalid_data(self, query_params, expected_result):
        """
        Verify that SubsidyAccessPolicyRedeemViewset list raises correct exception if request data is invalid.
        """
        response = self.client.get(SUBSIDY_ACCESS_POLICY_LIST_ENDPOINT, query_params)
        response_json = self.load_json(response.content)

        assert response_json == expected_result

    @mock.patch(
        'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.get_content_price',
        return_value=200,
    )
    def test_redeem_policy(self, mock_get_content_price):
        """
        Verify that SubsidyAccessPolicyRedeemViewset redeem endpoint works as expected
        """
        mock_transaction_record = {
            'uuid': str(uuid4()),
            'state': TransactionStateChoices.COMMITTED,
            'other': True,
        }
        # HTTPError would be caused by a 404, indicating that a transaction does not already exist for this policy.
        self.redeemable_policy.subsidy_client.retrieve_subsidy_transaction.side_effect = HTTPError()
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.side_effect = None
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.return_value = mock_transaction_record
        payload = {
            'lms_user_id': '1234',
            'content_key': 'course-v1:edX+edXPrivacy101+3T2020',
        }

        response = self.client.post(self.subsidy_access_policy_redeem_endpoint, payload)

        response_json = self.load_json(response.content)
        assert response_json == mock_transaction_record
        mock_get_content_price.assert_called_once_with(payload['content_key'])

    @mock.patch(
        'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.get_content_price',
        return_value=200,
    )
    def test_redeem_policy_with_metadata(self, mock_get_content_price):
        """
        Verify that SubsidyAccessPolicyRedeemViewset redeem endpoint works as expected
        """
        mock_transaction_record = {
            'uuid': str(uuid4()),
            'status': 'committed',
            'other': True,
        }
        # HTTPError would be caused by a 404, indicating that a transaction does not already exist for this policy.
        self.redeemable_policy.subsidy_client.retrieve_subsidy_transaction.side_effect = HTTPError()
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.side_effect = None
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.return_value = mock_transaction_record
        payload = {
            'lms_user_id': '1234',
            'content_key': 'course-v1:edX+edXPrivacy101+3T2020',
            'metadata': {
                'geag_first_name': 'John'
            }
        }

        response = self.client.post(self.subsidy_access_policy_redeem_endpoint, payload)

        response_json = self.load_json(response.content)
        assert response_json == mock_transaction_record
        mock_get_content_price.assert_called_once_with(payload['content_key'])

    def test_redemption_endpoint(self):
        """
        Verify that SubsidyAccessPolicyViewset redemption endpoint works as expected
        """
        mock_transaction_record = {
            'uuid': str(uuid4()),
            'state': TransactionStateChoices.COMMITTED,
            'other': True,
            'content_key': 'course-v1:edX+test+courserun',
        }
        self.redeemable_policy.subsidy_client.list_subsidy_transactions.return_value = {
            'results': [
                mock_transaction_record
            ],
            'aggregates': {
                'total_quantity': 100,
            },
        }
        query_params = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'lms_user_id': '1234',
            'content_key': 'course-v1:edX+edXPrivacy101+3T2020',
        }
        response = self.client.get(self.subsidy_access_policy_redemption_endpoint, query_params)
        response_json = self.load_json(response.content)
        assert response_json == {
            str(self.redeemable_policy.uuid): [mock_transaction_record],
        }

    def test_credits_available_endpoint(self):
        """
        Verify that SubsidyAccessPolicyViewset credits_available returns credit based policies with redeemable credit.
        """
        mock_transaction_record = {
            'uuid': str(uuid4()),
            'state': TransactionStateChoices.COMMITTED,
            'other': True,
        }
        self.redeemable_policy.subsidy_client.list_subsidy_transactions.return_value = {
            'results': [
                mock_transaction_record
            ],
            'aggregates': {
                'total_quantity': 0,
            },
        }
        enroll_cap_policy = PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            per_learner_enrollment_limit=5
        )
        spend_cap_policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            per_learner_spend_limit=5
        )

        query_params = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'lms_user_id': 1234,
        }
        response = self.client.get(self.subsidy_access_policy_credits_available_endpoint, query_params)

        response_json = response.json()
        # self.redeemable_policy, along with the 2 instances created from factories above,
        # should give us a total of 3 policy records with credits available.
        assert len(response_json) == 3
        redeemable_policy_uuids = {self.redeemable_policy.uuid, enroll_cap_policy.uuid, spend_cap_policy.uuid}
        actual_uuids = {UUID(policy['uuid']) for policy in response_json}
        self.assertEqual(redeemable_policy_uuids, actual_uuids)

    def test_credits_available_endpoint_with_non_redeemable_policies(self):
        """
        Verify that SubsidyAccessPolicyViewset credits_available does not return policies for which the per user credit
        limits have already exceeded.
        """
        mock_transaction_record = {
            'uuid': str(uuid4()),
            'state': TransactionStateChoices.COMMITTED,
            'other': True,
        }
        self.redeemable_policy.subsidy_client.list_subsidy_transactions.return_value = {
            'results': [
                mock_transaction_record
            ],
            'aggregates': {
                'total_quantity': 100,
            },
        }
        PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            per_learner_enrollment_limit=1
        )
        PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            per_learner_spend_limit=1
        )

        query_params = {
            'enterprise_customer_uuid': self.enterprise_uuid,
            'lms_user_id': '1234',
        }
        response = self.client.get(self.subsidy_access_policy_credits_available_endpoint, query_params)

        response_json = self.load_json(response.content)
        # only returns 1 policy created in the setup
        assert len(response_json) == 1

    def test_can_redeem_policy_missing_params(self):
        """
        Test that the can_redeem endpoint returns an access policy when one is redeemable.
        """
        self.redeemable_policy.subsidy_client.list_subsidy_transactions.return_value = {
            'results': [],
            'aggregates': {
                'total_quantity': 0,
            },
        }
        query_params = {}  # Test what happens when we fail to supply a list of content_keys.
        response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"content_key": ["This field is required."]}

    def test_can_redeem_policy(self):
        """
        Test that the can_redeem endpoint returns an access policy when one is redeemable.
        """
        self.redeemable_policy.subsidy_client.list_subsidy_transactions.return_value = {
            'results': [],
            'aggregates': {
                'total_quantity': 0,
            },
        }
        test_content_key_1 = "course-v1:edX+edXPrivacy101+3T2020"
        test_content_key_2 = "course-v1:edX+edXPrivacy101+3T2020_2"
        test_content_key_1_metadata_price = 29900
        test_content_key_2_metadata_price = 81900
        test_content_key_1_usd_price = 299
        test_content_key_2_usd_price = 819
        test_content_key_1_cents_price = 29900
        test_content_key_2_cents_price = 81900

        def mock_get_subsidy_content_data(*args):
            if test_content_key_1 in args:
                return {
                    "content_uuid": str(uuid4()),
                    "content_key": test_content_key_1,
                    "source": "edX",
                    "content_price": test_content_key_1_metadata_price,
                }
            elif test_content_key_2 in args:
                return {
                    "content_uuid": str(uuid4()),
                    "content_key": test_content_key_2,
                    "source": "edX",
                    "content_price": test_content_key_2_metadata_price,
                }
            else:
                return {}

        mock_subsidy_client = mock.Mock()
        mock_subsidy_client.get_subsidy_content_data.side_effect = mock_get_subsidy_content_data
        SubsidyAccessPolicyRedeemViewset.subsidy_client = mock_subsidy_client
        self.redeemable_policy.subsidy_client.get_subsidy_content_data.side_effect = mock_get_subsidy_content_data

        query_params = {
            'content_key': [test_content_key_1, test_content_key_2],
        }
        response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)
        assert response.status_code == status.HTTP_200_OK
        response_list = response.json()

        # Make sure we got responses for all two content_keys requested.
        assert len(response_list) == 2

        # Check the response for the first content_key given.
        assert response_list[0]["content_key"] == test_content_key_1
        assert response_list[0]["list_price"] == {
            "usd": test_content_key_1_usd_price,
            "usd_cents": test_content_key_1_cents_price,
        }
        assert len(response_list[0]["redemptions"]) == 0
        assert response_list[0]["has_successful_redemption"] is False
        assert response_list[0]["redeemable_subsidy_access_policy"]["uuid"] == str(self.redeemable_policy.uuid)
        assert response_list[0]["can_redeem"] is True
        assert len(response_list[0]["reasons"]) == 0

        # Check the response for the second content_key given.
        assert response_list[1]["content_key"] == test_content_key_2
        assert response_list[1]["list_price"] == {
            "usd": test_content_key_2_usd_price,
            "usd_cents": test_content_key_2_cents_price,
        }
        assert len(response_list[1]["redemptions"]) == 0
        assert response_list[1]["has_successful_redemption"] is False
        assert response_list[1]["redeemable_subsidy_access_policy"]["uuid"] == str(self.redeemable_policy.uuid)
        assert response_list[1]["can_redeem"] is True
        assert len(response_list[1]["reasons"]) == 0

    @mock.patch('enterprise_access.apps.api.v1.views.subsidy_access_policy.LmsApiClient', return_value=mock.MagicMock())
    @ddt.data(
        {"has_admin_users": True},
        {"has_admin_users": False},
    )
    @ddt.unpack
    def test_can_redeem_policy_none_redeemable(self, mock_lms_client, has_admin_users):
        """
        Test that the can_redeem endpoint returns resons for why each non-redeemable policy failed.
        """
        slug = 'sluggy'
        admin_email = 'edx@example.org'
        mock_lms_client().get_enterprise_customer_data.return_value = {
            'slug': slug,
            'admin_users': [{'email': admin_email}] if has_admin_users else [],
        }

        self.redeemable_policy.subsidy_client.list_subsidy_transactions.return_value = {
            'results': [],
            'aggregates': {
                'total_quantity': 0,
            },
        }
        self.redeemable_policy.subsidy_client.can_redeem.return_value = False
        test_content_key_1 = "course-v1:edX+edXPrivacy101+3T2020"
        test_content_key_2 = "course-v1:edX+edXPrivacy101+3T2020_2"
        test_content_key_1_metadata_price = 29900
        test_content_key_2_metadata_price = 81900
        test_content_key_1_usd_price = 299
        test_content_key_2_usd_price = 819
        test_content_key_1_cents_price = 29900
        test_content_key_2_cents_price = 81900

        def mock_get_subsidy_content_data(*args, **kwargs):
            if test_content_key_1 in args:
                return {
                    "content_uuid": str(uuid4()),
                    "content_key": test_content_key_1,
                    "source": "edX",
                    "content_price": test_content_key_1_metadata_price,
                }
            elif test_content_key_2 in args:
                return {
                    "content_uuid": str(uuid4()),
                    "content_key": test_content_key_2,
                    "source": "edX",
                    "content_price": test_content_key_2_metadata_price,
                }
            else:
                return {}

        mock_subsidy_client = mock.Mock()
        mock_subsidy_client.get_subsidy_content_data.side_effect = mock_get_subsidy_content_data
        SubsidyAccessPolicyRedeemViewset.subsidy_client = mock_subsidy_client
        query_params = {
            'content_key': [test_content_key_1, test_content_key_2],
        }
        response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)
        assert response.status_code == status.HTTP_200_OK
        response_list = response.json()

        # Make sure we got responses for all two content_keys requested.
        assert len(response_list) == 2

        # Check the response for the first content_key given.
        assert response_list[0]["content_key"] == test_content_key_1
        assert response_list[0]["list_price"] == {
            "usd": test_content_key_1_usd_price,
            "usd_cents": test_content_key_1_cents_price,
        }
        assert len(response_list[0]["redemptions"]) == 0
        assert response_list[0]["has_successful_redemption"] is False
        assert response_list[0]["redeemable_subsidy_access_policy"] is None
        assert response_list[0]["can_redeem"] is False

        expected_user_message = (
            MissingSubsidyAccessReasonUserMessages.ORGANIZATION_NO_FUNDS
            if has_admin_users
            else MissingSubsidyAccessReasonUserMessages.ORGANIZATION_NO_FUNDS_NO_ADMINS
        )
        expected_enterprise_admins = [{'email': admin_email}] if has_admin_users else []

        assert response_list[0]["reasons"] == [
            {
                "reason": REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY,
                "user_message": expected_user_message,
                "metadata": {
                    "enterprise_administrators": expected_enterprise_admins,
                },
                "policy_uuids": [str(self.redeemable_policy.uuid)],
            },
        ]

        # Check the response for the second content_key given.
        assert response_list[1]["content_key"] == test_content_key_2
        assert response_list[1]["list_price"] == {
            "usd": test_content_key_2_usd_price,
            "usd_cents": test_content_key_2_cents_price,
        }
        assert len(response_list[1]["redemptions"]) == 0
        assert response_list[1]["has_successful_redemption"] is False
        assert response_list[1]["redeemable_subsidy_access_policy"] is None
        assert response_list[1]["can_redeem"] is False
        assert response_list[1]["reasons"] == [
            {
                "reason": REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY,
                "user_message": expected_user_message,
                "metadata": {
                    "enterprise_administrators": expected_enterprise_admins,
                },
                "policy_uuids": [str(self.redeemable_policy.uuid)],
            },
        ]

    @mock.patch('enterprise_access.apps.api.v1.views.SubsidyAccessPolicyRedeemViewset.subsidy_client')
    @mock.patch(
        'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.get_content_price',
        return_value=19900,
    )
    def test_can_redeem_policy_existing_redemptions(self, mock_get_content_price, mock_view_subsidy_client):
        """
        Test that the can_redeem endpoint shows existing redemptions too.
        """
        test_transaction_uuid = str(uuid4())
        self.redeemable_policy.subsidy_client.list_subsidy_transactions.return_value = {
            "results": [{
                "uuid": test_transaction_uuid,
                "state": TransactionStateChoices.COMMITTED,
                "idempotency_key": "the-idempotency-key",
                "lms_user_id": self.user.lms_user_id,
                "content_key": "course-v1:demox+1234+2T2023",
                "quantity": -19900,
                "unit": "USD_CENTS",
                "enterprise_fulfillment_uuid": "6ff2c1c9-d5fc-48a8-81da-e6a675263f67",
                "subsidy_access_policy_uuid": str(self.redeemable_policy.uuid),
                "metadata": {},
                "reversals": [],
            }],
            "aggregates": {
                "total_quantity": -19900,
            },
        }

        mock_view_subsidy_client.get_subsidy_content_data.return_value = {
            "content_uuid": str(uuid4()),
            "content_key": "course-v1:demox+1234+2T2023",
            "source": "edX",
            "content_price": 19900,
        }

        query_params = {'content_key': 'course-v1:demox+1234+2T2023'}
        response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)
        assert response.status_code == status.HTTP_200_OK
        response_list = response.json()

        # Make sure we got responses containing existing redemptions.
        assert len(response_list) == 1
        assert response_list[0]["content_key"] == query_params["content_key"]
        assert response_list[0]["list_price"] == {
            "usd": 199.00,
            "usd_cents": 19900,
        }
        assert len(response_list[0]["redemptions"]) == 1
        assert response_list[0]["redemptions"][0]["uuid"] == test_transaction_uuid
        assert response_list[0]["redemptions"][0]["policy_redemption_status_url"] == \
            f"{settings.ENTERPRISE_SUBSIDY_URL}/api/v1/transactions/{test_transaction_uuid}/"
        assert response_list[0]["redemptions"][0]["courseware_url"] == \
            f"{settings.LMS_URL}/courses/course-v1:demox+1234+2T2023/courseware/"
        assert response_list[0]["has_successful_redemption"] is True
        assert response_list[0]["redeemable_subsidy_access_policy"]["uuid"] == str(self.redeemable_policy.uuid)
        assert response_list[0]["can_redeem"] is True
        assert len(response_list[0]["reasons"]) == 0
        mock_get_content_price.assert_called_once_with(query_params['content_key'])

    @mock.patch('enterprise_access.apps.api.v1.views.SubsidyAccessPolicyRedeemViewset.subsidy_client')
    @mock.patch('enterprise_access.apps.api.v1.views.subsidy_access_policy.LmsApiClient')
    @mock.patch(
        'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.get_content_price',
    )
    def test_can_redeem_policy_no_price(self, mock_get_content_price, mock_lms_client, mock_view_subsidy_client):
        """
        Test that the can_redeem endpoint successfuly serializes a response for content that has no price.
        """
        test_content_key = "course-v1:demox+1234+2T2023"
        mock_lms_client.return_value.get_enterprise_customer_data.return_value = {
            'slug': 'sluggy',
            'admin_users': [{'email': 'edx@example.org'}],
        }

        mock_get_content_price.side_effect = ContentPriceNullException

        # FIXME: subisdy client's can_redeem() function returns a dict in reality, so when we fix this in the policy
        # model code, fix it here too by making it return a dictionary with a `can_redeem` key set to a value of False.
        self.redeemable_policy.subsidy_client.can_redeem.return_value = True
        self.redeemable_policy.subsidy_client.list_subsidy_transactions.return_value = {
            'results': [],
            'aggregates': {
                'total_quantity': 0,
            },
        }

        mock_view_subsidy_client.get_subsidy_content_data.return_value = {
            "content_uuid": str(uuid4()),
            "content_key": test_content_key,
            "source": "edX",
            "content_price": None,
        }

        query_params = {'content_key': test_content_key}

        response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.json() == {
            'detail': f'Could not determine price for content_key: {test_content_key}',
        }
