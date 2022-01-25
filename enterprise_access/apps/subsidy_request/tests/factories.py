"""
Factoryboy factories.
"""

from datetime import datetime
from uuid import uuid4

import factory
from faker import Faker

from enterprise_access.apps.core.models import User
from enterprise_access.apps.subsidy_request.constants import (
    PendingRequestReminderFrequency,
    SubsidyRequestStates,
    SubsidyTypeChoices,
)
from enterprise_access.apps.subsidy_request.models import (
    CouponCodeRequest,
    LicenseRequest,
    SubsidyRequestCustomerConfiguration,
)

USER_PASSWORD = 'password'

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

    coupon_id = factory.LazyFunction(uuid4)
    coupon_code = factory.LazyFunction(uuid4)

    class Meta:
        model = CouponCodeRequest

class UserFactory(factory.django.DjangoModelFactory):
    """
    Test factory for the `User` model.
    """
    id = factory.Sequence(lambda n: n + 1)
    username = factory.Faker('user_name')
    password = factory.PostGenerationMethodCall('set_password', USER_PASSWORD)
    email = factory.Faker('email')
    first_name = factory.Faker('first_name')
    last_name = factory.Faker('last_name')
    is_active = True
    is_staff = False
    is_superuser = False

    class Meta:
        model = User


class SubsidyRequestCustomerConfigurationFactory(factory.django.DjangoModelFactory):
    """
    Test factory for the `SubsidyRequestCustomerConfiguration` model.
    """
    enterprise_customer_uuid = factory.LazyFunction(uuid4)
    subsidy_requests_enabled = True
    subsidy_type = SubsidyTypeChoices.LICENSE
    pending_request_reminder_frequency = PendingRequestReminderFrequency.NEVER
    changed_by = None

    class Meta:
        model = SubsidyRequestCustomerConfiguration
