"""
Tests for Enterprise Access Subsidy Access Policy app API v1 views.
"""
from operator import itemgetter
from unittest import mock
from uuid import UUID, uuid4

import ddt
from django.conf import settings
from rest_framework import status
from rest_framework.reverse import reverse

from enterprise_access.apps.core.constants import (
    SYSTEM_ENTERPRISE_ADMIN_ROLE,
    SYSTEM_ENTERPRISE_LEARNER_ROLE,
    SYSTEM_ENTERPRISE_OPERATOR_ROLE
)
from enterprise_access.apps.subsidy_access_policy.constants import (
    REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY,
    AccessMethods,
    MissingSubsidyAccessReasonUserMessages,
    PolicyTypes,
    TransactionStateChoices
)
from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory,
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory
)
from enterprise_access.apps.subsidy_access_policy.utils import create_idempotency_key_for_transaction
from test_utils import APITestWithMocks

SUBSIDY_ACCESS_POLICY_DEPR_LIST_ENDPOINT = reverse('api:v1:admin-policy-list')
SUBSIDY_ACCESS_POLICY_LIST_ENDPOINT = reverse('api:v1:subsidy-access-policies-list')

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
        # A good admin role, even with the correct context/customer, gets you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
            status.HTTP_403_FORBIDDEN,
        ),
        # A good learner role, but in a context/customer we're not aware of, gets you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(uuid4())},
            status.HTTP_403_FORBIDDEN,
        ),
        # A good learner role, even with the correct context/customer, gets you a 403.
        (
            {'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE, 'context': str(TEST_ENTERPRISE_UUID)},
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
    def test_policy_crud_write_views_unauthorized_forbidden(self, role_context_dict, expected_response_code):
        """
        Tests that we get expected 40x responses for all of the policy write views.
        """
        # Set the JWT-based auth that we'll use for every request
        if role_context_dict:
            self.set_jwt_cookie([role_context_dict])

        request_kwargs = {'uuid': str(self.redeemable_policy.uuid)}

        # Test the create endpoint.
        response = self.client.post(
            SUBSIDY_ACCESS_POLICY_LIST_ENDPOINT,
            data={'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID)},
        )
        self.assertEqual(response.status_code, expected_response_code)

        # Test the delete endpoint.
        response = self.client.delete(reverse('api:v1:subsidy-access-policies-detail', kwargs=request_kwargs))
        self.assertEqual(response.status_code, expected_response_code)

        # Test the update and partial_update views.
        response = self.client.put(reverse('api:v1:subsidy-access-policies-detail', kwargs=request_kwargs))
        self.assertEqual(response.status_code, expected_response_code)

        response = self.client.patch(reverse('api:v1:subsidy-access-policies-detail', kwargs=request_kwargs))
        self.assertEqual(response.status_code, expected_response_code)


@ddt.ddt
class TestAuthenticatedPolicyCRUDViews(CRUDViewTestMixin, APITestWithMocks):
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

    @ddt.data(
        {
            'request_payload': {'reason': 'Peer Pressure.'},
            'expected_change_reason': 'Peer Pressure.',
        },
        {
            'request_payload': {'reason': ''},
            'expected_change_reason': None,
        },
        {
            'request_payload': {'reason': None},
            'expected_change_reason': None,
        },
        {
            'request_payload': {},
            'expected_change_reason': None,
        },
    )
    @ddt.unpack
    def test_destroy_view(self, request_payload, expected_change_reason):
        """
        Test that the destroy view performs a soft-delete and returns an appropriate response with 200 status code and
        the expected results of serialization.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        # Test the destroy endpoint
        response = self.client.delete(
            reverse('api:v1:subsidy-access-policies-detail', kwargs={'uuid': str(self.redeemable_policy.uuid)}),
            request_payload,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected_response = {
            'access_method': 'direct',
            'active': False,
            'catalog_uuid': str(self.redeemable_policy.catalog_uuid),
            'description': '',
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'per_learner_enrollment_limit': self.redeemable_policy.per_learner_enrollment_limit,
            'per_learner_spend_limit': self.redeemable_policy.per_learner_spend_limit,
            'policy_type': 'PerLearnerEnrollmentCreditAccessPolicy',
            'spend_limit': 3,
            'subsidy_uuid': str(self.redeemable_policy.subsidy_uuid),
            'uuid': str(self.redeemable_policy.uuid),
        }
        self.assertEqual(expected_response, response.json())

        # Check that the latest history record for this policy contains the change reason provided via the API.
        self.redeemable_policy.refresh_from_db()
        assert self.redeemable_policy.history.order_by('-history_date').first().history_change_reason \
            == expected_change_reason

        # Test idempotency of the destroy endpoint.
        response = self.client.delete(
            reverse('api:v1:subsidy-access-policies-detail', kwargs={'uuid': str(self.redeemable_policy.uuid)}),
            request_payload,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(expected_response, response.json())

    @ddt.data(True, False)
    def test_update_views(self, is_patch):
        """
        Test that the update and partial_update views can modify certain
        fields of a policy record.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        policy_for_edit = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            spend_limit=5,
            active=False,
        )

        request_payload = {
            'description': 'the new description',
            'active': True,
            'catalog_uuid': str(uuid4()),
            'subsidy_uuid': str(uuid4()),
            'access_method': AccessMethods.ASSIGNED,
            'spend_limit': None,
            'per_learner_spend_limit': 10000,
        }

        action = self.client.patch if is_patch else self.client.put
        url = reverse(
            'api:v1:subsidy-access-policies-detail',
            kwargs={'uuid': str(policy_for_edit.uuid)}
        )
        response = action(url, data=request_payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_response = {
            'access_method': AccessMethods.ASSIGNED,
            'active': True,
            'catalog_uuid': request_payload['catalog_uuid'],
            'description': request_payload['description'],
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'per_learner_enrollment_limit': None,
            'per_learner_spend_limit': request_payload['per_learner_spend_limit'],
            'policy_type': 'PerLearnerSpendCreditAccessPolicy',
            'spend_limit': request_payload['spend_limit'],
            'subsidy_uuid': request_payload['subsidy_uuid'],
            'uuid': str(policy_for_edit.uuid),
        }
        self.assertEqual(expected_response, response.json())

    @ddt.data(
        {
            'enterprise_customer_uuid': str(uuid4()),
            'uuid': str(uuid4()),
            'policy_type': 'PerLearnerEnrollmentCapCreditAccessPolicy',
            'created': '1970-01-01 12:00:00Z',
            'modified': '1970-01-01 12:00:00Z',
            'nonsense_key': 'ship arriving too late to save a drowning witch',
        },
    )
    def test_update_views_fields_disallowed_for_update(self, request_payload):
        """
        Test that the update and partial_update views can NOT modify fields
        of a policy record that are not included in the update request serializer fields defintion.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        policy_for_edit = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            enterprise_customer_uuid=self.enterprise_uuid,
            spend_limit=5,
            active=False,
        )
        url = reverse(
            'api:v1:subsidy-access-policies-detail',
            kwargs={'uuid': str(policy_for_edit.uuid)}
        )

        expected_unknown_keys = ", ".join(sorted(request_payload.keys()))

        # Test the PUT view
        response = self.client.put(url, data=request_payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.json(),
            {'non_field_errors': [f'Field(s) are not updatable: {expected_unknown_keys}']},
        )

        # Test the PATCH view
        response = self.client.patch(url, data=request_payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.assertEqual(
            response.json(),
            {'non_field_errors': [f'Field(s) are not updatable: {expected_unknown_keys}']},
        )

    @ddt.data(
        {
            'policy_class': PerLearnerSpendCapLearnerCreditAccessPolicyFactory,
            'request_payload': {
                'per_learner_enrollment_limit': 10,
            },
        },
        {
            'policy_class': PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory,
            'request_payload': {
                'per_learner_spend_limit': 1000,
            },
        },
    )
    @ddt.unpack
    def test_update_view_validates_fields_vs_policy_type(self, policy_class, request_payload):
        """
        Test that the update view can NOT modify fields
        of a policy record that are relevant only to a different
        type of policy.
        """
        # Set the JWT-based auth to an operator.
        self.set_jwt_cookie([
            {'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE, 'context': str(TEST_ENTERPRISE_UUID)}
        ])

        policy_for_edit = policy_class(
            enterprise_customer_uuid=self.enterprise_uuid,
            spend_limit=5,
            active=False,
        )
        url = reverse(
            'api:v1:subsidy-access-policies-detail',
            kwargs={'uuid': str(policy_for_edit.uuid)}
        )

        response = self.client.put(url, data=request_payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        expected_error_message = (
            f"Extraneous fields for {policy_for_edit.__class__.__name__} policy type: "
            f"{list(request_payload)}."
        )
        self.assertEqual(response.json(), [expected_error_message])


@ddt.ddt
class TestAdminPolicyCreateView(CRUDViewTestMixin, APITestWithMocks):
    """
    Test the create view for subsidy access policy records.
    This tests both the deprecated viewset and the preferred
    ``SubsidyAccessPolicyViewSet`` implementation.
    """

    @ddt.data(
        {
            'policy_type': PolicyTypes.PER_LEARNER_ENROLLMENT_CREDIT,
            'extra_fields': {
                'per_learner_enrollment_limit': None,
            },
            'expected_response_code': status.HTTP_201_CREATED,
            'expected_error_keywords': [],
        },
        {
            'policy_type': PolicyTypes.PER_LEARNER_ENROLLMENT_CREDIT,
            'extra_fields': {
                'per_learner_enrollment_limit': 10,
            },
            'expected_response_code': status.HTTP_201_CREATED,
            'expected_error_keywords': [],
        },
        {
            'policy_type': PolicyTypes.PER_LEARNER_SPEND_CREDIT,
            'extra_fields': {
                'per_learner_spend_limit': None,
            },
            'expected_response_code': status.HTTP_201_CREATED,
            'expected_error_keywords': [],
        },
        {
            'policy_type': PolicyTypes.PER_LEARNER_SPEND_CREDIT,
            'extra_fields': {
                'per_learner_spend_limit': 30000,
            },
            'expected_response_code': status.HTTP_201_CREATED,
            'expected_error_keywords': [],
        },
        {
            'policy_type': PolicyTypes.PER_LEARNER_ENROLLMENT_CREDIT,
            'extra_fields': {
                'per_learner_spend_limit': 30000,
            },
            'expected_response_code': status.HTTP_400_BAD_REQUEST,
            'expected_error_keywords': ['Missing fields', 'Extraneous fields'],
        },
        {
            'policy_type': PolicyTypes.PER_LEARNER_ENROLLMENT_CREDIT,
            'extra_fields': {
                'per_learner_spend_limit': 30000,
                'per_learner_enrollment_limit': 10,
            },
            'expected_response_code': status.HTTP_400_BAD_REQUEST,
            'expected_error_keywords': ['Extraneous fields'],
        },
        {
            'policy_type': PolicyTypes.PER_LEARNER_SPEND_CREDIT,
            'extra_fields': {
                'per_learner_enrollment_limit': 10,
            },
            'expected_response_code': status.HTTP_400_BAD_REQUEST,
            'expected_error_keywords': ['Missing fields', 'Extraneous fields'],
        },
        {
            'policy_type': PolicyTypes.PER_LEARNER_SPEND_CREDIT,
            'extra_fields': {
                'per_learner_enrollment_limit': 10,
                'per_learner_spend_limit': 30000,
            },
            'expected_response_code': status.HTTP_400_BAD_REQUEST,
            'expected_error_keywords': ['Extraneous fields'],
        },
    )
    @ddt.unpack
    def test_create_view(self, policy_type, extra_fields, expected_response_code, expected_error_keywords):
        """
        Test the (deprecated) policy create view.  make sure "extra" fields which pertain to the specific policy type
        are correctly validated for existence/non-existence.
        """
        # Set the JWT-based auth that we'll use for every request
        self.set_jwt_cookie([
            {
                'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE,
                'context': str(TEST_ENTERPRISE_UUID),
            },
        ])

        # Test the retrieve endpoint
        for create_url in (SUBSIDY_ACCESS_POLICY_DEPR_LIST_ENDPOINT, SUBSIDY_ACCESS_POLICY_LIST_ENDPOINT):
            payload = {
                'policy_type': policy_type,
                'description': 'test description',
                'active': True,
                'enterprise_customer_uuid': str(TEST_ENTERPRISE_UUID),
                'catalog_uuid': str(uuid4()),
                'subsidy_uuid': str(uuid4()),
                'access_method': AccessMethods.DIRECT,
                'spend_limit': None,
            }
            payload.update(extra_fields)
            response = self.client.post(create_url, payload)
            assert response.status_code == expected_response_code

            if expected_response_code == status.HTTP_201_CREATED:
                response_json = response.json()
                del response_json['uuid']
                expected_response = payload.copy()
                expected_response.setdefault("per_learner_enrollment_limit")
                expected_response.setdefault("per_learner_spend_limit")
                assert response_json == expected_response
            elif expected_response_code == status.HTTP_400_BAD_REQUEST:
                for expected_error_keyword in expected_error_keywords:
                    assert expected_error_keyword in response.content.decode("utf-8")

    @ddt.data(
        {
            'policy_type': PolicyTypes.PER_LEARNER_SPEND_CREDIT,
            'extra_fields': {
                'per_learner_spend_limit': 30000,
            },
            'expected_response_code': status.HTTP_201_CREATED,
        }
    )
    @ddt.unpack
    def test_idempotent_create_view(self, policy_type, extra_fields, expected_response_code):
        """
        Test the (deprecated) policy create view's idempotency.
        """
        # Set the JWT-based auth that we'll use for every request
        self.set_jwt_cookie([
            {
                'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE,
                'context': str(TEST_ENTERPRISE_UUID),
            },
        ])

        # Test the retrieve endpoint
        for create_url in (SUBSIDY_ACCESS_POLICY_DEPR_LIST_ENDPOINT, SUBSIDY_ACCESS_POLICY_LIST_ENDPOINT):
            enterprise_customer_uuid = str(TEST_ENTERPRISE_UUID)
            catalog_uuid = str(uuid4())
            subsidy_uuid = str(uuid4())
            payload = {
                'policy_type': policy_type,
                'description': 'test description',
                'active': True,
                'enterprise_customer_uuid': enterprise_customer_uuid,
                'catalog_uuid': catalog_uuid,
                'subsidy_uuid': subsidy_uuid,
                'access_method': AccessMethods.DIRECT,
                'spend_limit': None,
            }
            payload.update(extra_fields)
            response = self.client.post(create_url, payload)
            assert response.status_code == expected_response_code

            if expected_response_code == status.HTTP_201_CREATED:
                response_json = response.json()
                del response_json['uuid']
                expected_response = payload.copy()
                expected_response.setdefault("per_learner_enrollment_limit")
                expected_response.setdefault("per_learner_spend_limit")
                assert response_json == expected_response

            # Test idempotency
            response = self.client.post(create_url, payload)
            duplicate_status_code = status.HTTP_200_OK

            assert response.status_code == duplicate_status_code

            if response.status_code == status.HTTP_200_OK:
                response_json = response.json()
                del response_json['uuid']
                expected_response = payload.copy()
                expected_response.setdefault("per_learner_enrollment_limit")
                expected_response.setdefault("per_learner_spend_limit")
                assert response_json == expected_response


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

        # The redeem endpoint
        url = reverse('api:v1:policy-redemption-redeem', kwargs={'policy_uuid': self.redeemable_policy.uuid})
        payload = {
            'lms_user_id': 1234,
            'content_key': 'course-v1:edX+edXPrivacy101+3T2020',
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, expected_response_code)

        # The credits_available endpoint
        query_params = {
            'enterprise_customer_uuid': str(self.enterprise_uuid),
            'lms_user_id': 1234,
        }
        response = self.client.get(reverse('api:v1:policy-redemption-credits-available'), query_params)
        self.assertEqual(response.status_code, expected_response_code)

        # The can_redeem endpoint
        url = reverse(
            "api:v1:policy-redemption-can-redeem",
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
            'api:v1:policy-redemption-redeem',
            kwargs={'policy_uuid': self.redeemable_policy.uuid}
        )
        self.subsidy_access_policy_credits_available_endpoint = reverse('api:v1:policy-redemption-credits-available')
        self.subsidy_access_policy_can_redeem_endpoint = reverse(
            "api:v1:policy-redemption-can-redeem",
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
        subsidy_client.can_redeem.return_value = {
            'can_redeem': True,
            'content_price': 0,
            'unit': 'usd_cents',
            'all_transactions': [],
        }
        subsidy_client.list_subsidy_transactions.return_value = {"results": [], "aggregates": {}}
        subsidy_client.create_subsidy_transaction.side_effect = (
            NotImplementedError("unit test must override create_subsidy_transaction to use.")
        )

        path_prefix = 'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.'

        contains_key_patcher = mock.patch(path_prefix + 'catalog_contains_content_key')
        self.mock_contains_key = contains_key_patcher.start()
        self.mock_contains_key.return_value = True

        get_content_metadata_patcher = mock.patch(path_prefix + 'get_content_metadata')
        self.mock_get_content_metadata = get_content_metadata_patcher.start()
        self.mock_get_content_metadata.return_value = {}

        lms_client_patcher = mock.patch('enterprise_access.apps.subsidy_access_policy.models.LmsApiClient')
        lms_client = lms_client_patcher.start()
        lms_client_instance = lms_client.return_value
        lms_client_instance.enterprise_contains_learner.return_value = True

        self.addCleanup(lms_client_patcher.stop)
        self.addCleanup(subsidy_client_patcher.stop)
        self.addCleanup(contains_key_patcher.stop)
        self.addCleanup(get_content_metadata_patcher.stop)

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.get_and_cache_transactions_for_learner')
    def test_redeem_policy(self, mock_transactions_cache_for_learner):  # pylint: disable=unused-argument
        """
        Verify that SubsidyAccessPolicyRedeemViewset redeem endpoint works as expected
        """
        self.mock_get_content_metadata.return_value = {'content_price': 123}
        mock_transaction_record = {
            'uuid': str(uuid4()),
            'state': TransactionStateChoices.COMMITTED,
            'other': True,
        }
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.side_effect = None
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.return_value = mock_transaction_record
        payload = {
            'lms_user_id': 1234,
            'content_key': 'course-v1:edX+edXPrivacy101+3T2020',
        }

        response = self.client.post(self.subsidy_access_policy_redeem_endpoint, payload)

        response_json = self.load_json(response.content)
        assert response_json == mock_transaction_record
        self.mock_get_content_metadata.assert_called_once_with(payload['content_key'])
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.assert_called_once_with(
            subsidy_uuid=str(self.redeemable_policy.subsidy_uuid),
            lms_user_id=payload['lms_user_id'],
            content_key=payload['content_key'],
            subsidy_access_policy_uuid=str(self.redeemable_policy.uuid),
            metadata=None,
            idempotency_key=create_idempotency_key_for_transaction(
                subsidy_uuid=str(self.redeemable_policy.subsidy_uuid),
                lms_user_id=payload['lms_user_id'],
                content_key=payload['content_key'],
                subsidy_access_policy_uuid=str(self.redeemable_policy.uuid),
                historical_redemptions_uuids=[],
            ),
        )

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.get_and_cache_transactions_for_learner')
    def test_redeem_policy_with_metadata(self, mock_transactions_cache_for_learner):  # pylint: disable=unused-argument
        """
        Verify that SubsidyAccessPolicyRedeemViewset redeem endpoint works as expected
        """
        self.mock_get_content_metadata.return_value = {'content_price': 123}
        mock_transaction_record = {
            'uuid': str(uuid4()),
            'status': 'committed',
            'other': True,
        }
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.side_effect = None
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.return_value = mock_transaction_record
        payload = {
            'lms_user_id': 1234,
            'content_key': 'course-v1:edX+edXPrivacy101+3T2020',
            'metadata': {
                'geag_first_name': 'John'
            }
        }

        response = self.client.post(self.subsidy_access_policy_redeem_endpoint, payload)

        response_json = self.load_json(response.content)
        assert response_json == mock_transaction_record
        self.mock_get_content_metadata.assert_called_once_with(payload['content_key'])
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.assert_called_once_with(
            subsidy_uuid=str(self.redeemable_policy.subsidy_uuid),
            lms_user_id=payload['lms_user_id'],
            content_key=payload['content_key'],
            subsidy_access_policy_uuid=str(self.redeemable_policy.uuid),
            metadata=payload['metadata'],
            idempotency_key=create_idempotency_key_for_transaction(
                subsidy_uuid=str(self.redeemable_policy.subsidy_uuid),
                lms_user_id=payload['lms_user_id'],
                content_key=payload['content_key'],
                subsidy_access_policy_uuid=str(self.redeemable_policy.uuid),
                historical_redemptions_uuids=[],
            ),
        )

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.get_and_cache_transactions_for_learner')
    @ddt.data(
        {
            "existing_transaction_state": None,
            "existing_transaction_reversed": None,
            "idempotency_key_versioned": False,
        },
        {
            "existing_transaction_state": TransactionStateChoices.CREATED,
            "existing_transaction_reversed": False,
            "idempotency_key_versioned": False,
        },
        {
            "existing_transaction_state": TransactionStateChoices.PENDING,
            "existing_transaction_reversed": False,
            "idempotency_key_versioned": False,
        },
        {
            "existing_transaction_state": TransactionStateChoices.COMMITTED,
            "existing_transaction_reversed": False,
            "idempotency_key_versioned": False,
        },
        {
            "existing_transaction_state": TransactionStateChoices.COMMITTED,
            "existing_transaction_reversed": True,
            "idempotency_key_versioned": True,
        },
        {
            "existing_transaction_state": TransactionStateChoices.FAILED,
            "existing_transaction_reversed": False,
            "idempotency_key_versioned": True,
        },
    )
    @ddt.unpack
    def test_redeem_policy_redemption_idempotency_key_versions(
        self,
        mock_transactions_cache_for_learner,
        existing_transaction_state,
        existing_transaction_reversed,
        idempotency_key_versioned,
    ):  # pylint: disable=unused-argument
        """
        Verify that SubsidyAccessPolicyRedeemViewset redeem endpoint sends either a baseline or a versioned idempotency
        key, depending on any existing transactions.
        """
        self.mock_get_content_metadata.return_value = {'content_price': 5000}

        lms_user_id = 1234
        content_key = 'course-v1:edX+edXPrivacy101+3T2020'
        historical_redemption_uuid = str(uuid4())
        baseline_idempotency_key = create_idempotency_key_for_transaction(
            subsidy_uuid=str(self.redeemable_policy.subsidy_uuid),
            lms_user_id=lms_user_id,
            content_key=content_key,
            subsidy_access_policy_uuid=str(self.redeemable_policy.uuid),
            historical_redemptions_uuids=[],
        )
        existing_transactions = []
        if existing_transaction_state:
            existing_transaction = {
                'uuid': historical_redemption_uuid,
                'state': existing_transaction_state,
                'idempotency_key': baseline_idempotency_key,
                'reversal': None,
            }
            if existing_transaction_reversed:
                existing_transaction['reversal'] = {'state': TransactionStateChoices.COMMITTED}
            existing_transactions.append(existing_transaction)
        self.redeemable_policy.subsidy_client.can_redeem.return_value = {
            'can_redeem': True,
            'content_price': 5000,
            'unit': 'usd_cents',
            'all_transactions': existing_transactions,
        }
        mock_transaction_record = {
            'uuid': str(uuid4()),
            'state': TransactionStateChoices.COMMITTED,
            'other': True,
        }
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.side_effect = None
        self.redeemable_policy.subsidy_client.create_subsidy_transaction.return_value = mock_transaction_record

        payload = {
            'lms_user_id': lms_user_id,
            'content_key': content_key,
        }
        response = self.client.post(self.subsidy_access_policy_redeem_endpoint, payload)

        assert response.status_code == status.HTTP_200_OK

        new_idempotency_key_sent = \
            self.redeemable_policy.subsidy_client.create_subsidy_transaction.call_args.kwargs['idempotency_key']
        if idempotency_key_versioned:
            assert new_idempotency_key_sent != baseline_idempotency_key
        else:
            assert new_idempotency_key_sent == baseline_idempotency_key

    @mock.patch('enterprise_access.apps.subsidy_access_policy.models.get_and_cache_transactions_for_learner')
    def test_credits_available_endpoint(self, mock_transactions_cache_for_learner):
        """
        Verify that SubsidyAccessPolicyViewset credits_available returns credit based policies with redeemable credit.
        """
        mock_transaction_record = {
            'uuid': str(uuid4()),
            'state': TransactionStateChoices.COMMITTED,
            'content_key': 'something',
            'subsidy_access_policy_uuid': str(self.redeemable_policy.uuid),
            'quantity': 200,
            'other': True,
        }
        mock_transactions_cache_for_learner.return_value = {
            'transactions': [
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


@ddt.ddt
class TestSubsidyAccessPolicyCanRedeemView(APITestWithMocks):
    """
    Tests for the can-redeem view
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

        self.subsidy_access_policy_can_redeem_endpoint = reverse(
            "api:v1:policy-redemption-can-redeem",
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
        subsidy_client.can_redeem.return_value = {
            'can_redeem': True,
            'content_price': 5000,
            'unit': 'usd_cents',
            'all_transactions': [],
        }
        subsidy_client.list_subsidy_transactions.return_value = {"results": [], "aggregates": {}}
        subsidy_client.create_subsidy_transaction.side_effect = (
            NotImplementedError("unit test must override create_subsidy_transaction to use.")
        )

        path_prefix = 'enterprise_access.apps.subsidy_access_policy.models.SubsidyAccessPolicy.'

        contains_key_patcher = mock.patch(path_prefix + 'catalog_contains_content_key')
        self.mock_contains_key = contains_key_patcher.start()
        self.mock_contains_key.return_value = True

        get_content_metadata_patcher = mock.patch(path_prefix + 'get_content_metadata')
        self.mock_get_content_metadata = get_content_metadata_patcher.start()
        self.mock_get_content_metadata.return_value = {}

        transactions_for_learner_patcher = mock.patch(path_prefix + 'transactions_for_learner')
        self.mock_policy_transactions_for_learner = transactions_for_learner_patcher.start()
        self.mock_policy_transactions_for_learner.return_value = {
            'transactions': [],
            'aggregates': {'total_quantity': 0},
        }

        lms_client_patcher = mock.patch('enterprise_access.apps.subsidy_access_policy.models.LmsApiClient')
        lms_client = lms_client_patcher.start()
        lms_client_instance = lms_client.return_value
        lms_client_instance.enterprise_contains_learner.return_value = True

        self.addCleanup(lms_client_patcher.stop)
        self.addCleanup(subsidy_client_patcher.stop)
        self.addCleanup(contains_key_patcher.stop)
        self.addCleanup(get_content_metadata_patcher.stop)
        self.addCleanup(transactions_for_learner_patcher.stop)

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

    @mock.patch('enterprise_access.apps.subsidy_access_policy.subsidy_api.get_and_cache_transactions_for_learner')
    def test_can_redeem_policy(self, mock_transactions_cache_for_learner):
        """
        Test that the can_redeem endpoint returns an access policy when one is redeemable.
        """
        mock_transactions_cache_for_learner.return_value = {
            'transactions': [],
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

        self.mock_get_content_metadata.side_effect = mock_get_subsidy_content_data

        with mock.patch(
            'enterprise_access.apps.api.v1.views.subsidy_access_policy.get_and_cache_content_metadata',
            side_effect=mock_get_subsidy_content_data,
        ):
            query_params = {'content_key': [test_content_key_1, test_content_key_2]}
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

    @mock.patch('enterprise_access.apps.subsidy_access_policy.subsidy_api.get_and_cache_transactions_for_learner')
    @mock.patch('enterprise_access.apps.api.v1.views.subsidy_access_policy.LmsApiClient', return_value=mock.MagicMock())
    @ddt.data(
        {"has_admin_users": True},
        {"has_admin_users": False},
    )
    @ddt.unpack
    def test_can_redeem_policy_none_redeemable(
        self, mock_lms_client, mock_transactions_cache_for_learner, has_admin_users
    ):
        """
        Test that the can_redeem endpoint returns resons for why each non-redeemable policy failed.
        """
        slug = 'sluggy'
        admin_email = 'edx@example.org'
        mock_lms_client().get_enterprise_customer_data.return_value = {
            'slug': slug,
            'admin_users': [{'email': admin_email}] if has_admin_users else [],
        }

        mock_transactions_cache_for_learner.return_value = {
            'transactions': [],
            'aggregates': {
                'total_quantity': 0,
            },
        }
        self.redeemable_policy.subsidy_client.can_redeem.return_value = {
            'can_redeem': False,
            'content_price': 5000,  # value is ignored.
            'unit': 'usd_cents',
            'all_transactions': [],
        }
        test_content_key_1 = "course-v1:edX+edXPrivacy101+3T2020"
        test_content_key_2 = "course-v1:edX+edXPrivacy101+3T2020_2"
        test_content_key_1_metadata_price = 29900
        test_content_key_2_metadata_price = 81900

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

        self.mock_get_content_metadata.side_effect = mock_get_subsidy_content_data

        with mock.patch(
            'enterprise_access.apps.api.v1.views.subsidy_access_policy.get_and_cache_content_metadata',
            side_effect=mock_get_subsidy_content_data,
        ):
            query_params = {'content_key': [test_content_key_1, test_content_key_2]}
            response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)

        assert response.status_code == status.HTTP_200_OK
        response_list = response.json()

        # Make sure we got responses for all two content_keys requested.
        assert len(response_list) == 2

        # Check the response for the first content_key given.
        assert response_list[0]["content_key"] == test_content_key_1
        # We should not assume that a list price is fetchable if the
        # content cant' be redeemed - the content may not be in any catalog for any policy.
        assert response_list[0]["list_price"] is None
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
        assert response_list[1]["list_price"] is None

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

    @mock.patch('enterprise_access.apps.subsidy_access_policy.subsidy_api.get_and_cache_transactions_for_learner')
    def test_can_redeem_policy_existing_redemptions(self, mock_transactions_cache_for_learner):
        """
        Test that the can_redeem endpoint shows existing redemptions too.
        """
        test_transaction_uuid = str(uuid4())
        mock_transactions_cache_for_learner.return_value = {
            "transactions": [{
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
                "reversal": None,
            }],
            "aggregates": {
                "total_quantity": -19900,
            },
        }

        self.redeemable_policy.subsidy_client.can_redeem.return_value = {
            'can_redeem': False,
        }
        self.mock_get_content_metadata.return_value = {'content_price': 19900}

        mocked_content_data_from_view = {
            "content_uuid": str(uuid4()),
            "content_key": "course-v1:demox+1234+2T2023",
            "source": "edX",
            "content_price": 19900,
        }

        with mock.patch(
            'enterprise_access.apps.api.v1.views.subsidy_access_policy.get_and_cache_content_metadata',
            return_value=mocked_content_data_from_view,
        ):
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
        self.assertTrue(response_list[0]["has_successful_redemption"])
        self.assertIsNone(response_list[0]["redeemable_subsidy_access_policy"])
        self.assertFalse(response_list[0]["can_redeem"])
        self.assertEqual(response_list[0]["reasons"], [])
        # the subsidy.can_redeem check returns false, so we don't make
        # it to the point of fetching subsidy content data
        self.assertFalse(self.mock_get_content_metadata.called)

    @mock.patch('enterprise_access.apps.subsidy_access_policy.subsidy_api.get_and_cache_transactions_for_learner')
    def test_can_redeem_policy_existing_reversed_redemptions(self, mock_transactions_cache_for_learner):
        """
        Test that the can_redeem endpoint returns can_redeem=True even with an existing reversed transaction.
        """
        test_transaction_uuid = str(uuid4())
        mock_transactions_cache_for_learner.return_value = {
            "transactions": [{
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
                "reversal": {
                    "uuid": str(uuid4()),
                    "state": TransactionStateChoices.COMMITTED,
                    "idempotency_key": f"admin-invoked-reverse-{test_transaction_uuid}",
                    "quantity": -19900,
                },
            }],
            "aggregates": {
                "total_quantity": 0,
            },
        }

        self.redeemable_policy.subsidy_client.can_redeem.return_value = {
            'can_redeem': True,
        }
        self.mock_get_content_metadata.return_value = {'content_price': 19900}

        mocked_content_data_from_view = {
            "content_uuid": str(uuid4()),
            "content_key": "course-v1:demox+1234+2T2023",
            "source": "edX",
            "content_price": 19900,
        }

        with mock.patch(
            'enterprise_access.apps.api.v1.views.subsidy_access_policy.get_and_cache_content_metadata',
            return_value=mocked_content_data_from_view,
        ):
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
        assert response_list[0]["has_successful_redemption"] is False
        assert response_list[0]["redeemable_subsidy_access_policy"]["uuid"] == str(self.redeemable_policy.uuid)
        assert response_list[0]["can_redeem"] is True
        assert response_list[0]["reasons"] == []

    @mock.patch('enterprise_access.apps.subsidy_access_policy.subsidy_api.get_and_cache_transactions_for_learner')
    @mock.patch('enterprise_access.apps.api.v1.views.subsidy_access_policy.LmsApiClient')
    def test_can_redeem_policy_no_price(self, mock_lms_client, mock_transactions_cache_for_learner):
        """
        Test that the can_redeem endpoint successfuly serializes a response for content that has no price.
        """
        test_content_key = "course-v1:demox+1234+2T2023"
        mock_lms_client.return_value.get_enterprise_customer_data.return_value = {
            'slug': 'sluggy',
            'admin_users': [{'email': 'edx@example.org'}],
        }

        self.mock_get_content_metadata.return_value = {
            'content_price': None,
        }

        mock_transactions_cache_for_learner.return_value = {
            'transactions': [],
            'aggregates': {
                'total_quantity': 0,
            },
        }

        mocked_content_data_from_view = {
            "content_uuid": str(uuid4()),
            "content_key": test_content_key,
            "source": "edX",
            "content_price": None,
        }

        with mock.patch(
            'enterprise_access.apps.api.v1.views.subsidy_access_policy.get_and_cache_content_metadata',
            return_value=mocked_content_data_from_view,
        ):
            query_params = {'content_key': test_content_key}
            response = self.client.get(self.subsidy_access_policy_can_redeem_endpoint, query_params)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.json() == {
            'detail': f'Could not determine price for content_key: {test_content_key}',
        }
