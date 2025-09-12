"""Test subsidy_requests.admin"""

from unittest import mock

from django.contrib.admin.sites import AdminSite
from django.http import HttpRequest

from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.subsidy_request.admin import (
    LearnerCreditRequestAdmin,
    LicenseRequestAdmin,
    SubsidyRequestCustomerConfigurationAdmin
)
from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates
from enterprise_access.apps.subsidy_request.models import (
    LearnerCreditRequest,
    LicenseRequest,
    SubsidyRequestCustomerConfiguration
)
from enterprise_access.apps.subsidy_request.tests.factories import (
    LearnerCreditRequestFactory,
    LicenseRequestFactory,
    SubsidyRequestCustomerConfigurationFactory
)
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

    @mock.patch('enterprise_access.apps.subsidy_request.admin.get_user_from_request_session')
    def test_bulk_decline_requests_license_requests(self, mock_get_user):
        """
        Test bulk decline action works correctly for LicenseRequest objects.
        """
        reviewer = UserFactory()
        mock_get_user.return_value = reviewer

        # Create some license requests in different states
        requested_requests = [
            LicenseRequestFactory(state=SubsidyRequestStates.REQUESTED, reviewer=None, decline_reason=None)
            for _ in range(3)
        ]
        already_declined_request = LicenseRequestFactory(state=SubsidyRequestStates.DECLINED)
        approved_request = LicenseRequestFactory(state=SubsidyRequestStates.APPROVED)

        all_requests = requested_requests + [already_declined_request, approved_request]

        # Create admin instance and mock request
        request = HttpRequest()
        license_admin = LicenseRequestAdmin(LicenseRequest, AdminSite())

        # Create queryset with all requests
        queryset = LicenseRequest.objects.filter(
            pk__in=[req.pk for req in all_requests]
        )

        # Execute the bulk decline action
        with mock.patch.object(license_admin, 'message_user') as mock_message:
            license_admin.bulk_decline_requests(request, queryset)

        # Verify only the requested requests were declined
        for req in requested_requests:
            req.refresh_from_db()
            assert req.state == SubsidyRequestStates.DECLINED
            assert req.reviewer == reviewer
            assert req.decline_reason == "Declined via admin bulk action"
            assert req.reviewed_at is not None

        # Verify already processed requests were not changed
        already_declined_request.refresh_from_db()
        approved_request.refresh_from_db()
        assert already_declined_request.state == SubsidyRequestStates.DECLINED
        assert approved_request.state == SubsidyRequestStates.APPROVED

        # Verify correct message was shown
        mock_message.assert_called_once_with(
            request,
            'Successfully declined 3 subsidy request(s).'
        )

    @mock.patch('enterprise_access.apps.subsidy_request.admin.get_user_from_request_session')
    def test_bulk_decline_requests_learner_credit_requests(self, mock_get_user):
        """
        Test bulk decline action works correctly for LearnerCreditRequest objects.
        """
        reviewer = UserFactory()
        mock_get_user.return_value = reviewer

        # Create some learner credit requests
        requested_requests = [
            LearnerCreditRequestFactory(state=SubsidyRequestStates.REQUESTED, reviewer=None, decline_reason=None)
            for _ in range(2)
        ]

        # Create admin instance and mock request
        request = HttpRequest()
        learner_credit_admin = LearnerCreditRequestAdmin(LearnerCreditRequest, AdminSite())

        # Create queryset
        queryset = LearnerCreditRequest.objects.filter(
            pk__in=[req.pk for req in requested_requests]
        )

        # Execute the bulk decline action
        with mock.patch.object(learner_credit_admin, 'message_user') as mock_message:
            learner_credit_admin.bulk_decline_requests(request, queryset)

        # Verify requests were declined
        for req in requested_requests:
            req.refresh_from_db()
            assert req.state == SubsidyRequestStates.DECLINED
            assert req.reviewer == reviewer
            assert req.decline_reason == "Declined via admin bulk action"
            assert req.reviewed_at is not None

        # Verify correct message was shown
        mock_message.assert_called_once_with(
            request,
            'Successfully declined 2 subsidy request(s).'
        )

    @mock.patch('enterprise_access.apps.subsidy_request.admin.get_user_from_request_session')
    def test_bulk_decline_requests_empty_queryset(self, mock_get_user):
        """
        Test bulk decline action with empty queryset.
        """
        reviewer = UserFactory()
        mock_get_user.return_value = reviewer

        # Create admin instance and mock request
        request = HttpRequest()
        license_admin = LicenseRequestAdmin(LicenseRequest, AdminSite())

        # Create empty queryset
        queryset = LicenseRequest.objects.none()

        # Execute the bulk decline action
        with mock.patch.object(license_admin, 'message_user') as mock_message:
            license_admin.bulk_decline_requests(request, queryset)

        # Verify correct message was shown
        mock_message.assert_called_once_with(
            request,
            'Successfully declined 0 subsidy request(s).'
        )

    @mock.patch('enterprise_access.apps.subsidy_request.admin.get_user_from_request_session')
    def test_bulk_decline_requests_no_requested_state(self, mock_get_user):
        """
        Test bulk decline action when no requests are in requested state.
        """
        reviewer = UserFactory()
        mock_get_user.return_value = reviewer

        # Create requests in non-requested states
        declined_request = LicenseRequestFactory(state=SubsidyRequestStates.DECLINED)
        approved_request = LicenseRequestFactory(state=SubsidyRequestStates.APPROVED)

        # Create admin instance and mock request
        request = HttpRequest()
        license_admin = LicenseRequestAdmin(LicenseRequest, AdminSite())

        # Create queryset with non-requested requests
        queryset = LicenseRequest.objects.filter(
            pk__in=[declined_request.pk, approved_request.pk]
        )

        # Execute the bulk decline action
        with mock.patch.object(license_admin, 'message_user') as mock_message:
            license_admin.bulk_decline_requests(request, queryset)

        # Verify states weren't changed
        declined_request.refresh_from_db()
        approved_request.refresh_from_db()
        assert declined_request.state == SubsidyRequestStates.DECLINED
        assert approved_request.state == SubsidyRequestStates.APPROVED

        # Verify correct message was shown
        mock_message.assert_called_once_with(
            request,
            'Successfully declined 0 subsidy request(s).'
        )

    @mock.patch('enterprise_access.apps.subsidy_request.admin.get_user_from_request_session')
    @mock.patch('enterprise_access.apps.subsidy_request.models.SubsidyRequest.bulk_update')
    def test_bulk_decline_requests_calls_bulk_update(self, mock_bulk_update, mock_get_user):
        """
        Test that bulk decline action uses bulk_update for performance.
        """
        reviewer = UserFactory()
        mock_get_user.return_value = reviewer

        # Create requested license requests
        requested_requests = [
            LicenseRequestFactory(state=SubsidyRequestStates.REQUESTED, reviewer=None, decline_reason=None)
            for _ in range(3)
        ]

        # Create admin instance and mock request
        request = HttpRequest()
        license_admin = LicenseRequestAdmin(LicenseRequest, AdminSite())

        # Create queryset
        queryset = LicenseRequest.objects.filter(
            pk__in=[req.pk for req in requested_requests]
        )

        # Execute the bulk decline action
        with mock.patch.object(license_admin, 'message_user'):
            license_admin.bulk_decline_requests(request, queryset)

        # Verify bulk_update was called with correct parameters
        mock_bulk_update.assert_called_once()
        call_args = mock_bulk_update.call_args
        updated_requests, field_names = call_args[0]

        assert len(updated_requests) == 3
        assert field_names == ['state', 'reviewer', 'decline_reason', 'reviewed_at']

        # Verify the requests have correct values before bulk_update
        for req in updated_requests:
            assert req.state == SubsidyRequestStates.DECLINED
            assert req.reviewer == reviewer
            assert req.decline_reason == "Declined via admin bulk action"
            assert req.reviewed_at is not None
