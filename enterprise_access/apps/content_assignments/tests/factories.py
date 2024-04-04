"""
Factoryboy factories.
"""

from uuid import uuid4

import factory
from faker import Faker

from test_utils import random_content_key

from ..models import AssignmentConfiguration, LearnerContentAssignment

FAKER = Faker()


class AssignmentConfigurationFactory(factory.django.DjangoModelFactory):
    """
    Base Test factory for the ``AssignmentConfiguration`` model.
    """
    class Meta:
        model = AssignmentConfiguration

    uuid = factory.LazyFunction(uuid4)
    enterprise_customer_uuid = factory.LazyFunction(uuid4)
    active = True


class LearnerContentAssignmentFactory(factory.django.DjangoModelFactory):
    """
    Base Test factory for the ``LearnerContentAssignment`` model.
    """
    class Meta:
        model = LearnerContentAssignment

    uuid = factory.LazyFunction(uuid4)
    learner_email = factory.LazyAttribute(lambda _: FAKER.email())
    lms_user_id = factory.LazyAttribute(lambda _: FAKER.pyint())
    content_key = factory.LazyAttribute(lambda _: random_content_key())
    content_title = factory.LazyAttribute(lambda _: f'{FAKER.word()}: a master class')
    content_quantity = factory.LazyAttribute(lambda _: FAKER.pyfloat(positive=False, right_digits=0))
