"""
Tests for BFF handlers
"""
<<<<<<< HEAD
from unittest import mock

from rest_framework import status
=======
from django.test import RequestFactory, TestCase
from faker import Faker
>>>>>>> e155f13 (feat: add response builder to the BFF)

from enterprise_access.apps.bffs.context import HandlerContext
from enterprise_access.apps.bffs.handlers import BaseHandler, BaseLearnerPortalHandler, DashboardHandler
from enterprise_access.apps.bffs.tests.utils import TestHandlerContextMixin


class TestBaseHandler(TestHandlerContextMixin):
    """
    Test BaseHandler
    """

    def test_base_handler_load_and_process_not_implemented(self):
        context = HandlerContext(self.request)
        base_handler = BaseHandler(context)
        with self.assertRaises(NotImplementedError):
            base_handler.load_and_process()

    @mock.patch('enterprise_access.apps.api_client.lms_client.LmsUserApiClient.get_enterprise_customers_for_user')
    def test_base_handler_add_error(self, mock_get_enterprise_customers_for_user):
        mock_get_enterprise_customers_for_user.return_value = {'results': []}
        context = HandlerContext(self.request)
        base_handler = BaseHandler(context)
        # Define kwargs for add_error
        arguments = {
            **self.mock_error,
            "status_code": status.HTTP_400_BAD_REQUEST
        }
        base_handler.add_error(
            **arguments
        )
        self.assertEqual(self.mock_error, base_handler.context.errors[0])

    def test_base_handler_add_warning(self):
        context = HandlerContext(self.request)
        base_handler = BaseHandler(context)
        # Define kwargs for add_warning
        arguments = {
            **self.mock_warning,
            "status_code": 113  # Add an attribute that is not explicitly defined in the serializer to verify
        }
        base_handler.add_warning(
            **arguments
        )
        self.assertEqual(self.mock_warning, base_handler.context.warnings[0])


class TestBaseLearnerPortalHandler(TestHandlerContextMixin):
    """
    Test BaseLearnerPortalHandler
    """

    def setUp(self):
        super().setUp()
        self.expected_enterprise_customer = {
            **self.mock_enterprise_customer,
            'disable_search': False,
            'show_integration_warning': False,
        }
        self.expected_enterprise_customer_2 = {
            **self.mock_enterprise_customer_2,
            'disable_search': False,
            'show_integration_warning': False,
        }
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

    def get_expected_enterprise_customer(self, enterprise_customer_user):
        enterprise_customer = enterprise_customer_user.get('enterprise_customer')
        return (
            self.expected_enterprise_customer
            if enterprise_customer.get('uuid') == self.mock_enterprise_customer_uuid
            else self.expected_enterprise_customer_2
        )

    @mock.patch('enterprise_access.apps.api_client.lms_client.LmsUserApiClient.get_enterprise_customers_for_user')
    @mock.patch(
        'enterprise_access.apps.api_client.lms_client.LmsUserApiClient'
        '.get_default_enterprise_enrollment_intentions_learner_status'
    )
    @mock.patch(
        'enterprise_access.apps.api_client.license_manager_client.LicenseManagerUserApiClient'
        '.get_subscription_licenses_for_learner'
    )
    def test_load_and_process(
        self,
        mock_get_subscription_licenses_for_learner,
        mock_get_default_enrollment_intentions_learner_status,
        mock_get_enterprise_customers_for_user,
    ):
        """
        Test load_and_process method
        """
        mock_get_enterprise_customers_for_user.return_value = self.mock_enterprise_learner_response_data
        mock_get_subscription_licenses_for_learner.return_value = self.mock_subscription_licenses_data
        mock_get_default_enrollment_intentions_learner_status.return_value =\
            self.mock_default_enterprise_enrollment_intentions_learner_status_data

        context = HandlerContext(self.request)
        handler = BaseLearnerPortalHandler(context)

        handler.load_and_process()

        # Enterprise Customer related assertions
        actual_enterprise_customer = handler.context.data.get('enterprise_customer')
        actual_active_enterprise_customer = handler.context.data.get('active_enterprise_customer')
        actual_linked_ecus = handler.context.data.get('all_linked_enterprise_customer_users')
        expected_linked_ecus = [
            {
                **enterprise_customer_user,
                'enterprise_customer': self.get_expected_enterprise_customer(enterprise_customer_user),
            }
            for enterprise_customer_user in self.mock_enterprise_learner_response_data['results']
        ]
        actual_staff_enterprise_customer = handler.context.data.get('staff_enterprise_customer')
        expected_staff_enterprise_customer = None
        self.assertEqual(actual_enterprise_customer, self.expected_enterprise_customer)
        self.assertEqual(actual_active_enterprise_customer, self.expected_enterprise_customer)
        self.assertEqual(actual_linked_ecus, expected_linked_ecus)
        self.assertEqual(actual_staff_enterprise_customer, expected_staff_enterprise_customer)

        # Base subscriptions related assertions
        actual_subscriptions = handler.context.data['enterprise_customer_user_subsidies']['subscriptions']
        expected_subscriptions = {
            'customer_agreement': None,
            'subscription_licenses': [],
            'subscription_licenses_by_status': {},
        }
        self.assertEqual(actual_subscriptions, expected_subscriptions)

        # Default enterprise enrollment intentions related assertions
        actual_default_enterprise_enrollment_intentions = (
            handler.context.data.get('default_enterprise_enrollment_intentions')
        )
        expected_default_enterprise_enrollment_intentions = (
            self.mock_default_enterprise_enrollment_intentions_learner_status_data
        )
        self.assertEqual(
            actual_default_enterprise_enrollment_intentions,
            expected_default_enterprise_enrollment_intentions
        )

    @mock.patch('enterprise_access.apps.api_client.lms_client.LmsUserApiClient.get_enterprise_customers_for_user')
    def test_load_and_process_without_learner_portal_enabled(self, mock_get_enterprise_customers_for_user):
        """
        Test load_and_process method without learner portal enabled. No enterprise
        customer metadata should be returned.
        """
        mock_get_enterprise_customers_for_user.return_value = {
            **self.mock_enterprise_learner_response_data,
            'results': [{
                **self.mock_enterprise_customer,
                'enable_learner_portal': False,
            }],
        }
        context = HandlerContext(self.request)
        handler = BaseLearnerPortalHandler(context)

        handler.load_and_process()

        actual_enterprise_customer = handler.context.data.get('enterprise_customer')
        expected_enterprise_customer = None
        self.assertEqual(actual_enterprise_customer, expected_enterprise_customer)

    @mock.patch('enterprise_access.apps.api_client.lms_client.LmsUserApiClient.get_enterprise_customers_for_user')
    @mock.patch('enterprise_access.apps.api_client.lms_client.LmsApiClient.get_enterprise_customer_data')
    def test_load_and_process_staff_enterprise_customer(
        self,
        mock_get_enterprise_customer_data,
        mock_get_enterprise_customers_for_user,
    ):
        mock_get_enterprise_customers_for_user.return_value = {
            **self.mock_enterprise_learner_response_data,
            'results': [],
        }
        mock_get_enterprise_customer_data.return_value = self.mock_enterprise_customer
        request = self.request
        request.user = self.mock_staff_user
        context = HandlerContext(request)
        handler = BaseLearnerPortalHandler(context)

        handler.load_and_process()

        actual_enterprise_customer = handler.context.data.get('enterprise_customer')
        expected_enterprise_customer = self.expected_enterprise_customer
        self.assertEqual(actual_enterprise_customer, expected_enterprise_customer)
        actual_staff_enterprise_customer = handler.context.data.get('staff_enterprise_customer')
        expected_staff_enterprise_customer = self.expected_enterprise_customer
        self.assertEqual(actual_staff_enterprise_customer, expected_staff_enterprise_customer)


class TestDashboardHandler(TestHandlerContextMixin):
    """
    Test DashboardHandler
    """

    def setUp(self):
        super().setUp()

        self.mock_enterprise_course_enrollment = {
            "certificate_download_url": None,
            "emails_enabled": False,
            "course_run_id": "course-v1:BabsonX+MIS01x+1T2019",
            "course_run_status": "in_progress",
            "created": "2023-09-29T14:24:45.409031+00:00",
            "start_date": "2019-03-19T10:00:00Z",
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
        self.mock_enterprise_course_enrollments = [self.mock_enterprise_course_enrollment]

    @mock.patch('enterprise_access.apps.api_client.lms_client.LmsUserApiClient.get_enterprise_course_enrollments')
    def test_load_and_process(self, mock_get_enterprise_course_enrollments):
        mock_get_enterprise_course_enrollments.return_value = self.mock_enterprise_course_enrollments

        context = HandlerContext(self.request)
        dashboard_handler = DashboardHandler(context)

        dashboard_handler.load_and_process()

        self.assertEqual(
            dashboard_handler.context.data.get('enterprise_course_enrollments'),
            self.mock_enterprise_course_enrollments,
        )
