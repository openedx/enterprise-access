"""
Integration tests for filter backend order and interaction.
"""
from datetime import timedelta
from rest_framework import status
from rest_framework.reverse import reverse

from enterprise_access.apps.core.constants import SYSTEM_ENTERPRISE_ADMIN_ROLE
from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates
from enterprise_access.apps.subsidy_request.tests.factories import (
    LearnerCreditRequestFactory,
    LearnerCreditRequestActionsFactory,
    LearnerCreditRequestConfigurationFactory,
)
from enterprise_access.apps.api.v1.tests.test_browse_and_request_views import BaseEnterpriseAccessTestCase


class TestFilterBackendOrder(BaseEnterpriseAccessTestCase):
    """Test that filter backends execute in correct order and don't conflict."""

    def setUp(self):
        super().setUp()
        self.config = LearnerCreditRequestConfigurationFactory()
        self.endpoint = reverse('api:v1:learner-credit-requests-list')

    def test_filter_backend_execution_order(self):
        """
        Test that filter backends execute in the correct order:
        1. SubsidyRequestFilterBackend (security + state)
        2. DjangoFilterBackend (nested + other fields)
        3. LearnerCreditRequestOrderingFilter (ordering)
        4. SearchFilter (search)
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        # Create test data that exercises all filter backends
        request = LearnerCreditRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            user=self.user,
            state=SubsidyRequestStates.REQUESTED,
            course_title="Python Programming Course",
            learner_credit_request_config=self.config
        )
        LearnerCreditRequestActionsFactory(
            learner_credit_request=request,
            status='approved'
        )

        # Test with parameters that hit all filter backends
        query_params = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'state': SubsidyRequestStates.REQUESTED,  # SubsidyRequestFilterBackend
            'action_status': 'approved',              # DjangoFilterBackend
            'ordering': '-latest_action__created',    # LearnerCreditRequestOrderingFilter
            'search': 'Python'                       # SearchFilter
        }

        response = self.client.get(self.endpoint, query_params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response_json = self.load_json(response.content)
        results = response_json['results']

        # Verify all filters worked
        self.assertGreaterEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result['uuid'], str(request.uuid))
        self.assertEqual(result['state'], SubsidyRequestStates.REQUESTED)
        self.assertIn('Python', result['course_title'])

    def test_no_state_filter_conflicts(self):
        """
        Test that removing state filter from LearnerCreditRequestFilter
        eliminates conflicts with SubsidyRequestFilterBackend.
        """
        # This test verifies that state filtering is handled only by
        # SubsidyRequestFilterBackend and not duplicated in LearnerCreditRequestFilter

        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        # Create requests in different states
        requested_req = LearnerCreditRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            state=SubsidyRequestStates.REQUESTED,
            learner_credit_request_config=self.config
        )
        declined_req = LearnerCreditRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            state=SubsidyRequestStates.DECLINED,
            learner_credit_request_config=self.config
        )

        # Test comma-separated state filtering (should work via SubsidyRequestFilterBackend only)
        query_params = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'state': f'{SubsidyRequestStates.REQUESTED},{SubsidyRequestStates.DECLINED}'
        }

        response = self.client.get(self.endpoint, query_params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response_json = self.load_json(response.content)
        returned_uuids = {result['uuid'] for result in response_json['results']}

        # Both requests should be returned (proves comma-separated filtering works)
        self.assertIn(str(requested_req.uuid), returned_uuids)
        self.assertIn(str(declined_req.uuid), returned_uuids)

    def test_ordering_with_filtering_integration(self):
        """
        Test that ordering (LearnerCreditRequestOrderingFilter) works with filtering.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'context': str(self.enterprise_customer_uuid_1)
        }])

        # Create requests with different action statuses and creation times
        older_request = LearnerCreditRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            user=self.user,
            state=SubsidyRequestStates.REQUESTED,
            learner_credit_request_config=self.config
        )
        older_action = LearnerCreditRequestActionsFactory(
            learner_credit_request=older_request,
            status='pending'
        )

        newer_request = LearnerCreditRequestFactory(
            enterprise_customer_uuid=self.enterprise_customer_uuid_1,
            user=self.user,
            state=SubsidyRequestStates.REQUESTED,
            learner_credit_request_config=self.config
        )
        newer_action = LearnerCreditRequestActionsFactory(
            learner_credit_request=newer_request,
            status='approved'
        )

        # Ensure newer action has later timestamp
        newer_action.created = older_action.created + timedelta(minutes=1)
        newer_action.save()

        # Test filtering + ordering
        query_params = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid_1,
            'state': SubsidyRequestStates.REQUESTED,
            'ordering': '-latest_action__created'
        }
        response = self.client.get(self.endpoint, query_params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response_json = self.load_json(response.content)
        results = response_json['results']

        # Should be ordered by latest action creation time (newest first)
        self.assertGreaterEqual(len(results), 2)
        self.assertEqual(results[0]['uuid'], str(newer_request.uuid))
        self.assertEqual(results[1]['uuid'], str(older_request.uuid))
