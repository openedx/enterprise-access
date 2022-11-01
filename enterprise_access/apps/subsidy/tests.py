from uuid import uuid4, UUID
import mock
from django.test import TestCase
import pytest

from enterprise_access.apps.subsidy.models import *
from enterprise_access.apps.ledger.models import Ledger, Transaction, Reversal, UnitChoices

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
def subscription_subsidy():
    return SubscriptionSubsidy.objects.create(
        starting_balance=10000,
        subsidy_type='subscription',
        subscription_plan_uuid=uuid4(),
        customer_uuid=uuid4(),
    )


@pytest.fixture
def policy_fixture(group_a, subscription_subsidy, catalog_a):
    policy = SubscriptionAccessPolicy.objects.create(
        group_uuid=group_a['uuid'],
        subsidy=subscription_subsidy,
        catalog_uuid=catalog_a['uuid'],
        total_value=5000,
    )
    # make it so any learner is always in the group for this policy
    policy.group_client.get_groups_for_learner.return_value = [
        {'uuid': policy.group_uuid},
    ]
    return policy


@pytest.mark.django_db
def test_create_subsidy_happy_path(subscription_subsidy):
    assert subscription_subsidy.subsidy_type == 'subscription'


@pytest.mark.django_db
def test_subscription_subsidy_policy_happy_paths(policy_fixture):
    assert policy_fixture.total_value == 5000
    some_learner_id = 'abcde12345'
    assert policy_fixture.get_license_for_learner(some_learner_id)['uuid'] is not None

    # Test the flow for checking if a learner is entitled to a subscription
    # make the learner not have a license yet.
    policy_fixture.subsidy.subscription_client.get_license_for_learner.return_value = None
    # makes is_redeemable() return True
    policy_fixture.subsidy.subscription_client.get_plan_metadata.return_value = {'licenses': {'pending': 45}}

    assert policy_fixture.is_learner_entitled_to_policy(some_learner_id)

    # Test the flow for giving the license as an entitlement
    # subsidy.create_redemption() is mocked to return a "license uuid" in its implementation
    granted_license_entitlement = policy_fixture.give_entitlement(some_learner_id)
    assert granted_license_entitlement == {
        'status': 'activated',
        'uuid': mock.ANY,
    }
    assert type(granted_license_entitlement['uuid']) == UUID

    # Test the flow for checking if learner is entitled to content in the subscription subsidy
    policy_fixture.catalog_client.catalog_contains_content.return_value = True
    policy_fixture.subsidy.subscription_client.get_license_for_learner.return_value = {
        'uuid': uuid4(),
    }
    some_content_key = 'doesnt matter'
    assert policy_fixture.is_learner_entitled_to_content_in_policy(some_learner_id, some_content_key)

    # Test the flow for giving entitlement in the context of a specific content key
    assert policy_fixture.give_entitlement_for_content(some_learner_id, some_content_key)
