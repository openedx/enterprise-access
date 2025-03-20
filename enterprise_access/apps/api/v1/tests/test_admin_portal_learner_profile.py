
"""
Tests for AdminPortalLearnerProfileViewset.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from enterprise_access.apps.api.v1.views.admin_portal_learner_profile import AdminLearnerProfileViewSet


class TestAdminPortalLearnerProfileView(TestCase):
    """Unit tests for AdminLearnerProfileViewSet."""

    @classmethod
    def setUpTestData(cls):
        """Set up reusable test data."""
        cls.factory = APIRequestFactory()
        cls.User = get_user_model()
        cls.admin_user = cls.User.objects.create_superuser(
            username='admin', password='password', email='admin@example.com'
        )
        cls.view = AdminLearnerProfileViewSet.as_view({'get': 'learner_profile'})
        cls.mock_subscriptions = {
            "uuid": "1b66a3c0-b001-48c9-a22e-c91d7a77b724",
            "status": "activated",
            "user_email": "edx@example.com",
            "assigned_date": "2025-03-04T22:41:57Z",
            "activation_date": "2025-03-04T22:41:59Z",
            "revoked_date": None,
            "last_remind_date": None,
            "subscription_plan_title": "subscription plan",
            "subscription_plan_expiration_date": "2025-12-25T22:40:52Z",
            "subscription_plan": {
                "title": "subscription plan",
                "uuid": "bf093806-0285-4dc5-8ba3-bf3d69da6c7c",
                "start_date": "2025-03-04T22:40:02Z",
                "expiration_date": "2025-12-25T22:40:52Z",
                "enterprise_customer_uuid": "7dbf461e-8d3d-4a4a-9b20-9c9121f04806",
                "enterprise_catalog_uuid": "6fcdee6f-eee3-428a-a020-90602f105893",
                "is_active": True,
                "is_current": True,
                "is_revocation_cap_enabled": False,
                "days_until_expiration": 282,
                "days_until_expiration_including_renewals": 282,
                "is_locked_for_renewal_processing": False,
                "should_auto_apply_licenses": True,
                "created": "2025-03-04T22:40:54.124784Z",
                "plan_type": "Standard Paid"
            }
        }

        cls.mock_get_group_memberships = {
            'lms_user_id': 3,
            'pending_enterprise_customer_user_id': None,
            'enterprise_group_membership_uuid': 'uuid-1',
            'member_details': {
                'user_email': 'test@example.com',
                'user_name': 'Test User'
            },
            'recent_action': 'Accepted: March 17, 2025',
            'status': 'accepted',
            'activated_at': '2025-03-17T22:07:48Z',
            'enrollments': 0,
            'group_name': 'Groups - 1'
        }

    def authenticate_request(self, params):
        """Helper method to create and authenticate a request."""
        request = self.factory.get('/api/v1/admin-view/learner_profile', params)
        force_authenticate(request, user=self.admin_user)
        return request

    def test_missing_enterprise_customer_uuid(self):
        """Test missing enterprise_customer_uuid returns 400 error."""
        request = self.authenticate_request({'user_email': 'test@example.com'})
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('enterprise_customer_uuid', response.data)

    def test_missing_lms_user_id(self):
        """Test when neither user_email nor lms_user_id is provided."""
        request = self.authenticate_request({
            'enterprise_customer_uuid': '123e4567-e89b-12d3-a456-426614174000',
            'user_email': 'test@example.com',
        })
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('lms_user_id', response.data)

    def test_missing_user_email(self):
        """Test when neither user_email nor lms_user_id is provided."""
        request = self.authenticate_request({
            'enterprise_customer_uuid': '123e4567-e89b-12d3-a456-426614174000',
            'lms_user_id': '456',
        })
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('user_email', response.data)

    @patch('enterprise_access.apps.admin_portal_learner_profile.api.get_learner_subscriptions')
    @patch('enterprise_access.apps.admin_portal_learner_profile.api.get_group_memberships')
    @patch('enterprise_access.apps.admin_portal_learner_profile.api.get_enrollments')
    def test_successful_response_with_email_and_lms_user_id(
        self, mock_get_enrollments, mock_get_group_memberships, mock_get_learner_subscriptions
    ):
        """Test successful response with both user_email and lms_user_id."""
        mock_get_learner_subscriptions.return_value = [self.mock_subscriptions]
        mock_get_group_memberships.return_value = [self.mock_get_group_memberships]
        mock_get_enrollments.return_value = {'in_progress': [{'course_id': 'course-v1:test+T1+2025'}]}

        request = self.authenticate_request({
            'user_email': 'test@example.com',
            'lms_user_id': '456',
            'enterprise_customer_uuid': '123e4567-e89b-12d3-a456-426614174000'
        })

        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('subscriptions', response.data)
        self.assertIn('group_memberships', response.data)
        self.assertIn('enrollments', response.data)
        self.assertEqual(len(response.data['subscriptions']), 1)
        self.assertEqual(len(response.data['group_memberships']), 1)
        self.assertEqual(len(response.data['enrollments'].get('in_progress')), 1)
