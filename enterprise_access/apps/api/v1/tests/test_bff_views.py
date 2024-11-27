"""
Tests for bffs app API v1 views.
"""
from unittest import mock
from urllib.parse import urlencode

import ddt
from django.core.cache import cache as django_cache
from rest_framework import status
from rest_framework.reverse import reverse

from enterprise_access.apps.api_client.tests.test_utils import MockLicenseManagerMetadataMixin
from enterprise_access.apps.bffs.tests.utils import TestHandlerContextMixin, mock_dashboard_dependencies
from enterprise_access.apps.core.constants import (
    BFF_READ_PERMISSION,
    SYSTEM_ENTERPRISE_ADMIN_ROLE,
    SYSTEM_ENTERPRISE_LEARNER_ROLE,
    SYSTEM_ENTERPRISE_OPERATOR_ROLE
)
from test_utils import APITest


@ddt.ddt
class TestLearnerPortalBFFViewSet(TestHandlerContextMixin, MockLicenseManagerMetadataMixin, APITest):
    """
    Tests for the LearnerPortalBFFViewSet.
    """

    def setUp(self):
        """
        Set up the tests.
        """
        super().setUp()
        self.addCleanup(django_cache.clear)
        self.mock_subscription_licenses_data = {
            'customer_agreement': None,
            'results': [],
        }
        self.mock_default_enterprise_enrollment_intentions_learner_status_data = {
            "lms_user_id": self.mock_user.id,
            "user_email": self.mock_user.email,
            "enterprise_customer_uuid": self.mock_enterprise_customer_uuid,
            "enrollment_statuses": {
                "needs_enrollment": {
                    "enrollable": [],
                    "not_enrollable": [],
                },
                'already_enrolled': [],
            },
            "metadata": {
                "total_default_enterprise_enrollment_intentions": 0,
                "total_needs_enrollment": {
                    "enrollable": 0,
                    "not_enrollable": 0
                },
                "total_already_enrolled": 0
            }
        }
        self.mock_enterprise_course_enrollment = {
            "certificate_download_url": None,
            "emails_enabled": False,
            "course_run_id": "course-v1:BabsonX+MIS01x+1T2019",
            "course_run_status": "in_progress",
            "created": "2023-03-01T00:00:00Z",
            "start_date": "2023-03-19T10:00:00Z",
            "end_date": "2024-12-31T04:30:00Z",
            "display_name": "AI for Leaders",
            "course_run_url": "https://learning.edx.org/course/course-v1:BabsonX+MIS01x+1T2019/home",
            "due_dates": [],
            "pacing": "self",
            "org_name": "BabsonX",
            "is_revoked": False,
            "is_enrollment_active": True,
            "mode": "verified",
            "resume_course_run_url": None,
            "course_key": "BabsonX+MIS01x",
            "course_type": "verified-audit",
            "product_source": "edx",
            "enroll_by": "2024-12-21T23:59:59Z",
        }
        self.mock_enterprise_course_enrollments = []

        self.expected_enterprise_customer = {
            **self.mock_enterprise_customer,
            'disable_search': False,
            'show_integration_warning': True,
        }

        self.expected_customer_agreement = {
            'uuid': self.mock_customer_agreement_uuid,
            'available_subscription_catalogs': self.mock_customer_agreement.get('available_subscription_catalogs'),
            'default_enterprise_catalog_uuid': self.mock_customer_agreement.get('default_enterprise_catalog_uuid'),
            'net_days_until_expiration': self.mock_customer_agreement.get('net_days_until_expiration'),
            'disable_expiration_notifications': self.mock_customer_agreement.get('disable_expiration_notifications'),
            'enable_auto_applied_subscriptions_with_universal_link': self.mock_customer_agreement.get(
                'enable_auto_applied_subscriptions_with_universal_link'
            ),
            'subscription_for_auto_applied_licenses': self.mock_customer_agreement.get(
                'subscription_for_auto_applied_licenses'
            ),
            'has_custom_license_expiration_messaging_v2': self.mock_customer_agreement.get(
                'has_custom_license_expiration_messaging_v2'
            ),
            'button_label_in_modal_v2': self.mock_customer_agreement.get('button_label_in_modal_v2'),
            'expired_subscription_modal_messaging_v2': self.mock_customer_agreement.get(
                'expired_subscription_modal_messaging_v2'
            ),
            'modal_header_text_v2': self.mock_customer_agreement.get('modal_header_text_v2'),
        }
        self.expected_subscription_license = {
            'uuid': self.mock_subscription_license.get('uuid'),
            'status': self.mock_subscription_license.get('status'),
            'user_email': self.mock_subscription_license.get('user_email'),
            'activation_date': self.mock_subscription_license.get('activation_date'),
            'last_remind_date': self.mock_subscription_license.get('last_remind_date'),
            'revoked_date': self.mock_subscription_license.get('revoked_date'),
            'activation_key': self.mock_subscription_license.get('activation_key'),
            'subscription_plan': {
                'uuid': self.mock_subscription_plan_uuid,
                'title': self.mock_subscription_plan.get('title'),
                'enterprise_catalog_uuid': self.mock_subscription_plan.get('enterprise_catalog_uuid'),
                'is_active': self.mock_subscription_plan.get('is_active'),
                'is_current': self.mock_subscription_plan.get('is_current'),
                'start_date': self.mock_subscription_plan.get('start_date'),
                'expiration_date': self.mock_subscription_plan.get('expiration_date'),
                'days_until_expiration': self.mock_subscription_plan.get('days_until_expiration'),
                'days_until_expiration_including_renewals': self.mock_subscription_plan.get(
                    'days_until_expiration_including_renewals'
                ),
                'should_auto_apply_licenses': self.mock_subscription_plan.get('should_auto_apply_licenses'),
            },
        }

        # Mock base response data
        self.mock_common_response_data = {
            'enterprise_customer': self.expected_enterprise_customer,
            'enterprise_customer_user_subsidies': {
                'subscriptions': {
                    'customer_agreement': None,
                    'subscription_licenses': [],
                    'subscription_licenses_by_status': {
                        'activated': [],
                        'assigned': [],
                        'revoked': [],
                    },
                },
            },
            'errors': [],
            'warnings': [],
            'enterprise_features': {'feature_flag': True},
        }
        self.mock_dashboard_route_response_data = {
            **self.mock_common_response_data,
            'enterprise_course_enrollments': [],
        }

    @ddt.data(
        {
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'is_linked_user': True,
        },
        {
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'is_linked_user': True,
        },
        {
            'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE,
            'is_linked_user': True,
        },
        {
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'is_linked_user': False,
        },
        {
            'system_wide_role': SYSTEM_ENTERPRISE_ADMIN_ROLE,
            'is_linked_user': False,
        },
        {
            'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE,
            'is_linked_user': False,
        },
    )
    @ddt.unpack
    @mock_dashboard_dependencies
    def test_dashboard_empty_state_with_permissions(
        self,
        mock_get_enterprise_customers_for_user,
        mock_get_subscription_licenses_for_learner,
        mock_get_default_enrollment_intentions_learner_status,
        mock_get_enterprise_course_enrollments,
        system_wide_role,
        is_linked_user,
    ):
        """
        Test the dashboard route.
        """
        enterprise_customer_uuid_for_context = (
            self.mock_enterprise_customer_uuid
            if is_linked_user
            else self.mock_enterprise_customer_uuid_2
        )
        role_context = (
            '*'
            if system_wide_role == SYSTEM_ENTERPRISE_OPERATOR_ROLE
            else enterprise_customer_uuid_for_context
        )
        self.set_jwt_cookie([{
            'system_wide_role': system_wide_role,
            'context': role_context,
        }])
        mock_get_enterprise_customers_for_user.return_value = self.mock_enterprise_learner_response_data
        mock_get_subscription_licenses_for_learner.return_value = self.mock_subscription_licenses_data
        mock_get_default_enrollment_intentions_learner_status.return_value =\
            self.mock_default_enterprise_enrollment_intentions_learner_status_data
        mock_get_enterprise_course_enrollments.return_value = self.mock_enterprise_course_enrollments

        query_params = {
            'enterprise_customer_slug': self.mock_enterprise_customer_slug,
        }
        dashboard_url = reverse('api:v1:learner-portal-bff-dashboard')
        dashboard_url += f"?{urlencode(query_params)}"

        response = self.client.post(dashboard_url)
        expected_status_code = status.HTTP_200_OK
        if is_linked_user or system_wide_role == SYSTEM_ENTERPRISE_OPERATOR_ROLE:
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            expected_response_data = self.mock_dashboard_route_response_data
        else:
            expected_status_code = status.HTTP_403_FORBIDDEN
            expected_response_data = {
                'detail': f'Missing: {BFF_READ_PERMISSION}',
            }

        self.assertEqual(response.status_code, expected_status_code)
        self.assertEqual(response.json(), expected_response_data)

    @mock_dashboard_dependencies
    def test_dashboard_with_subscriptions(
        self,
        mock_get_enterprise_customers_for_user,
        mock_get_default_enrollment_intentions_learner_status,
        mock_get_subscription_licenses_for_learner,
        mock_get_enterprise_course_enrollments,
    ):
        """
        Test the dashboard route with subscriptions.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': self.mock_enterprise_customer_uuid,
        }])
        mock_get_enterprise_customers_for_user.return_value = self.mock_enterprise_learner_response_data
        mock_subscription_licenses_data = {
            'customer_agreement': self.mock_customer_agreement,
            'results': [self.mock_subscription_license],
        }
        mock_get_subscription_licenses_for_learner.return_value = mock_subscription_licenses_data
        mock_get_default_enrollment_intentions_learner_status.return_value =\
            self.mock_default_enterprise_enrollment_intentions_learner_status_data
        mock_get_enterprise_course_enrollments.return_value = self.mock_enterprise_course_enrollments

        query_params = {
            'enterprise_customer_slug': self.mock_enterprise_customer_slug,
        }
        dashboard_url = reverse('api:v1:learner-portal-bff-dashboard')
        dashboard_url += f"?{urlencode(query_params)}"

        response = self.client.post(dashboard_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected_response_data = self.mock_dashboard_route_response_data.copy()
        expected_response_data.update({
            'enterprise_customer_user_subsidies': {
                'subscriptions': {
                    'customer_agreement': self.expected_customer_agreement,
                    'subscription_licenses': [self.expected_subscription_license],
                    'subscription_licenses_by_status': {
                        'activated': [self.expected_subscription_license],
                        'assigned': [],
                        'revoked': [],
                    },
                },
            },
        })
        self.assertEqual(response.json(), expected_response_data)

    @mock_dashboard_dependencies
    @mock.patch('enterprise_access.apps.api_client.license_manager_client.LicenseManagerUserApiClient.activate_license')
    def test_dashboard_with_subscriptions_license_activation(
        self,
        mock_get_enterprise_customers_for_user,
        mock_get_default_enrollment_intentions_learner_status,
        mock_get_subscription_licenses_for_learner,
        mock_get_enterprise_course_enrollments,
        mock_activate_license,
    ):
        """
        Test the dashboard route with subscriptions, handling
        activation of assigned licenses.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': self.mock_enterprise_customer_uuid,
        }])
        mock_get_enterprise_customers_for_user.return_value = self.mock_enterprise_learner_response_data
        mock_assigned_subscription_license = {
            **self.mock_subscription_license,
            'status': 'assigned',
            'activation_date': None,
        }
        mock_activated_subscription_license = {
            **self.mock_subscription_license,
            'status': 'activated',
            'activation_date': '2024-01-01T00:00:00Z',
        }
        mock_subscription_licenses_data = {
            'customer_agreement': self.mock_customer_agreement,
            'results': [mock_assigned_subscription_license],
        }
        mock_get_subscription_licenses_for_learner.return_value = mock_subscription_licenses_data
        mock_activate_license.return_value = mock_activated_subscription_license
        mock_get_default_enrollment_intentions_learner_status.return_value =\
            self.mock_default_enterprise_enrollment_intentions_learner_status_data
        mock_get_enterprise_course_enrollments.return_value = self.mock_enterprise_course_enrollments

        query_params = {
            'enterprise_customer_slug': self.mock_enterprise_customer_slug,
        }
        dashboard_url = reverse('api:v1:learner-portal-bff-dashboard')
        dashboard_url += f"?{urlencode(query_params)}"

        response = self.client.post(dashboard_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected_activated_subscription_license = {
            **self.expected_subscription_license,
            'status': 'activated',
            'activation_date': '2024-01-01T00:00:00Z',
        }
        expected_response_data = self.mock_dashboard_route_response_data.copy()
        expected_response_data.update({
            'enterprise_customer_user_subsidies': {
                'subscriptions': {
                    'customer_agreement': self.expected_customer_agreement,
                    'subscription_licenses': [expected_activated_subscription_license],
                    'subscription_licenses_by_status': {
                        'activated': [expected_activated_subscription_license],
                        'assigned': [],
                        'revoked': [],
                    },
                },
            },
        })
        self.assertEqual(response.json(), expected_response_data)

    @ddt.data(
        # No identity provider, no universal link auto-apply, no plan for auto-apply.
        # Expected: Should not auto-apply.
        {
            'identity_provider': False,
            'auto_apply_with_universal_link': False,
            'has_plan_for_auto_apply': False,
            'should_auto_apply': False,
        },
        # Identity provider exists, but no universal link auto-apply, no plan for auto-apply.
        # Expected: Should not auto-apply.
        {
            'identity_provider': True,
            'auto_apply_with_universal_link': False,
            'has_plan_for_auto_apply': False,
            'should_auto_apply': False,
        },
        # No identity provider, universal link auto-apply is enabled, no plan for auto-apply.
        # Expected: Should not auto-apply.
        {
            'identity_provider': False,
            'auto_apply_with_universal_link': True,
            'has_plan_for_auto_apply': False,
            'should_auto_apply': False,
        },
        # Identity provider exists, universal link auto-apply is enabled, no plan for auto-apply.
        # Expected: Should not auto-apply.
        {
            'identity_provider': True,
            'auto_apply_with_universal_link': True,
            'has_plan_for_auto_apply': False,
            'should_auto_apply': False,
        },
        # No identity provider, no universal link auto-apply, but a plan for auto-apply exists.
        # Expected: Should not auto-apply.
        {
            'identity_provider': False,
            'auto_apply_with_universal_link': False,
            'has_plan_for_auto_apply': True,
            'should_auto_apply': False,
        },
        # Identity provider exists, no universal link auto-apply, but a plan for auto-apply exists.
        # Expected: Should auto-apply.
        {
            'identity_provider': True,
            'auto_apply_with_universal_link': False,
            'has_plan_for_auto_apply': True,
            'should_auto_apply': True,
        },
        # No identity provider, universal link auto-apply is enabled, and a plan for auto-apply exists.
        # Expected: Should auto-apply.
        {
            'identity_provider': False,
            'auto_apply_with_universal_link': True,
            'has_plan_for_auto_apply': True,
            'should_auto_apply': True,
        },
        # Identity provider exists, universal link auto-apply is enabled, and a plan for auto-apply exists.
        # Expected: Should auto-apply.
        {
            'identity_provider': True,
            'auto_apply_with_universal_link': True,
            'has_plan_for_auto_apply': True,
            'should_auto_apply': True,
        },
    )
    @ddt.unpack
    @mock_dashboard_dependencies
    @mock.patch(
        'enterprise_access.apps.api_client.license_manager_client.LicenseManagerUserApiClient.auto_apply_license'
    )
    def test_dashboard_with_subscriptions_license_auto_apply(
        self,
        mock_get_enterprise_customers_for_user,
        mock_get_default_enrollment_intentions_learner_status,
        mock_get_subscription_licenses_for_learner,
        mock_get_enterprise_course_enrollments,
        mock_auto_apply_license,
        identity_provider,
        auto_apply_with_universal_link,
        has_plan_for_auto_apply,
        should_auto_apply,
    ):
        """
        Test the dashboard route with subscriptions, auto-applying a subscription
        license based on the customer agreement and enterprise customer settings.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': self.mock_enterprise_customer_uuid,
        }])
        mock_identity_provider = 'mock_idp' if identity_provider else None
        mock_identity_providers = (
            [
                {
                    'provider_id': 'mock_idp',
                    'default_provider': True,
                },
            ]
            if identity_provider
            else []
        )
        mock_enterprise_customer_with_auto_apply = {
            **self.mock_enterprise_customer,
            'identity_provider': mock_identity_provider,
            'identity_providers': mock_identity_providers,
        }
        mock_enterprise_learner_response_data = {
            **self.mock_enterprise_learner_response_data,
            'results': [
                {
                    'active': True,
                    'enterprise_customer': mock_enterprise_customer_with_auto_apply,
                },
            ],
        }
        mock_get_enterprise_customers_for_user.return_value = mock_enterprise_learner_response_data
        mock_auto_applied_subscription_license = {
            **self.mock_subscription_license,
            'status': 'activated',
            'activation_date': '2024-01-01T00:00:00Z',
        }
        mock_subscription_licenses_data = {
            **self.mock_subscription_licenses_data,
            'customer_agreement': {
                **self.mock_customer_agreement,
                'enable_auto_applied_subscriptions_with_universal_link': auto_apply_with_universal_link,
                'subscription_for_auto_applied_licenses': (
                    self.mock_subscription_plan_uuid
                    if has_plan_for_auto_apply else None
                ),
            },
        }
        mock_get_subscription_licenses_for_learner.return_value = mock_subscription_licenses_data
        mock_auto_apply_license.return_value = mock_auto_applied_subscription_license
        mock_get_default_enrollment_intentions_learner_status.return_value =\
            self.mock_default_enterprise_enrollment_intentions_learner_status_data
        mock_get_enterprise_course_enrollments.return_value = self.mock_enterprise_course_enrollments

        query_params = {
            'enterprise_customer_slug': self.mock_enterprise_customer_slug,
        }
        dashboard_url = reverse('api:v1:learner-portal-bff-dashboard')
        dashboard_url += f"?{urlencode(query_params)}"

        response = self.client.post(dashboard_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected_customer_agreement = {
            **self.expected_customer_agreement,
            'enable_auto_applied_subscriptions_with_universal_link': auto_apply_with_universal_link,
            'subscription_for_auto_applied_licenses': (
                self.mock_subscription_plan_uuid
                if has_plan_for_auto_apply else None
            ),
        }
        expected_activated_subscription_license = {
            **self.expected_subscription_license,
            'status': 'activated',
            'activation_date': '2024-01-01T00:00:00Z',
        }
        expected_licenses = [expected_activated_subscription_license] if should_auto_apply else []
        expected_response_data = self.mock_dashboard_route_response_data.copy()
        expected_show_integration_warning = bool(identity_provider)
        expected_response_data.update({
            'enterprise_customer': {
                **self.expected_enterprise_customer,
                'identity_provider': mock_identity_provider,
                'identity_providers': mock_identity_providers,
                'show_integration_warning': expected_show_integration_warning,
            },
            'enterprise_customer_user_subsidies': {
                'subscriptions': {
                    'customer_agreement': expected_customer_agreement,
                    'subscription_licenses': expected_licenses,
                    'subscription_licenses_by_status': {
                        'activated': expected_licenses,
                        'assigned': [],
                        'revoked': [],
                    },
                },
            },
        })
        self.assertEqual(response.json(), expected_response_data)

    @mock_dashboard_dependencies
    def test_dashboard_with_enrollments(
        self,
        mock_get_enterprise_customers_for_user,
        mock_get_subscription_licenses_for_learner,
        mock_get_default_enrollment_intentions_learner_status,
        mock_get_enterprise_course_enrollments,
    ):
        """
        Test the dashboard route with enrollments.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': self.mock_enterprise_customer_uuid,
        }])
        mock_get_enterprise_customers_for_user.return_value = self.mock_enterprise_learner_response_data
        mock_get_subscription_licenses_for_learner.return_value = self.mock_subscription_licenses_data
        mock_get_default_enrollment_intentions_learner_status.return_value =\
            self.mock_default_enterprise_enrollment_intentions_learner_status_data
        mock_get_enterprise_course_enrollments.return_value = [self.mock_enterprise_course_enrollment]

        query_params = {
            'enterprise_customer_slug': self.mock_enterprise_customer_slug,
        }
        dashboard_url = reverse('api:v1:learner-portal-bff-dashboard')
        dashboard_url += f"?{urlencode(query_params)}"

        response = self.client.post(dashboard_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected_response_data = self.mock_dashboard_route_response_data.copy()
        expected_response_data.update({
            'enterprise_course_enrollments': [
                self.mock_enterprise_course_enrollment,
            ],
        })
        self.assertEqual(response.json(), expected_response_data)
