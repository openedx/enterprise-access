"""
Factoryboy factories.
"""

from datetime import datetime
from uuid import uuid4

import factory
from faker import Faker

from enterprise_access.apps.content_assignments.tests.factories import LearnerContentAssignmentFactory
from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates, SubsidyTypeChoices
from enterprise_access.apps.subsidy_request.models import (
    CouponCodeRequest,
    LearnerCreditRequest,
    LearnerCreditRequestActions,
    LearnerCreditRequestConfiguration,
    LicenseRequest,
    SubsidyRequestCustomerConfiguration
)
from enterprise_access.apps.subsidy_request.utils import get_action_choice, get_user_message_choice

FAKER = Faker()


class SubsidyRequestFactory(factory.django.DjangoModelFactory):
    """
    Base Test factory for the `LicenseRequest` and `CouponCodeRequest` model.
    """

    uuid = factory.LazyFunction(uuid4)
    user = factory.SubFactory(UserFactory)
    course_id = factory.LazyFunction(uuid4)
    course_title = factory.LazyAttribute(lambda _: FAKER.word())
    course_partners = factory.LazyAttribute(lambda _: [{'uuid': uuid4(), 'name': FAKER.word()}])
    enterprise_customer_uuid = factory.LazyFunction(uuid4)
    state = SubsidyRequestStates.REQUESTED
    reviewed_at = datetime.utcnow()
    reviewer = factory.SubFactory(UserFactory)
    decline_reason = None


class LicenseRequestFactory(SubsidyRequestFactory):
    """
    Test factory for the `LicenseRequest` model.
    """

    subscription_plan_uuid = factory.LazyFunction(uuid4)
    license_uuid = factory.LazyFunction(uuid4)

    class Meta:
        model = LicenseRequest


class CouponCodeRequestFactory(SubsidyRequestFactory):
    """
    Test factory for the `CouponCodeRequest` model.
    """

    coupon_id = factory.LazyAttribute(lambda _: FAKER.pyint())
    coupon_code = factory.LazyFunction(uuid4)

    class Meta:
        model = CouponCodeRequest


class SubsidyRequestCustomerConfigurationFactory(factory.django.DjangoModelFactory):
    """
    Test factory for the `SubsidyRequestCustomerConfiguration` model.
    """
    enterprise_customer_uuid = factory.LazyFunction(uuid4)
    subsidy_requests_enabled = True
    subsidy_type = SubsidyTypeChoices.LICENSE
    changed_by = None

    class Meta:
        model = SubsidyRequestCustomerConfiguration


class LearnerCreditRequestConfigurationFactory(factory.django.DjangoModelFactory):
    """
    Test factory for the `LearnerCreditRequestConfiguration` model.
    """
    uuid = factory.LazyFunction(uuid4)
    active = True

    class Meta:
        model = LearnerCreditRequestConfiguration


class LearnerCreditRequestFactory(SubsidyRequestFactory):
    """
    Test factory for the `LearnerCreditRequest` model.
    """
    learner_credit_request_config = factory.SubFactory(LearnerCreditRequestConfigurationFactory)
    assignment = factory.SubFactory(LearnerContentAssignmentFactory)

    class Meta:
        model = LearnerCreditRequest


class LearnerCreditRequestActionsFactory(factory.django.DjangoModelFactory):
    """
    Test factory for the `LearnerCreditRequestActions` model.
    """
    uuid = factory.LazyFunction(uuid4)
    recent_action = get_action_choice(SubsidyRequestStates.REQUESTED)
    status = get_user_message_choice(SubsidyRequestStates.REQUESTED)
    learner_credit_request = factory.SubFactory(LearnerCreditRequestFactory)
    error_reason = None
    traceback = None

    class Meta:
        model = LearnerCreditRequestActions
