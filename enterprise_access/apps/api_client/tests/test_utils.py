"""
Helper utilities for api_client tests.
"""
from django.test import TestCase
from faker import Faker
from requests import Response

from enterprise_access.apps.api_client.constants import LicenseStatuses
from enterprise_access.apps.api_client.tests.test_constants import DATE_FORMAT_ISO_8601, DATE_FORMAT_ISO_8601_MS
from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.utils import _days_from_now


class MockResponse(Response):
    """
    Useful for mocking HTTP responses, especially for code that relies on raise_for_status().
    """
    def __init__(self, json_data, status_code):
        super().__init__()
        self.json_data = json_data
        self.status_code = status_code

    def json(self):  # pylint: disable=arguments-differ
        return self.json_data


class MockEnterpriseMetadata(TestCase):
    """
    Mock enterprise metadata
    """
    def setUp(self):
        super().setUp()
        self.faker = Faker()

        self.base_paginated_response = {
            "next": None,
            "previous": None,
            "count": 0,
            "results": [],
        }

        self.mock_enterprise_customer_uuid = self.faker.uuid4()
        self.mock_enterprise_customer_slug = "test_slug"
        self.mock_enterprise_catalog_uuid = self.faker.uuid4()
        self.mock_user = UserFactory()
        self.mock_user_email = self.mock_user.email


class MockLicenseManagerMetadataMixin(MockEnterpriseMetadata):
    """
    Mixin for the TestLicenseManagerUserApiClient
    """
    def setUp(self):
        super().setUp()

        self.mock_learner_license_uuid = str(self.faker.uuid4())
        self.mock_learner_license_activation_uuid = str(self.faker.uuid4())
        self.mock_license_activation_key = str(self.faker.uuid4())
        self.mock_auto_apply_uuid = str(self.faker.uuid4())
        self.mock_subscription_plan_uuid = str(self.faker.uuid4())
        self.mock_customer_agreement_uuid = str(self.faker.uuid4())

        self.mock_subscription_plan = {
            "title": "mock_title",
            "uuid": self.mock_subscription_plan_uuid,
            "start_date": _days_from_now(-50, DATE_FORMAT_ISO_8601),
            "expiration_date": _days_from_now(50, DATE_FORMAT_ISO_8601),
            "enterprise_customer_uuid": self.mock_enterprise_customer_uuid,
            "enterprise_catalog_uuid": self.mock_enterprise_catalog_uuid,
            "is_active": True,
            "is_current": True,
            "is_revocation_cap_enabled": False,
            "days_until_expiration": 50,
            "days_until_expiration_including_renewals": 50,
            "is_locked_for_renewal_processing": False,
            "should_auto_apply_licenses": False,
            "created": _days_from_now(-60, DATE_FORMAT_ISO_8601)
        }
        self.mock_customer_agreement = {
            "uuid": self.mock_customer_agreement_uuid,
            "enterprise_customer_uuid": self.mock_enterprise_customer_uuid,
            "enterprise_customer_slug": self.mock_enterprise_customer_slug,
            "default_enterprise_catalog_uuid": self.mock_enterprise_catalog_uuid,
            "disable_expiration_notifications": False,
            "net_days_until_expiration": 50,
            "subscription_for_auto_applied_licenses": self.mock_subscription_plan_uuid,
            "available_subscription_catalogs": [
                self.mock_enterprise_catalog_uuid,
            ],
            "enable_auto_applied_subscriptions_with_universal_link": False,
            "has_custom_license_expiration_messaging_v2": False,
            "modal_header_text_v2": None,
            "expired_subscription_modal_messaging_v2": None,
            "button_label_in_modal_v2": None,
            "url_for_button_in_modal_v2": None
        }
        self.mock_subscription_license = {
            "uuid": self.mock_learner_license_activation_uuid,
            "status": LicenseStatuses.ACTIVATED,
            "user_email": self.mock_user_email,
            "activation_date": _days_from_now(-10, DATE_FORMAT_ISO_8601_MS),
            "last_remind_date": _days_from_now(-5, DATE_FORMAT_ISO_8601_MS),
            "subscription_plan_uuid": self.mock_subscription_plan_uuid,
            "revoked_date": None,
            "activation_key": self.mock_license_activation_key,
            "subscription_plan": self.mock_subscription_plan
        }
        self.mock_learner_license_auto_apply_response = {
            **self.mock_subscription_license,
            'customer-agreement': self.mock_customer_agreement,
            'subscription_plan': self.mock_subscription_plan,
        }
