"""
Tests for Enterprise Access Subsidy Access Policy app API v1 views.
"""
import re
from unittest.mock import patch
from uuid import UUID, uuid4

import ddt
import mock
from django.conf import settings
from requests.exceptions import HTTPError
from rest_framework import status
from rest_framework.reverse import reverse

from enterprise_access.apps.core.constants import SYSTEM_ENTERPRISE_LEARNER_ROLE
from enterprise_access.apps.subsidy_access_policy.constants import (
    REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY,
    MissingSubsidyAccessReasonUserMessages,
    TransactionStateChoices
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory,
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory
)

from .utils import BaseEnterpriseAccessTestCase

SUBSIDY_ACCESS_POLICY_LIST_ENDPOINT = reverse('api:v1:policy-list')
SUBSIDY_ACCESS_POLICY_ADMIN_LIST_ENDPOINT = reverse('api:v1:admin-policy-list')


@ddt.ddt
class TestSubsidyAccessPolicyRedeemViewset(BaseEnterpriseAccessTestCase):
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
            spend_limit=3
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
        get_subsidy_client_path = (
            'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.get_subsidy_client'
        )
        get_subsidy_client_patcher = patch(get_subsidy_client_path)
        get_subsidy_client = get_subsidy_client_patcher.start()
        get_subsidy_client.return_value.can_redeem.return_value = True
        get_subsidy_client.return_value.transactions_for_learner.return_value = 2
        get_subsidy_client.return_value.amount_spent_for_learner.return_value = 2
        get_subsidy_client.return_value.amount_spent_for_group_and_catalog.return_value = 2
        get_subsidy_client.return_value.get_current_balance.return_value = 10
        get_subsidy_client.return_value.list_subsidy_transactions.return_value = {"results": [], "aggregates": []}
        get_subsidy_client.return_value.retrieve_subsidy_transaction.side_effect = \
            NotImplementedError("unit test must override retrieve_subsidy_transaction to use.")
        get_subsidy_client.return_value.create_subsidy_transaction.side_effect = \
            NotImplementedError("unit test must override create_subsidy_transaction to use.")

        catalog_client_path = 'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.catalog_client'
        enterprise_catalog_client_patcher = patch(catalog_client_path)
        enterprise_catalog_client = enterprise_catalog_client_patcher.start()
        enterprise_catalog_client_instance = enterprise_catalog_client.return_value
        enterprise_catalog_client_instance.contains_content_items.return_value = True

        lms_client_patcher = patch('enterprise_access.apps.subsidy_access_policy.models.LmsApiClient')
        lms_client = lms_client_patcher.start()
        lms_client_instance = lms_client.return_value
        lms_client_instance.enterprise_contains_learner.return_value = True

        self.addCleanup(lms_client_patcher.stop)
        self.addCleanup(get_subsidy_client_patcher.stop)
        self.addCleanup(enterprise_catalog_client_patcher.stop)

    def test_list(self):
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
            {'detail': 'MISSING: requests.has_learner_or_admin_access'}
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

    def test_redeem_policy(self):
        """
        Verify that SubsidyAccessPolicyRedeemViewset redeem endpoint works as expected
        """
        mock_transaction_record = {
            'uuid': str(uuid4()),
            'state': TransactionStateChoices.COMMITTED,
            'other': True,
        }
        # HTTPError would be caused by a 404, indicating that a transaction does not already exist for this policy.
        self.redeemable_policy.get_subsidy_client.return_value.retrieve_subsidy_transaction.side_effect = HTTPError()
        self.redeemable_policy.get_subsidy_client.return_value.create_subsidy_transaction.side_effect = None
        self.redeemable_policy.get_subsidy_client.return_value.create_subsidy_transaction.return_value = \
            mock_transaction_record
        payload = {
            'lms_user_id': '1234',
            'content_key': 'course-v1:edX+edXPrivacy101+3T2020',
        }
        response = self.client.post(self.subsidy_access_policy_redeem_endpoint, payload)
        response_json = self.load_json(response.content)
        assert response_json == mock_transaction_record

    def test_redeem_policy_with_metadata(self):
        """
        Verify that SubsidyAccessPolicyRedeemViewset redeem endpoint works as expected
        """
        mock_transaction_record = {
            'uuid': str(uuid4()),
            'status': 'committed',
            'other': True,
        }
        # HTTPError would be caused by a 404, indicating that a transaction does not already exist for this policy.
        self.redeemable_policy.get_subsidy_client.return_value.retrieve_subsidy_transaction.side_effect = HTTPError()
        self.redeemable_policy.get_subsidy_client.return_value.create_subsidy_transaction.side_effect = None
        self.redeemable_policy.get_subsidy_client.return_value.create_subsidy_transaction.return_value = \
            mock_transaction_record
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
        query_params = {
            'content_key': ['course-v1:edX+edXPrivacy101+3T2020', 'course-v1:edX+edXPrivacy101+3T2020_2'],
        }
        response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)
        assert response.status_code == status.HTTP_200_OK
        response_list = response.json()

        # Make sure we got responses for all two content_keys requested.
        assert len(response_list) == 2

        # Check the response for the first content_key given.
        assert response_list[0]["content_key"] == query_params["content_key"][0]
        assert len(response_list[0]["redemptions"]) == 0
        assert response_list[0]["has_successful_redemption"] is False
        assert response_list[0]["redeemable_subsidy_access_policy"]["uuid"] == str(self.redeemable_policy.uuid)
        assert response_list[0]["can_redeem"] is True
        assert len(response_list[0]["reasons"]) == 0

        # Check the response for the second content_key given.
        assert response_list[1]["content_key"] == query_params["content_key"][1]
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
        query_params = {
            'content_key': ['course-v1:edX+edXPrivacy101+3T2020', 'course-v1:edX+edXPrivacy101+3T2020_2'],
        }
        response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)
        assert response.status_code == status.HTTP_200_OK
        response_list = response.json()

        # Make sure we got responses for all two content_keys requested.
        assert len(response_list) == 2

        # Check the response for the first content_key given.
        assert response_list[0]["content_key"] == query_params["content_key"][0]
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
        assert response_list[1]["content_key"] == query_params["content_key"][1]
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

    def test_can_redeem_policy_existing_redemptions(self):
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
        query_params = {'content_key': 'course-v1:demox+1234+2T2023'}
        response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)
        assert response.status_code == status.HTTP_200_OK
        response_list = response.json()

        # Make sure we got responses containing existing redemptions.
        assert len(response_list) == 1
        assert response_list[0]["content_key"] == query_params["content_key"]
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
