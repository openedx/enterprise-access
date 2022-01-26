"""
Factoryboy factories.
"""

from datetime import datetime
from uuid import uuid4

import factory
from faker import Faker

from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates, SubsidyTypeChoices
from enterprise_access.apps.subsidy_request.models import (
    CouponCodeRequest,
    LicenseRequest,
    SubsidyRequestCustomerConfiguration
)

FAKER = Faker()

class SubsidyRequestFactory(factory.django.DjangoModelFactory):
    """
    Base Test factory for the `LicenseRequest` and `CouponCodeRequest` model.
    """

    uuid = factory.LazyFunction(uuid4)
    lms_user_id = factory.LazyAttribute(lambda x: FAKER.pyint())
    course_id = factory.LazyFunction(uuid4)
    enterprise_customer_uuid = factory.LazyFunction(uuid4)
    state = SubsidyRequestStates.PENDING_REVIEW
    reviewed_at = datetime.utcnow()
    reviewer_lms_user_id = factory.LazyAttribute(lambda x: FAKER.pyint())
    denial_reason = None


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

    coupon_id = factory.LazyAttribute(lambda x: FAKER.pyint())
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
