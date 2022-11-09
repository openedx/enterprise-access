from uuid import uuid4, UUID
import mock
from django.test import TestCase
import pytest

from enterprise_access.apps.subsidy.models import SubscriptionAccessPolicy, LearnerCreditAccessPolicy
from enterprise_access.apps.ledger.models import Ledger, Transaction, Reversal, UnitChoices
from enterprise_access.apps.subsidy import api as subsidy_api
from enterprise_access.apps.ledger import api as ledger_api

@pytest.fixture
def group_a():
    return {
        'uuid': uuid4(),
    }


@pytest.fixture
def catalog_a():
    return {
        'uuid': uuid4(),
    }


@pytest.fixture
def ledger_fixture():
    ledger_idp_key = uuid4()
    ledger = ledger_api.create_ledger(
        unit=UnitChoices.SEATS,
        idempotency_key=ledger_idp_key,
    )
    ledger_api.create_transaction(
        ledger,
        quantity=100,
        idempotency_key=f'ledger-{ledger_idp_key}-init-100',
    )
    return ledger


@pytest.fixture
def subscription_fixture():
    return subsidy_api.create_subscription_subsidy(
        customer_uuid=uuid4(),
        subscription_plan_uuid=uuid4(),
        unit=UnitChoices.SEATS,
        starting_balance=100,
    )


@pytest.fixture
def subs_policy_fixture(group_a, subscription_fixture, catalog_a):
    subs_policy = SubscriptionAccessPolicy.objects.create(
        group_uuid=group_a['uuid'],
        subsidy=subscription_fixture,
        catalog_uuid=catalog_a['uuid'],
    )
    # make it so any learner is always in the group for this policy
    subs_policy.group_client.get_groups_for_learner.return_value = [
        {'uuid': subs_policy.group_uuid},
    ]
    return subs_policy

@pytest.fixture
def learner_credit_fixture():
    return subsidy_api.create_learner_credit_subsidy(
        customer_uuid=uuid4(),
        unit=UnitChoices.USD_CENTS,
        starting_balance=1000000,
    )


@pytest.fixture
def learner_credit_policy_fixture(group_a, learner_credit_fixture, catalog_a):
    lc_policy = LearnerCreditAccessPolicy.objects.create(
        group_uuid=group_a['uuid'],
        subsidy=learner_credit_fixture,
        catalog_uuid=catalog_a['uuid'],
    )
    # make it so any learner is always in the group for this policy
    lc_policy.group_client.get_groups_for_learner.return_value = [
        {'uuid': lc_policy.group_uuid},
    ]
    return lc_policy

@pytest.mark.django_db
def test_create_subsidy_happy_path(subscription_fixture):
    assert subscription_fixture.unit == UnitChoices.SEATS


@pytest.mark.django_db
def test_subscription_fixture_policy_happy_paths(subs_policy_fixture):
    some_learner_id = 'abcde12345'
    assert subs_policy_fixture.subsidy.get_license_for_learner(some_learner_id)['uuid'] is not None

    # Test the flow for checking if a learner is entitled to a subscription plan's license
    # make the learner not have a license yet.
    subs_policy_fixture.subsidy.subscription_client.get_license_for_learner.return_value = None
    # makes is_redeemable() return True
    subs_policy_fixture.subsidy.subscription_client.get_plan_metadata.return_value = {'licenses': {'pending': 50}}

    assert subs_policy_fixture.is_entitled(some_learner_id, 'some-content-id')

    # Test the flow for giving the license as an entitlement
    # subsidy.create_redemption() is mocked to return a "license uuid" in its implementation
    granted_license_entitlement = subs_policy_fixture.use_entitlement(some_learner_id, 'some-content-id')
    assert granted_license_entitlement == {
        'status': 'activated',
        'uuid': mock.ANY,
    }
    assert type(granted_license_entitlement['uuid']) == UUID


@pytest.mark.django_db
def test_subsidy_has_balance(subscription_fixture):
    assert subscription_fixture.unit == UnitChoices.SEATS
    subscription_fixture.subscription_client.get_plan_metadata.return_value = {
        'licenses': {'pending': 50, 'total': 100}
    }
    subsidy_api.sync_subscription(subscription_fixture, request_user='bob')
    # TODO: explain the subtlety that results in a "fresh" subscription balance being zero
    assert subscription_fixture.current_balance() == 0


@pytest.mark.django_db
def test_create_learner_credit_subsidy(learner_credit_fixture):
    assert learner_credit_fixture.current_balance() == 1000000


@pytest.mark.django_db
def test_learner_credit_policy_entitlement(learner_credit_policy_fixture):
    learner_credit_policy_fixture.subsidy.catalog_client.get_content_metadata.return_value = {
        'price': 100,
    }
    reference_id = 'the-enrollment-id'
    some_content_key = uuid4()
    some_learner_id = 123456
    learner_credit_policy_fixture.subsidy.enrollment_client.enroll.return_value = reference_id
    assert learner_credit_policy_fixture.is_entitled(some_learner_id, some_content_key)

    balance_before_entitlement = learner_credit_policy_fixture.subsidy.current_balance()
    assert learner_credit_policy_fixture.use_entitlement(some_learner_id, some_content_key)
    assert learner_credit_policy_fixture.subsidy.current_balance() == balance_before_entitlement - (100 * 100)


# @pytest.mark.django_db
# def test_learner_credit_policy_is_redeemable(learner_credit_policy_fixture):
#     # TODO: make multiple subsidies, only one of which would actually
#     # allow a learner to enroll in a content.
#     balance_before_entitlement = learner_credit_policy_fixture.subsidy.current_balance()

#     quantity = 1000
#     
#     learner_credit_policy_fixture.catalog_client.catalog_contains_content.return_value = True
#     learner_credit_policy_fixture.can_learner_redeem_for_content('a-learner-id', 'some-content-key')
#     learner_credit_policy_fixture.redeem_for_content('a-learner-id', 'some-content-key')

#     current_balance = learner_credit_policy_fixture.subsidy.current_balance()
#     assert balance_before_entitlement == (current_balance + quantity)
