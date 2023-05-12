"""Test subsidy_requests.admin"""

from unittest import mock

from django.contrib.admin.sites import AdminSite
from django.http import HttpRequest

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.subsidy_request.admin import SubsidyRequestCustomerConfigurationAdmin
from enterprise_access.apps.subsidy_request.models import SubsidyRequestCustomerConfiguration
from enterprise_access.apps.subsidy_request.tests.factories import SubsidyRequestCustomerConfigurationFactory
from test_utils import TestCaseWithMockedDiscoveryApiClient


class AdminTests(TestCaseWithMockedDiscoveryApiClient):
    """ Tests for admin. """

    @mock.patch('enterprise_access.apps.subsidy_request.admin.get_data_from_jwt_payload')
    @mock.patch('enterprise_access.apps.subsidy_request.admin.get_user_from_request_session')
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

    @mock.patch('enterprise_access.apps.subsidy_request.admin.get_data_from_jwt_payload')
    @mock.patch('enterprise_access.apps.subsidy_request.admin.get_user_from_request_session')
    def test_subsidy_request_config_admin_user_history(self, mock_get_user, mock_get_jwt_data):
        """
        Verify history of users is recorded after mulitple changes are made
        """
        test_user1 = UserFactory(username='user1', lms_user_id=1)
        test_user2 = UserFactory(username='user2', lms_user_id=2)
        mock_get_user.side_effect = [
            test_user1,
            test_user2,
        ]
        mock_get_jwt_data.side_effect = [
            {'user_id': '1337'},
            {'user_id': '2'},
        ]

        request = HttpRequest()
        obj = SubsidyRequestCustomerConfigurationFactory()
        form = None  # We don't care about what the form is in this case
        change = False

        for _ in range(2):
            config_admin = SubsidyRequestCustomerConfigurationAdmin(
                SubsidyRequestCustomerConfiguration,
                AdminSite(),
            )
            config_admin.save_model(request, obj, form, change)

        history = obj.history.all()
        assert history[0].changed_by.username == test_user2.username
        assert history[1].changed_by.username == test_user1.username
        assert history[2].changed_by is None
