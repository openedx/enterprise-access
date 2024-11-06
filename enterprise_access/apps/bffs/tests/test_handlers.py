from django.test import RequestFactory, TestCase
from faker import Faker
from rest_framework.exceptions import ValidationError

from enterprise_access.apps.bffs.context import HandlerContext
from enterprise_access.apps.bffs.handlers import BaseHandler, BaseLearnerPortalHandler, DashboardHandler
from enterprise_access.apps.core.tests.factories import UserFactory


class TestBaseHandlerMixin(TestCase):
    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.mock_user = UserFactory()
        self.faker = Faker()

        self.mock_enterprise_customer_uuid = self.faker.uuid4()
        self.request = self.factory.get('sample/api/call')
        self.request.query_params = {
            'enterprise_customer_uuid': self.mock_enterprise_customer_uuid
        }
        self.request.user = self.mock_user
        self.context = HandlerContext(self.request)


class TestBaseHandler(TestBaseHandlerMixin):
    def setUp(self):
        super().setUp()
        self.base_handler = BaseHandler(self.context)

    def test_base_handler_uninitialized_load_and_process(self):
        base_handler = self.base_handler
        with self.assertRaises(NotImplementedError):
            base_handler.load_and_process()

    def test_base_handler_add_error(self):
        base_handler = self.base_handler
        expected_output = {
            "developer_message": "No enterprise uuid associated to the user mock-uuid",
            "user_message": "You may not be associated with the enterprise.",
        }
        # Define kwargs for add_error
        arguments = {
            **expected_output,
            "status": 403  # Add an attribute that is not explicitly defined in the serializer to verify
        }
        base_handler.add_error(
            **arguments
        )
        self.assertEqual(expected_output, base_handler.context.errors[0])

    def test_base_handler_add_warning(self):
        base_handler = self.base_handler
        expected_output = {
            "developer_message": "Heuristic Expiration",
            "user_message": "The data received might be out-dated",
        }
        # Define kwargs for add_warning
        arguments = {
            **expected_output,
            "status": 113  # Add an attribute that is not explicitly defined in the serializer to verify
        }
        base_handler.add_warning(
            **arguments
        )
        self.assertEqual(expected_output, base_handler.context.warnings[0])


class TestBaseLearnerPortalHandler(TestBaseHandlerMixin):
    def setUp(self):
        super().setUp()
        self.base_learner_portal_handler = BaseLearnerPortalHandler(self.context)

    # TODO: Test pertaining to currently stubbed out functions deferred for future tickets


class TestDashboardHandler(TestBaseHandlerMixin):
    def setUp(self):
        super().setUp()
        self.dashboard_handler = DashboardHandler(self.context)

    # TODO: Update tests once stubbed out function updated
    def test_load_and_process(self):
        expected_output = [
            {
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
        ]
        dashboard_handler = self.dashboard_handler
        dashboard_handler.load_and_process()
        self.assertEqual(
            dashboard_handler.context.data['enterprise_course_enrollments'],
            expected_output
        )
