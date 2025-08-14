"""
Test factories for provisioning app.
"""
import uuid

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from enterprise_access.apps.provisioning.models import ProvisionNewCustomerWorkflow


class ProvisionNewCustomerWorkflowFactory(DjangoModelFactory):
    """
    Factory for creating ProvisionNewCustomerWorkflow instances for testing.
    """
    class Meta:
        model = ProvisionNewCustomerWorkflow

    @factory.lazy_attribute
    def input_data(self):
        """
        Generate input data with properly serialized values
        """
        # pylint: disable=protected-access
        fake = factory.Faker._get_faker()

        return {
            'create_customer_input': {
                'name': fake.company(),
                'slug': f"test-{fake.slug()}",
                'country': 'US',
            },
            'create_enterprise_admin_users_input': {
                'user_emails': [fake.email(), fake.email()],
            },
            'create_catalog_input': {},
            'create_customer_agreement_input': {},
            'create_subscription_plan_input': {
                'title': 'Test Subscription Plan',
                'salesforce_opportunity_line_item': str(uuid.uuid4()),
                'start_date': timezone.now().date().isoformat(),
                'expiration_date': (timezone.now() + timezone.timedelta(days=365)).date().isoformat(),
                'desired_num_licenses': 10,
                'product_id': 123,
            },
        }

    output_data = factory.Dict({})
    created = factory.LazyFunction(timezone.now)
    modified = factory.LazyFunction(timezone.now)

    @classmethod
    def create_complete_workflow(cls, **kwargs):
        """
        Create a workflow that has completed successfully with sensible output data.
        """
        output_data = {
            'create_customer_output': {
                'uuid': str(uuid.uuid4()),
                'name': kwargs.get('enterprise_name', 'Test Enterprise'),
                'slug': kwargs.get('enterprise_slug', 'test-enterprise'),
                'country': 'US',
            },
            'create_enterprise_admin_users_output': {
                'enterprise_customer_uuid': str(uuid.uuid4()),
                'created_admins': [{'user_email': kwargs.get('admin_email', 'admin@example.com')}],
                'existing_admins': [],
            },
            'create_catalog_output': {
                'uuid': str(uuid.uuid4()),
                'enterprise_customer_uuid': str(uuid.uuid4()),
                'title': 'Test Catalog',
                'catalog_query_id': 123,
            },
            'create_customer_agreement_output': {
                'uuid': str(uuid.uuid4()),
                'enterprise_customer_uuid': str(uuid.uuid4()),
            },
            'create_subscription_plan_output': {
                'uuid': str(uuid.uuid4()),
                'title': 'Test Subscription Plan',
                'salesforce_opportunity_line_item': str(uuid.uuid4()),
                'created': timezone.now().isoformat(),
                'start_date': timezone.now().date().isoformat(),
                'expiration_date': (timezone.now() + timezone.timedelta(days=365)).date().isoformat(),
                'is_active': True,
                'is_current': True,
                'plan_type': 'subscription',
                'enterprise_catalog_uuid': str(uuid.uuid4()),
            },
        }

        return cls.create(
            input_data=cls.input_data,
            output_data=output_data,
            **kwargs,
        )
