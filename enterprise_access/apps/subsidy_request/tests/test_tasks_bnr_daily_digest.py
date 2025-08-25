"""Tests for send_learner_credit_bnr_admins_email_with_new_requests_task (task).

Validates payload, early exits, and slicing behavior for open (REQUESTED) requests.
"""
from datetime import timedelta
from unittest import mock
from uuid import uuid4

import pytest
from django.conf import settings
from django.utils import timezone

from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates
from enterprise_access.apps.subsidy_request.tasks import send_learner_credit_bnr_admins_email_with_new_requests_task
from enterprise_access.apps.subsidy_request.tests.factories import (
    LearnerCreditRequestConfigurationFactory,
    LearnerCreditRequestFactory
)


@pytest.mark.django_db
class TestLearnerCreditBNRDailyDigestTask:
    """Tests for the BNR daily digest task."""
    def _enterprise_data(self, enterprise_uuid, slug='ent-slug', name='Ent Name'):
        return {
            'uuid': enterprise_uuid,
            'slug': slug,
            'name': name,
            'admin_users': [
                {'lms_user_id': 1, 'email': 'a@example.com'},
                {'lms_user_id': 2, 'email': 'b@example.com'},
            ],
        }

    @mock.patch('enterprise_access.apps.subsidy_request.tasks.BrazeApiClient')
    @mock.patch('enterprise_access.apps.subsidy_request.tasks.LmsApiClient.get_enterprise_customer_data')
    def test_no_open_requests_early_exit(self, mock_ent_data, mock_braze):
        enterprise_uuid = uuid4()
        config = LearnerCreditRequestConfigurationFactory(active=True)
        mock_ent_data.return_value = self._enterprise_data(enterprise_uuid)

        # No REQUESTED requests
        send_learner_credit_bnr_admins_email_with_new_requests_task(
            policy_uuid=str(uuid4()),
            lc_request_config_uuid=str(config.uuid),
            enterprise_customer_uuid=str(enterprise_uuid),
        )
        mock_braze.return_value.send_campaign_message.assert_not_called()

    @mock.patch('enterprise_access.apps.subsidy_request.tasks.BrazeApiClient')
    @mock.patch('enterprise_access.apps.subsidy_request.tasks.LmsApiClient.get_enterprise_customer_data')
    def test_payload_and_recipients_for_open_requests(self, mock_ent_data, mock_braze):
        enterprise_uuid = uuid4()
        config = LearnerCreditRequestConfigurationFactory(active=True)
        mock_ent_data.return_value = self._enterprise_data(enterprise_uuid, slug='test-slug', name='Org Name')

        # Create 3 open requests with deterministic order (newest last created)
        reqs = []
        for i in range(3):
            r = LearnerCreditRequestFactory(
                enterprise_customer_uuid=enterprise_uuid,
                learner_credit_request_config=config,
                state=SubsidyRequestStates.REQUESTED,
            )
            # Stagger created to control ordering
            r.created = timezone.now() - timedelta(minutes=10 - i)
            r.save(update_fields=['created'])
            reqs.append(r)

        policy_uuid = uuid4()
        send_learner_credit_bnr_admins_email_with_new_requests_task(
            policy_uuid=str(policy_uuid),
            lc_request_config_uuid=str(config.uuid),
            enterprise_customer_uuid=str(enterprise_uuid),
        )

        mock_braze.return_value.send_campaign_message.assert_called_once()
        call_args = mock_braze.return_value.send_campaign_message.call_args
        campaign_id = call_args[0][0]
        kwargs = call_args[1]

        assert campaign_id == settings.BRAZE_LEARNER_CREDIT_BNR_NEW_REQUESTS_NOTIFICATION_CAMPAIGN
        assert kwargs['trigger_properties']['total_requests'] == 3
        # Ordered by -created: reqs with highest created last in loop
        emails = [item['user_email'] for item in kwargs['trigger_properties']['requests']]
        titles = [item['course_title'] for item in kwargs['trigger_properties']['requests']]
        expected = sorted(reqs, key=lambda r: r.created, reverse=True)
        assert emails == [r.user.email for r in expected]
        assert titles == [r.course_title for r in expected]
        assert kwargs['trigger_properties']['manage_requests_url'].endswith(
            f'/admin/learner-credit/{policy_uuid}/requests'
        )
        # Recipients are created for each admin
        assert len(kwargs['recipients']) == 2

    @mock.patch('enterprise_access.apps.subsidy_request.tasks.BrazeApiClient')
    @mock.patch('enterprise_access.apps.subsidy_request.tasks.LmsApiClient.get_enterprise_customer_data')
    def test_limit_to_latest_10(self, mock_ent_data, mock_braze):
        enterprise_uuid = uuid4()
        config = LearnerCreditRequestConfigurationFactory(active=True)
        mock_ent_data.return_value = self._enterprise_data(enterprise_uuid)

        # Create 12 open requests
        created = []
        for i in range(12):
            r = LearnerCreditRequestFactory(
                enterprise_customer_uuid=enterprise_uuid,
                learner_credit_request_config=config,
                state=SubsidyRequestStates.REQUESTED,
            )
            r.created = timezone.now() - timedelta(minutes=120 - i)
            r.save(update_fields=['created'])
            created.append(r)

        send_learner_credit_bnr_admins_email_with_new_requests_task(
            policy_uuid=str(uuid4()),
            lc_request_config_uuid=str(config.uuid),
            enterprise_customer_uuid=str(enterprise_uuid),
        )

        mock_braze.return_value.send_campaign_message.assert_called_once()
        sent = mock_braze.return_value.send_campaign_message.call_args[1]['trigger_properties']['requests']
        assert len(sent) == 10
        # Ensure we got the latest 10
        expected = sorted(created, key=lambda r: r.created, reverse=True)[:10]
        assert [i['user_email'] for i in sent] == [r.user.email for r in expected]

    @mock.patch('enterprise_access.apps.subsidy_request.tasks.BrazeApiClient')
    @mock.patch('enterprise_access.apps.subsidy_request.tasks.LmsApiClient.get_enterprise_customer_data')
    def test_no_admin_users_early_exit(self, mock_ent_data, mock_braze):
        enterprise_uuid = uuid4()
        config = LearnerCreditRequestConfigurationFactory(active=True)
        data = self._enterprise_data(enterprise_uuid)
        data['admin_users'] = []
        mock_ent_data.return_value = data

        LearnerCreditRequestFactory(
            enterprise_customer_uuid=enterprise_uuid,
            learner_credit_request_config=config,
            state=SubsidyRequestStates.REQUESTED,
        )

        send_learner_credit_bnr_admins_email_with_new_requests_task(
            policy_uuid=str(uuid4()),
            lc_request_config_uuid=str(config.uuid),
            enterprise_customer_uuid=str(enterprise_uuid),
        )
        mock_braze.return_value.send_campaign_message.assert_not_called()

    @mock.patch('enterprise_access.apps.subsidy_request.tasks.BrazeApiClient', side_effect=Exception('boom'))
    @mock.patch('enterprise_access.apps.subsidy_request.tasks.LmsApiClient.get_enterprise_customer_data')
    def test_braze_exception_propagates(self, mock_ent_data, _mock_braze):
        enterprise_uuid = uuid4()
        config = LearnerCreditRequestConfigurationFactory(active=True)
        mock_ent_data.return_value = self._enterprise_data(enterprise_uuid)

        LearnerCreditRequestFactory(
            enterprise_customer_uuid=enterprise_uuid,
            learner_credit_request_config=config,
            state=SubsidyRequestStates.REQUESTED,
        )

        with pytest.raises(Exception):
            send_learner_credit_bnr_admins_email_with_new_requests_task(
                policy_uuid=str(uuid4()),
                lc_request_config_uuid=str(config.uuid),
                enterprise_customer_uuid=str(enterprise_uuid),
            )
