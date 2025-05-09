"""
Factoryboy factories.
"""

from uuid import uuid4

import factory
from faker import Faker

from enterprise_access.apps.subsidy_access_policy.constants import AccessMethods
from enterprise_access.apps.subsidy_access_policy.models import (
    AssignedLearnerCreditAccessPolicy,
    ForcedPolicyRedemption,
    PerLearnerEnrollmentCreditAccessPolicy,
    PerLearnerSpendCreditAccessPolicy,
    PolicyGroupAssociation
)
from test_utils import random_content_key

FAKER = Faker()


class SubsidyAccessPolicyFactory(factory.django.DjangoModelFactory):
    """
    Base Test factory for the `SubscriptionAccessPolicy` `PerLearnerEnrollmentCreditAccessPolicy` models.
    """

    uuid = factory.LazyFunction(uuid4)
    enterprise_customer_uuid = factory.LazyFunction(uuid4)
    catalog_uuid = factory.LazyFunction(uuid4)
    subsidy_uuid = factory.LazyFunction(uuid4)
    access_method = AccessMethods.DIRECT
    description = 'A generic description'
    spend_limit = factory.LazyAttribute(lambda _: FAKER.pyint(min_value=1))
    active = True
    learner_credit_request_config = None


class PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(SubsidyAccessPolicyFactory):
    """
    Test factory for the `PerLearnerEnrollmentCreditAccessPolicy` model.
    """
    per_learner_enrollment_limit = factory.LazyAttribute(lambda _: FAKER.pyint(min_value=1))

    class Meta:
        model = PerLearnerEnrollmentCreditAccessPolicy


class PerLearnerSpendCapLearnerCreditAccessPolicyFactory(SubsidyAccessPolicyFactory):
    """
    Test factory for the `PerLearnerSpendCreditAccessPolicy` model.
    """
    per_learner_spend_limit = factory.LazyAttribute(lambda _: FAKER.pyint(min_value=1))

    class Meta:
        model = PerLearnerSpendCreditAccessPolicy


class AssignedLearnerCreditAccessPolicyFactory(SubsidyAccessPolicyFactory):
    """
    Test factory for the `AssignedLearnerCreditAccessPolicy` model.
    """

    class Meta:
        model = AssignedLearnerCreditAccessPolicy

    access_method = AccessMethods.ASSIGNED
    spend_limit = factory.LazyAttribute(lambda _: FAKER.pyint(min_value=1))
    per_learner_spend_limit = None
    per_learner_enrollment_limit = None


class PolicyGroupAssociationFactory(factory.django.DjangoModelFactory):
    """
    Test factory for the `PolicyGroupAssociation` model.
    """

    class Meta:
        model = PolicyGroupAssociation

    enterprise_group_uuid = factory.LazyFunction(uuid4)
    subsidy_access_policy = factory.SubFactory(SubsidyAccessPolicyFactory)


class ForcedPolicyRedemptionFactory(factory.django.DjangoModelFactory):
    """
    Test factory for ForcedPolicyRedemptions.
    """
    class Meta:
        model = ForcedPolicyRedemption

    lms_user_id = factory.LazyAttribute(lambda _: FAKER.pyint(min_value=1))
    course_run_key = factory.LazyAttribute(lambda _: random_content_key())
    content_price_cents = factory.LazyAttribute(lambda _: FAKER.pytint(min_value=1, max_value=1000))
