"""
Factoryboy factories.
"""

from uuid import uuid4

import factory
from faker import Faker

from ..models import LearnerContentAssignment

FAKER = Faker()


def random_content_key():
    """
    Helper to craft a random content key.
    """
    fake_words = [
        FAKER.word() + str(FAKER.random_int())
        for _ in range(3)
    ]
    return 'course-v1:{}+{}+{}'.format(*fake_words)


class LearnerContentAssignmentFactory(factory.django.DjangoModelFactory):
    """
    Base Test factory for the ``LearnerContentAssisgnment`` model.
    """
    class Meta:
        model = LearnerContentAssignment

    uuid = factory.LazyFunction(uuid4)
    learner_email = factory.LazyAttribute(lambda _: FAKER.email())
    lms_user_id = factory.LazyAttribute(lambda _: FAKER.pyint())
    content_key = factory.LazyAttribute(lambda _: random_content_key())
    content_quantity = factory.LazyAttribute(lambda _: FAKER.pyint())
