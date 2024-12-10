"""
Unittests for forms.
"""
import uuid
from unittest import mock

import requests
from django.test import TestCase

from enterprise_access.apps.subsidy_access_policy.admin.forms import SubsidyAccessPolicyForm
from enterprise_access.apps.subsidy_access_policy.constants import AccessMethods


class TestSubsidyAccessPolicyForm(TestCase):
    """
    Tests for SubsidyAccessPolicyForm.
    """
    def setUp(self):
        super().setUp()
        self.enterprise_customer_uuid = uuid.uuid4()
        self.subsidy_uuid = uuid.uuid4()
        self.catalog_uuid = uuid.uuid4()
        self.form_data = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid,
            'subsidy_uuid': self.subsidy_uuid,
            'catalog_uuid': self.catalog_uuid,
            'access_method': AccessMethods.DIRECT,
        }

    @mock.patch('enterprise_access.apps.subsidy_access_policy.admin.forms.get_versioned_subsidy_client')
    def test_clean_subsidy_uuid_success(self, mock_get_client):
        """
        Test successful validation when subsidy exists and belongs to enterprise customer.
        """
        mock_client = mock.MagicMock()
        mock_client.retrieve_subsidy.return_value = {
            'enterprise_customer_uuid': self.enterprise_customer_uuid
        }
        mock_get_client.return_value = mock_client

        form = SubsidyAccessPolicyForm(data=self.form_data)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['subsidy_uuid'], self.subsidy_uuid)

    @mock.patch('enterprise_access.apps.subsidy_access_policy.admin.forms.get_versioned_subsidy_client')
    def test_clean_subsidy_uuid_not_found(self, mock_get_client):
        """
        Verify that a validation error is raised when the subsidy does not exist.
        """
        mock_client = mock.MagicMock()
        mock_client.retrieve_subsidy.side_effect = requests.exceptions.HTTPError()
        mock_get_client.return_value = mock_client

        form = SubsidyAccessPolicyForm(data=self.form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('subsidy_uuid', form.errors)
        self.assertEqual(form.errors['subsidy_uuid'], ['Subsidy does not exist'])

    @mock.patch('enterprise_access.apps.subsidy_access_policy.admin.forms.get_versioned_subsidy_client')
    def test_clean_subsidy_uuid_wrong_enterprise(self, mock_get_client):
        """
        Verify that a validation error is raised when the subsidy belongs to a different enterprise customer.
        """
        different_enterprise_uuid = uuid.uuid4()
        mock_client = mock.MagicMock()
        mock_client.retrieve_subsidy.return_value = {
            'enterprise_customer_uuid': different_enterprise_uuid
        }
        mock_get_client.return_value = mock_client

        form = SubsidyAccessPolicyForm(data=self.form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('subsidy_uuid', form.errors)
        self.assertEqual(
            form.errors['subsidy_uuid'],
            ['Subsidy is not assigned to the same enterprise customer as the budget']
        )
