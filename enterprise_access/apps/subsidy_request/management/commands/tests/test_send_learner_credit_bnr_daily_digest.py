"""Tests for send_learner_credit_bnr_daily_digest management command.

Covers command behavior for enqueuing tasks when there are open (REQUESTED) requests.
"""

from datetime import timedelta
from unittest import mock
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.utils import timezone

from enterprise_access.apps.subsidy_access_policy.tests.factories import (
    PerLearnerSpendCapLearnerCreditAccessPolicyFactory
)
from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates
from enterprise_access.apps.subsidy_request.tests.factories import (
    LearnerCreditRequestConfigurationFactory,
    LearnerCreditRequestFactory
)


@pytest.mark.django_db
class TestSendLearnerCreditBNRDailyDigestCommand:
    """Tests enqueuing behavior for the BNR daily digest management command."""
    command_name = "send_learner_credit_bnr_daily_digest"

    def _make_policy_with_config(
        self,
        *,
        active=True,
        retired=False,
        config_active=True,
        enterprise_uuid=None
    ):
        """Helper to create a policy and its learner credit request config for test setups."""
        enterprise_uuid = enterprise_uuid or uuid4()
        config = LearnerCreditRequestConfigurationFactory(
            active=config_active
        )
        policy = PerLearnerSpendCapLearnerCreditAccessPolicyFactory(
            active=active,
            retired=retired,
            learner_credit_request_config=config,
            enterprise_customer_uuid=enterprise_uuid,
        )
        return policy, config

    @mock.patch(
        "enterprise_access.apps.subsidy_request.management.commands."
        "send_learner_credit_bnr_daily_digest."
        "send_learner_credit_bnr_admins_email_with_new_requests_task.delay"
    )
    def test_no_eligible_policies(self, mock_delay):
        # Inactive policy
        self._make_policy_with_config(active=False)
        # Retired policy
        self._make_policy_with_config(retired=True)
        # No config
        PerLearnerSpendCapLearnerCreditAccessPolicyFactory()
        # Inactive config
        self._make_policy_with_config(config_active=False)

        call_command(self.command_name)
        mock_delay.assert_not_called()

    @mock.patch(
        "enterprise_access.apps.subsidy_request.management.commands."
        "send_learner_credit_bnr_daily_digest."
        "send_learner_credit_bnr_admins_email_with_new_requests_task.delay"
    )
    def test_eligible_policy_no_open_requests(self, mock_delay):
        policy, config = self._make_policy_with_config()
        LearnerCreditRequestFactory(
            enterprise_customer_uuid=policy.enterprise_customer_uuid,
            learner_credit_request_config=config,
            state=SubsidyRequestStates.APPROVED,
        )
        call_command(self.command_name)
        mock_delay.assert_not_called()

    @mock.patch(
        "enterprise_access.apps.subsidy_request.management.commands."
        "send_learner_credit_bnr_daily_digest."
        "send_learner_credit_bnr_admins_email_with_new_requests_task.delay"
    )
    def test_enqueues_for_open_request(self, mock_delay):
        policy, config = self._make_policy_with_config()
        LearnerCreditRequestFactory(
            enterprise_customer_uuid=policy.enterprise_customer_uuid,
            learner_credit_request_config=config,
            state=SubsidyRequestStates.REQUESTED,
        )
        call_command(self.command_name)
        assert mock_delay.call_count == 1
        args = mock_delay.call_args[0]
        assert str(policy.uuid) == args[0]
        assert str(config.uuid) == args[1]
        assert str(policy.enterprise_customer_uuid) == args[2]

    @mock.patch(
        "enterprise_access.apps.subsidy_request.management.commands."
        "send_learner_credit_bnr_daily_digest."
        "send_learner_credit_bnr_admins_email_with_new_requests_task.delay"
    )
    def test_old_open_request_still_enqueues(self, mock_delay):
        policy, config = self._make_policy_with_config()
        req = LearnerCreditRequestFactory(
            enterprise_customer_uuid=policy.enterprise_customer_uuid,
            learner_credit_request_config=config,
            state=SubsidyRequestStates.REQUESTED,
        )
        # Make it "old" (yesterday)
        req.created = timezone.now() - timedelta(days=7)
        req.save(update_fields=["created"])

        call_command(self.command_name)
        assert mock_delay.call_count == 1

    @mock.patch(
        "enterprise_access.apps.subsidy_request.management.commands."
        "send_learner_credit_bnr_daily_digest."
        "send_learner_credit_bnr_admins_email_with_new_requests_task.delay"
    )
    def test_multiple_policies_mixed(self, mock_delay):
        policy_a, config_a = self._make_policy_with_config()
        LearnerCreditRequestFactory(
            enterprise_customer_uuid=policy_a.enterprise_customer_uuid,
            learner_credit_request_config=config_a,
            state=SubsidyRequestStates.REQUESTED,
        )

        self._make_policy_with_config()

        policy_c, config_c = self._make_policy_with_config()
        LearnerCreditRequestFactory(
            enterprise_customer_uuid=policy_c.enterprise_customer_uuid,
            learner_credit_request_config=config_c,
            state=SubsidyRequestStates.REQUESTED,
        )

        call_command(self.command_name)

        assert mock_delay.call_count == 2
        calls = [
            mock.call(
                str(policy_a.uuid),
                str(config_a.uuid),
                str(policy_a.enterprise_customer_uuid),
            ),
            mock.call(
                str(policy_c.uuid),
                str(config_c.uuid),
                str(policy_c.enterprise_customer_uuid),
            ),
        ]
        actual = [c.args for c in mock_delay.call_args_list]
        assert sorted(calls) == sorted([mock.call(*args) for args in actual])
