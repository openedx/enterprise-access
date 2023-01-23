"""
Factoryboy factories.
"""

from uuid import uuid4

import factory
from faker import Faker

from enterprise_access.apps.subsidy_access_policy.constants import AccessMethods
from enterprise_access.apps.subsidy_access_policy.models import (
    CappedEnrollmentLearnerCreditAccessPolicy,
    LearnerCreditAccessPolicy,
    LicenseAccessPolicy,
    LicenseRequestAccessPolicy,
    PerLearnerEnrollmentCapLearnerCreditAccessPolicy,
    PerLearnerSpendCapLearnerCreditAccessPolicy,
    SubscriptionAccessPolicy
)

FAKER = Faker()

class SubsidyAccessPolicyFactory(factory.django.DjangoModelFactory):
    """
    Base Test factory for the `SubscriptionAccessPolicy` `LearnerCreditAccessPolicy`
    `PerLearnerEnrollmentCapLearnerCreditAccessPolicy` LicenseRequestAccessPolicy models.
    """

    uuid = factory.LazyFunction(uuid4)
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


class LicenseRequestAccessPolicyFactory(SubscriptionAccessPolicyFactory):
    """
    Test factory for the `LicenseRequestAccessPolicy` model.
    """
    access_method = AccessMethods.REQUEST

    class Meta:
        model = LicenseRequestAccessPolicy


class LicenseAccessPolicyFactory(SubscriptionAccessPolicyFactory):
    """
    Test factory for the `LicenseAccessPolicy` model.
    """
    access_method = AccessMethods.DIRECT

    class Meta:
        model = LicenseAccessPolicy


class LearnerCreditAccessPolicyFactory(SubsidyAccessPolicyFactory):
    """
    Test factory for the `LearnerCreditAccessPolicy` model.
    """
    class Meta:
        model = LearnerCreditAccessPolicy

class PerLearnerEnrollmentCapLearnerCreditAccessPolicyFactory(LearnerCreditAccessPolicyFactory):
    """
    Test factory for the `PerLearnerEnrollmentCapLearnerCreditAccessPolicy` model.
    """
    per_learner_enrollment_limit = factory.LazyAttribute(lambda _: FAKER.pyint())

    class Meta:
        model = PerLearnerEnrollmentCapLearnerCreditAccessPolicy


class PerLearnerSpendCapLearnerCreditAccessPolicyFactory(LearnerCreditAccessPolicyFactory):
    """
    Test factory for the `PerLearnerSpendCapLearnerCreditAccessPolicy` model.
    """
    per_learner_spend_limit = factory.LazyAttribute(lambda _: FAKER.pyint())

    class Meta:
        model = PerLearnerSpendCapLearnerCreditAccessPolicy


class CappedEnrollmentLearnerCreditAccessPolicyFactory(LearnerCreditAccessPolicyFactory):
    """
    Test factory for the `CappedEnrollmentLearnerCreditAccessPolicy` model.
    """
    spend_limit = factory.LazyAttribute(lambda _: FAKER.pyint())

    class Meta:
        model = CappedEnrollmentLearnerCreditAccessPolicy
