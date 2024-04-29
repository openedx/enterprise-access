"""
Defines shared subsidy access policy test mixins.
"""
from unittest.mock import patch

from django.core.cache import cache as django_cache

from ..models import SubsidyAccessPolicy


class MockPolicyDependenciesMixin:
    """
    Mixin to help mock out all access policy dependencies
    on external services.
    """
    def setUp(self):
        """
        Initialize mocked service clients.
        """
        super().setUp()
        subsidy_client_patcher = patch.object(
            SubsidyAccessPolicy, 'subsidy_client'
        )
        self.mock_subsidy_client = subsidy_client_patcher.start()

        transactions_cache_for_learner_patcher = patch(
            'enterprise_access.apps.subsidy_access_policy.models.get_and_cache_transactions_for_learner'
        )
        self.mock_transactions_cache_for_learner = transactions_cache_for_learner_patcher.start()

        catalog_contains_content_key_patcher = patch.object(
            SubsidyAccessPolicy, 'catalog_contains_content_key'
        )
        self.mock_catalog_contains_content_key = catalog_contains_content_key_patcher.start()

        get_content_metadata_patcher = patch(
            'enterprise_access.apps.subsidy_access_policy.models.get_and_cache_content_metadata'
        )
        self.mock_get_content_metadata = get_content_metadata_patcher.start()

        lms_api_client_patcher = patch.object(
            SubsidyAccessPolicy, 'lms_api_client'
        )

        self.mock_lms_api_client = lms_api_client_patcher.start()

        enterprise_user_record_patcher = patch.object(
            SubsidyAccessPolicy, 'enterprise_user_record'
        )

        self.mock_enterprise_user_record= enterprise_user_record_patcher.start()

        self.addCleanup(subsidy_client_patcher.stop)
        self.addCleanup(transactions_cache_for_learner_patcher.stop)
        self.addCleanup(catalog_contains_content_key_patcher.stop)
        self.addCleanup(get_content_metadata_patcher.stop)
        self.addCleanup(lms_api_client_patcher.stop)
        self.addCleanup(enterprise_user_record_patcher.stop)
        self.addCleanup(django_cache.clear)  # clear any leftover policy locks.
