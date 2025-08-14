"""
Unit tests for the provisioning module.
"""
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import AdminSite
from django.contrib.messages import ERROR, SUCCESS
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.provisioning import admin, models


class AdminTriggerProvisioningWorkflowAdminTests(TestCase):
    """
    Unit tests for provisioning via a django admin form.
    """
    def setUp(self):
        self.request_factory = RequestFactory()
        self.admin_user = UserFactory(is_staff=True, is_superuser=True)
        self.site = AdminSite()
        self.model_admin = admin.AdminTriggerProvisioningSubscriptionTrialWorkflowAdmin(
            models.TriggerProvisionSubscriptionTrialCustomerWorkflow,
            self.site,
        )

    def _mock_session_messages(self, request):
        """
        Helper to setup some stub message storage on the test client session.
        """
        # pylint: disable=literal-used-as-attribute
        setattr(request, 'session', self.client.session)
        messages = FallbackStorage(request)
        setattr(request, '_messages', messages)
        return messages

    def _get_messages_from_request(self, request):
        """
        Helper to extract (level, message) tuples from the _messages storage attached to a Django request.
        Returns a list of (level, message) tuples.
        """
        # pylint: disable=protected-access
        if not hasattr(request, '_messages') or request._messages is None:
            return []
        return [(msg.level, msg.message) for msg in request._messages]

    @patch("enterprise_access.apps.provisioning.models.ProvisionNewCustomerWorkflow.generate_input_dict")
    @patch("enterprise_access.apps.provisioning.models.ProvisionNewCustomerWorkflow.objects.create")
    def test_add_view_success(self, mock_create, mock_generate_input_dict):
        """
        Test that add_view creates a workflow and redirects to the change page on success.
        """
        # Set up mocks
        mock_workflow_instance = MagicMock(
            succeeded_at=timezone.now(),
            failed_at=None,
            uuid="abc-123",
            pk=42,
        )
        mock_create.return_value = mock_workflow_instance
        mock_generate_input_dict.return_value = {"mock": "input"}

        post_data = {
            "customer_name": "Acme Inc.",
            "customer_slug": "acme-inc",
            "customer_country": "US",
            "admin_email_1": "admin1@example.com",
            "admin_email_2": "",
            "admin_email_3": "",
            "admin_email_4": "",
            "admin_email_5": "",
            "catalog_title": "Acme Catalog",
            "catalog_query_id": 2,
            "agreement_default_catalog_uuid": "",
            "plan_title": "Acme Plan",
            "plan_salesforce_opportunity_line_item": "OPP12345",
            "plan_start_date": "2025-01-01 00:00:00",
            "plan_expiration_date": "2026-01-01 00:00:00",
            "plan_product_id": 1,
            "plan_desired_num_licenses": 50,
            "plan_enterprise_catalog_uuid": "",
        }
        request = self.request_factory.post(
            '/admin/provisioning/admintriggerprovisionnewcustomerworkflow/add/',
            data=post_data,
        )
        request.user = self.admin_user

        self._mock_session_messages(request)

        response = self.model_admin.add_view(request)

        # Should redirect to the workflow instance's change page
        assert response.status_code == 302
        assert reverse(
            "admin:provisioning_provisionnewcustomerworkflow_change",
            args=[mock_workflow_instance.pk]
        ) in response.url
        mock_workflow_instance.execute.assert_called_once_with()

        messages = self._get_messages_from_request(request)
        assert (SUCCESS, "Successfully triggered and completed workflow: abc-123") == messages[0]

    @patch("enterprise_access.apps.provisioning.models.ProvisionNewCustomerWorkflow.generate_input_dict")
    @patch("enterprise_access.apps.provisioning.models.ProvisionNewCustomerWorkflow.objects.create")
    def test_add_view_workflow_failure(self, mock_create, mock_generate_input_dict):
        """
        Test that add_view shows error when workflow fails.
        """
        mock_workflow_instance = MagicMock(
            failed_at=timezone.now(),
            succeeded_at=None,
            uuid="abc-666",
            pk=45,
            exception_message="Some failure",
        )
        mock_create.return_value = mock_workflow_instance
        mock_generate_input_dict.return_value = {"mock": "input"}

        post_data = {
            "customer_name": "Acme Inc.",
            "customer_slug": "acme-inc",
            "customer_country": "US",
            "admin_email_1": "admin1@example.com",
            "catalog_title": "Acme Catalog",
            "catalog_query_id": 2,
            "plan_title": "Acme Plan",
            "plan_salesforce_opportunity_line_item": "OPP12345",
            "plan_start_date": "2025-01-01 00:00:00",
            "plan_expiration_date": "2026-01-01 00:00:00",
            "plan_product_id": 1,
            "plan_desired_num_licenses": 50,
        }
        request = self.request_factory.post(
            '/admin/provisioning/admintriggerprovisionnewcustomerworkflow/add/',
            data=post_data,
        )
        request.user = self.admin_user

        self._mock_session_messages(request)

        response = self.model_admin.add_view(request)

        # Should redirect even on failure
        assert response.status_code == 302
        mock_workflow_instance.execute.assert_called_once_with()

        messages = self._get_messages_from_request(request)
        assert (ERROR, 'Workflow triggered but failed: abc-666. Error: Some failure') == messages[0]

    def test_add_view_invalid_form(self):
        """
        Test that add_view returns to form on invalid input (no customer_name).
        """
        post_data = {
            "customer_name": "",  # required, left blank
            "customer_slug": "acme-inc",
            "customer_country": "US",
            "admin_email_1": "admin1@example.com",
            "catalog_title": "Acme Catalog",
            "catalog_query_id": 2,
            "plan_title": "Acme Plan",
            "plan_salesforce_opportunity_line_item": "OPP12345",
            "plan_start_date": "2025-01-01 00:00:00",
            "plan_expiration_date": "2026-01-01 00:00:00",
            "plan_product_id": 1,
            "plan_desired_num_licenses": 50,
        }
        request = self.request_factory.post(
            '/admin/provisioning/admintriggerprovisionnewcustomerworkflow/add/',
            data=post_data,
        )
        request.user = self.admin_user
        self._mock_session_messages(request)

        # Should redirect to the same page (form with errors)
        response = self.model_admin.add_view(request)
        assert response.status_code == 302
        assert response.url.endswith('/admin/provisioning/admintriggerprovisionnewcustomerworkflow/add/')

    def test_add_view_get(self):
        """
        Test that add_view renders the form on GET.
        """
        request = self.request_factory.get('/admin/provisioning/admintriggerprovisionnewcustomerworkflow/add/')
        request.user = self.admin_user

        self._mock_session_messages(request)

        response = self.model_admin.add_view(request)

        assert response.status_code == 200
        assert b'Trigger Subscription Trial Provisioning Workflow' in response.content
