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
from enterprise_access.apps.bffs.constants import COURSE_ENROLLMENT_STATUSES
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
            "micromasters_title": None,
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
            'url_for_button_in_modal_v2': self.mock_customer_agreement.get('url_for_button_in_modal_v2'),
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
            'all_linked_enterprise_customer_users': [
                {
                    'id': 1,
                    'active': True,
                    'enterprise_customer': self.expected_enterprise_customer,
                    'user_id': 3,
                },
                {
                    'id': 2,
                    'active': False,
                    'enterprise_customer': {
                        **self.mock_enterprise_customer_2,
                        'disable_search': False,
                        'show_integration_warning': True,
                    },
                    'user_id': 6,
                },
            ],
            'should_update_active_enterprise_customer_user': self.mock_should_update_active_enterprise_customer_user,
            'enterprise_customer_user_subsidies': {
                'subscriptions': {
                    'customer_agreement': None,
                    'subscription_licenses': [],
                    'subscription_licenses_by_status': {
                        'activated': [],
                        'assigned': [],
                        'revoked': [],
                    },
                    'subscription_license': None,
                    'subscription_plan': None,
                    'show_expiration_notifications': False,
                },
            },
            'errors': [],
            'warnings': [],
            'enterprise_features': {'feature_flag': True},
        }
        self.mock_dashboard_route_response_data = {
            **self.mock_common_response_data,
            'enterprise_course_enrollments': [],
            'all_enrollments_by_status': {
                COURSE_ENROLLMENT_STATUSES.IN_PROGRESS: [],
                COURSE_ENROLLMENT_STATUSES.UPCOMING: [],
                COURSE_ENROLLMENT_STATUSES.COMPLETED: [],
                COURSE_ENROLLMENT_STATUSES.SAVED_FOR_LATER: [],
            },
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
                    'subscription_license': self.expected_subscription_license,
                    'subscription_plan': self.expected_subscription_license['subscription_plan'],
                    'show_expiration_notifications': True,
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
                    'subscription_license': expected_activated_subscription_license,
                    'subscription_plan': expected_activated_subscription_license['subscription_plan'],
                    'show_expiration_notifications': True,
                },
            },
        })
        self.assertEqual(response.json(), expected_response_data)

    @ddt.data(
        # No existing licenses, identity provider, no universal link auto-apply, no plan for auto-apply,
        # and not a staff request user.
        # Expected: Should not auto-apply.
        {
            'has_existing_activated_license': False,
            'has_existing_revoked_license': False,
            'is_staff_request_user': False,
            'identity_provider': False,
            'auto_apply_with_universal_link': False,
            'has_plan_for_auto_apply': False,
            'should_auto_apply': False,
        },
        # No existing licenses, identity provider exists, but no universal link auto-apply, no plan for auto-apply,
        # and not a staff request user.
        # Expected: Should not auto-apply.
        {
            'has_existing_activated_license': False,
            'has_existing_revoked_license': False,
            'is_staff_request_user': False,
            'identity_provider': True,
            'auto_apply_with_universal_link': False,
            'has_plan_for_auto_apply': False,
            'should_auto_apply': False,
        },
        # No existing licenses, no identity provider, universal link auto-apply is enabled, no plan for auto-apply,
        # and not a staff request user.
        # Expected: Should not auto-apply.
        {
            'has_existing_activated_license': False,
            'has_existing_revoked_license': False,
            'is_staff_request_user': False,
            'identity_provider': False,
            'auto_apply_with_universal_link': True,
            'has_plan_for_auto_apply': False,
            'should_auto_apply': False,
        },
        # No existing licenses, identity provider exists, universal link auto-apply is enabled, no plan for auto-apply,
        # and not a staff request user.
        # Expected: Should not auto-apply.
        {
            'has_existing_activated_license': False,
            'has_existing_revoked_license': False,
            'is_staff_request_user': False,
            'identity_provider': True,
            'auto_apply_with_universal_link': True,
            'has_plan_for_auto_apply': False,
            'should_auto_apply': False,
        },
        # No existing licenses, no identity provider, no universal link auto-apply, a plan for auto-apply exists,
        # and not a staff request user.
        # Expected: Should not auto-apply.
        {
            'has_existing_activated_license': False,
            'has_existing_revoked_license': False,
            'is_staff_request_user': False,
            'identity_provider': False,
            'auto_apply_with_universal_link': False,
            'has_plan_for_auto_apply': True,
            'should_auto_apply': False,
        },
        # No existing licenses, identity provider exists, no universal link auto-apply,
        # a plan for auto-apply exists, and not a staff request user.
        # Expected: Should auto-apply.
        {
            'has_existing_activated_license': False,
            'has_existing_revoked_license': False,
            'is_staff_request_user': False,
            'identity_provider': True,
            'auto_apply_with_universal_link': False,
            'has_plan_for_auto_apply': True,
            'should_auto_apply': True,
        },
        # No existing licenses, no identity provider, universal link auto-apply is enabled,
        # a plan for auto-apply exists, and not a staff request user.
        # Expected: Should auto-apply.
        {
            'has_existing_activated_license': False,
            'has_existing_revoked_license': False,
            'is_staff_request_user': False,
            'identity_provider': False,
            'auto_apply_with_universal_link': True,
            'has_plan_for_auto_apply': True,
            'should_auto_apply': True,
        },
        # No existing licenses, identity provider exists, universal link auto-apply is enabled,
        # a plan for auto-apply exists, and not a staff request user.
        # Expected: Should auto-apply.
        {
            'has_existing_activated_license': False,
            'has_existing_revoked_license': False,
            'is_staff_request_user': False,
            'identity_provider': True,
            'auto_apply_with_universal_link': True,
            'has_plan_for_auto_apply': True,
            'should_auto_apply': True,
        },
        # Existing activated license, identity provider exists, universal link auto-apply is enabled,
        # a plan for auto-apply exists, and not a staff request user.
        # Expected: Should not auto-apply.
        {
            'has_existing_activated_license': True,
            'has_existing_revoked_license': False,
            'is_staff_request_user': False,
            'identity_provider': True,
            'auto_apply_with_universal_link': True,
            'has_plan_for_auto_apply': True,
            'should_auto_apply': False,
        },
        # Existing revoked license, identity provider exists, universal link auto-apply is enabled,
        # a plan for auto-apply exists, and not a staff request user.
        # Expected: Should not auto-apply.
        {
            'has_existing_activated_license': False,
            'has_existing_revoked_license': True,
            'is_staff_request_user': False,
            'identity_provider': True,
            'auto_apply_with_universal_link': True,
            'has_plan_for_auto_apply': True,
            'should_auto_apply': False,
        },
        # No existing licenses, identity provider exists, universal link auto-apply is enabled,
        # a plan for auto-apply exists, and is a staff request user.
        # Expected: Should not auto-apply.
        {
            'has_existing_activated_license': False,
            'has_existing_revoked_license': False,
            'is_staff_request_user': True,
            'identity_provider': True,
            'auto_apply_with_universal_link': True,
            'has_plan_for_auto_apply': True,
            'should_auto_apply': False,
        },
    )
    @ddt.unpack
    @mock_dashboard_dependencies
    @mock.patch('enterprise_access.apps.api_client.lms_client.LmsApiClient.get_enterprise_customer_data')
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
        mock_get_enterprise_customer_data,
        has_existing_activated_license,
        has_existing_revoked_license,
        is_staff_request_user,
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

        if is_staff_request_user:
            # Set the request user as a staff user and mock the enterprise customer response data.
            self.user.is_staff = True
            self.user.save()
            mock_get_enterprise_customer_data.return_value = self.mock_enterprise_customer

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
            **self.expected_enterprise_customer,
            'identity_provider': mock_identity_provider,
            'identity_providers': mock_identity_providers,
            'show_integration_warning': bool(identity_provider)
        }
        mock_linked_enterprise_customer_users = (
            []
            if is_staff_request_user
            else [{
                'id': 1,
                'active': True,
                'enterprise_customer': mock_enterprise_customer_with_auto_apply,
                'user_id': 3,
            }]
        )
        mock_enterprise_learner_response_data = {
            **self.mock_enterprise_learner_response_data,
            'results': mock_linked_enterprise_customer_users,
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
        if has_existing_activated_license:
            mock_subscription_licenses_data['results'].append({
                **self.mock_subscription_license,
                'status': 'activated',
                'activation_date': '2024-01-01T00:00:00Z',
            })
        if has_existing_revoked_license:
            mock_subscription_licenses_data['results'].append({
                **self.mock_subscription_license,
                'status': 'revoked',
            })
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
        expected_activated_licenses = (
            [expected_activated_subscription_license]
            if should_auto_apply or has_existing_activated_license
            else []
        )
        expected_revoked_subscription_license = {
            **self.expected_subscription_license,
            'status': 'revoked',
        }
        expected_revoked_licenses = (
            [expected_revoked_subscription_license]
            if has_existing_revoked_license
            else []
        )
        expected_subscription_license = None
        expected_subscription_plan = None
        if should_auto_apply or has_existing_activated_license:
            expected_subscription_license = expected_activated_subscription_license
        elif has_existing_revoked_license:
            expected_subscription_license = expected_revoked_subscription_license
        if expected_subscription_license:
            expected_subscription_plan = expected_subscription_license['subscription_plan']

        expected_licenses = []
        expected_licenses.extend(expected_activated_licenses)
        expected_licenses.extend(expected_revoked_licenses)
        expected_response_data = self.mock_dashboard_route_response_data.copy()
        expected_show_integration_warning = bool(identity_provider)
        expected_response_data.update({
            'enterprise_customer': {
                **self.expected_enterprise_customer,
                'identity_provider': mock_identity_provider,
                'identity_providers': mock_identity_providers,
                'show_integration_warning': expected_show_integration_warning,
            },
            'all_linked_enterprise_customer_users': mock_linked_enterprise_customer_users,
            'should_update_active_enterprise_customer_user': False,
            'enterprise_customer_user_subsidies': {
                'subscriptions': {
                    'customer_agreement': expected_customer_agreement,
                    'subscription_licenses': expected_licenses,
                    'subscription_licenses_by_status': {
                        'activated': expected_activated_licenses,
                        'assigned': [],
                        'revoked': expected_revoked_licenses,
                    },
                    'subscription_license': expected_subscription_license,
                    'subscription_plan': expected_subscription_plan,
                    'show_expiration_notifications': True,
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
        expected_transformed_enrollment = {
            'can_unenroll': True,
            'has_emails_enabled': False,
            'link_to_course': (
                self.mock_enterprise_course_enrollment['course_run_url'] or
                self.mock_enterprise_course_enrollment['resume_course_run_url']
            ),
            'link_to_certificate': self.mock_enterprise_course_enrollment['certificate_download_url'],
            'micromasters_title': self.mock_enterprise_course_enrollment['micromasters_title'],
            'course_run_id': self.mock_enterprise_course_enrollment['course_run_id'],
            'course_run_status': self.mock_enterprise_course_enrollment['course_run_status'],
            'created': self.mock_enterprise_course_enrollment['created'],
            'start_date': self.mock_enterprise_course_enrollment['start_date'],
            'end_date': self.mock_enterprise_course_enrollment['end_date'],
            'title': self.mock_enterprise_course_enrollment['display_name'],
            'notifications': self.mock_enterprise_course_enrollment['due_dates'],
            'pacing': self.mock_enterprise_course_enrollment['pacing'],
            'org_name': self.mock_enterprise_course_enrollment['org_name'],
            'is_revoked': self.mock_enterprise_course_enrollment['is_revoked'],
            'is_enrollment_active': self.mock_enterprise_course_enrollment['is_enrollment_active'],
            'mode': self.mock_enterprise_course_enrollment['mode'],
            'resume_course_run_url': self.mock_enterprise_course_enrollment['resume_course_run_url'],
            'course_key': self.mock_enterprise_course_enrollment['course_key'],
            'course_type': self.mock_enterprise_course_enrollment['course_type'],
            'product_source': self.mock_enterprise_course_enrollment['product_source'],
            'enroll_by': self.mock_enterprise_course_enrollment['enroll_by'],
        }
        expected_response_data = self.mock_dashboard_route_response_data.copy()
        expected_response_data.update({
            'enterprise_course_enrollments': [expected_transformed_enrollment],
            'all_enrollments_by_status': {
                COURSE_ENROLLMENT_STATUSES.IN_PROGRESS: [expected_transformed_enrollment],
                COURSE_ENROLLMENT_STATUSES.UPCOMING: [],
                COURSE_ENROLLMENT_STATUSES.COMPLETED: [],
                COURSE_ENROLLMENT_STATUSES.SAVED_FOR_LATER: [],
            },
        })
        self.assertEqual(response.json(), expected_response_data)

    @mock_dashboard_dependencies
    @mock.patch('enterprise_access.apps.api_client.lms_client.LmsApiClient.bulk_enroll_enterprise_learners')
    def test_dashboard_with_default_enrollment_realizations(
        self,
        mock_get_enterprise_customers_for_user,
        mock_get_default_enrollment_intentions_learner_status,
        mock_get_subscription_licenses_for_learner,
        mock_get_enterprise_course_enrollments,
        mock_bulk_enroll,
    ):
        """
        Test the dashboard route with enrollments.
        """
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': self.mock_enterprise_customer_uuid,
        }])
        mock_get_enterprise_customers_for_user.return_value = self.mock_enterprise_learner_response_data

        mock_activated_subscription_license = {
            **self.mock_subscription_license,
            'status': 'activated',
            'activation_date': '2024-01-01T00:00:00Z',
        }
        mock_subscription_licenses_data = {
            'customer_agreement': self.mock_customer_agreement,
            'results': [mock_activated_subscription_license],
        }
        mock_get_subscription_licenses_for_learner.return_value = mock_subscription_licenses_data

        mock_get_default_enrollment_intentions_learner_status.return_value = {
            "lms_user_id": self.mock_user.id,
            "user_email": self.mock_user.email,
            "enterprise_customer_uuid": self.mock_enterprise_customer_uuid,
            "enrollment_statuses": {
                "needs_enrollment": {
                    "enrollable": [
                        {
                            'applicable_enterprise_catalog_uuids': [self.mock_enterprise_catalog_uuid],
                            'course_run_key': 'course-run-1',
                        },
                    ],
                    "not_enrollable": [],
                },
                'already_enrolled': [],
            },
        }
        mock_bulk_enroll.return_value = {
            'successes': [
                {'course_run_key': 'course-run-1'},
            ],
            'failures': [],
        }
        mock_get_enterprise_course_enrollments.return_value = [self.mock_enterprise_course_enrollment]

        query_params = {
            'enterprise_customer_slug': self.mock_enterprise_customer_slug,
        }
        dashboard_url = reverse('api:v1:learner-portal-bff-dashboard')
        dashboard_url += f"?{urlencode(query_params)}"

        response = self.client.post(dashboard_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        actual_customer_uuid_arg, actual_payload_arg = mock_bulk_enroll.call_args_list[0][0]
        self.assertEqual(actual_customer_uuid_arg, self.mock_enterprise_customer_uuid)
        expected_payload = [
            {'user_id': self.user.lms_user_id, 'course_run_key': 'course-run-1',
             'license_uuid': mock_activated_subscription_license['uuid'], 'is_default_auto_enrollment': True},
        ]
        self.assertEqual(expected_payload, actual_payload_arg)
