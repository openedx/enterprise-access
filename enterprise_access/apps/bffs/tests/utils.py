"""
Test utilities for BFFs.
"""

from django.test import RequestFactory, TestCase
from faker import Faker

from enterprise_access.apps.core.tests.factories import UserFactory


class TestHandlerContextMixin(TestCase):
    """
    Mixin for HandlerContext tests
    """

    def setUp(self):
        super().setUp()
        self.maxDiff = None
        self.factory = RequestFactory()
        self.mock_user = UserFactory()
        self.mock_staff_user = UserFactory(is_staff=True)
        self.faker = Faker()

        self.mock_enterprise_customer_uuid = self.faker.uuid4()
        self.mock_enterprise_customer_slug = 'mock-slug'
        self.mock_enterprise_customer_uuid_2 = self.faker.uuid4()
        self.mock_enterprise_customer_slug_2 = 'mock-slug-2'

        # Mock request
        self.request = self.factory.get('sample/api/call')
        self.request.user = self.mock_user
        self.request.query_params = {
            'enterprise_customer_uuid': self.mock_enterprise_customer_uuid
        }
        self.request.data = {}

        # Mock enterprise customer data
        self.mock_enterprise_customer = {
            'uuid': self.mock_enterprise_customer_uuid,
            'slug': self.mock_enterprise_customer_slug,
            'enable_learner_portal': True,
        }
        self.mock_enterprise_customer_2 = {
            'uuid': self.mock_enterprise_customer_uuid_2,
            'slug': self.mock_enterprise_customer_slug_2,
            'enable_learner_portal': True,
        }
        self.mock_enterprise_learner_response_data = {
            'results': [
                {
                    'active': True,
                    'enterprise_customer': self.mock_enterprise_customer,
                },
                {
                    'active': False,
                    'enterprise_customer': self.mock_enterprise_customer_2,
                },
            ],
            'enterprise_features': {'feature_flag': True}
        }
