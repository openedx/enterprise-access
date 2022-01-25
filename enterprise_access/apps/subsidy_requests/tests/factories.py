from uuid import uuid4

import factory
from faker import Faker

from enterprise_access.apps.core.models import User

from enterprise_access.apps.subsidy_requests.constants import (
    PendingRequestReminderFrequency,
    SubsidyTypeChoices,
)
from enterprise_access.apps.subsidy_requests.models import (
    SubsidyRequestCustomerConfiguration,
)

USER_PASSWORD = 'password'

FAKER = Faker()


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

