"""
Factoryboy factories.
"""

from uuid import uuid4

import factory
from faker import Faker

from enterprise_access.apps.subsidy_access_policy.constants import AccessMethods
from enterprise_access.apps.subsidy_access_policy.models import (
    CappedEnrollmentLearnerCreditAccessPolicy,
    PerLearnerEnrollmentCreditAccessPolicy,
    PerLearnerSpendCreditAccessPolicy,
    SubscriptionAccessPolicy
)

FAKER = Faker()


class SubsidyAccessPolicyFactory(factory.django.DjangoModelFactory):
    """
    Base Test factory for the `SubscriptionAccessPolicy` `PerLearnerEnrollmentCreditAccessPolicy` models.
    """

    uuid = factory.LazyFunction(uuid4)
    enterprise_customer_uuid = factory.LazyFunction(uuid4)
    group_uuid = factory.LazyFunction(uuid4)
    catalog_uuid = factory.LazyFunction(uuid4)
    subsidy_uuid = factory.LazyFunction(uuid4)
    access_method = AccessMethods.DIRECT


class SubscriptionAccessPolicyFactory(SubsidyAccessPolicyFactory):
    """
    Test factory for the ` SubscriptionAccessPolicy` model.
    """

    class Meta:
        model = SubscriptionAccessPolicy


class PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(SubsidyAccessPolicyFactory):
    """
    Test factory for the `PerLearnerEnrollmentCreditAccessPolicy` model.
    """
    per_learner_enrollment_limit = factory.LazyAttribute(lambda _: FAKER.pyint())

    class Meta:
        model = PerLearnerEnrollmentCreditAccessPolicy


class PerLearnerSpendCapLearnerCreditAccessPolicyFactory(SubsidyAccessPolicyFactory):
    """
    Test factory for the `PerLearnerSpendCreditAccessPolicy` model.
    """
    per_learner_spend_limit = factory.LazyAttribute(lambda _: FAKER.pyint())

    class Meta:
        model = PerLearnerSpendCreditAccessPolicy


class CappedEnrollmentLearnerCreditAccessPolicyFactory(SubsidyAccessPolicyFactory):
    """
    Test factory for the `CappedEnrollmentLearnerCreditAccessPolicy` model.
    """
    spend_limit = factory.LazyAttribute(lambda _: FAKER.pyint())

    class Meta:
        model = CappedEnrollmentLearnerCreditAccessPolicy
