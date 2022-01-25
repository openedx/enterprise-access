"""Test subsidy_requests.admin"""

import mock

from django.contrib.admin.sites import AdminSite
from django.http import HttpRequest
from django.test import TestCase

from enterprise_access.apps.subsidy_requests.admin import (
    SubsidyRequestCustomerConfigurationAdmin,
)
from enterprise_access.apps.subsidy_requests.models import (
    SubsidyRequestCustomerConfiguration,
)
from enterprise_access.apps.subsidy_requests.tests.factories import (
    SubsidyRequestCustomerConfigurationFactory,
    UserFactory,
)

class AdminTests(TestCase):

    @mock.patch('enterprise_access.apps.subsidy_requests.admin.get_data_from_jwt_payload')
    @mock.patch('enterprise_access.apps.subsidy_requests.admin.get_user_from_request_session')
    def test_subsidy_request_config_admin(self, mock_get_user, mock_get_jwt_data):
        """
        Verify that creating a config object in admin sets changed_by
        to the user in the django admin.
        """
        test_user = UserFactory()
        mock_get_user.return_value = test_user
        mock_get_jwt_data.return_value = {
            'user_id': '1337',
        }

        request = HttpRequest()
        obj = SubsidyRequestCustomerConfigurationFactory()
        form = None  # We don't care about what the form is in this case
        change = False

        assert obj.changed_by is None

        config_admin = SubsidyRequestCustomerConfigurationAdmin(
            SubsidyRequestCustomerConfiguration,
            AdminSite(),
        )
        config_admin.save_model(request, obj, form, change)

        assert obj.changed_by == test_user
