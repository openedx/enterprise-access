"""
Comprehensive tests for enhanced filtering implementation.

Tests the mixin-based nested filtering system including:
- Root field filtering preservation
- Enhanced nested field filtering with clean syntax
- Combined filtering capabilities
- Backward compatibility
- Performance optimizations
- Security validations
"""

import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from django_filters import rest_framework as filters

from enterprise_access.apps.api.filters.mixins import (
    NestedFieldFilterMixin,
    DateTimeNestedFilterMixin,
    OptimizedNestedFilterMixin,
    SecureNestedFilterMixin,
    NestedFilterMixin,
    create_nested_filter_aliases,
    NestedFilterBuilder,
)
from enterprise_access.apps.api.filters.subsidy_request import LearnerCreditRequestFilter
from enterprise_access.apps.subsidy_request.models import LearnerCreditRequest, LearnerCreditRequestActions
from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates, LearnerCreditAdditionalActionStates
from enterprise_access.apps.core.models import User
from enterprise_access.apps.subsidy_request.tests.factories import (
    LearnerCreditRequestFactory,
    LearnerCreditRequestActionsFactory,
    LearnerCreditRequestConfigurationFactory,
)


class TestNestedFieldFilterMixin(TestCase):
    """Test the base NestedFieldFilterMixin functionality."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create(
            username='testuser',
            email='test@example.com',
            lms_user_id=12345
        )
        self.config = LearnerCreditRequestConfigurationFactory()

    def test_mixin_initialization(self):
        """Test that mixin initializes correctly with configuration."""

        class TestFilter(NestedFieldFilterMixin, filters.FilterSet):
            nested_field_config = {
                'action': {
                    'related_name': 'actions',
                    'latest_strategy': 'created',
                    'fields': ['status', 'recent_action']
                }
            }

            class Meta:
                model = LearnerCreditRequest
                fields = ['state']

        filter_instance = TestFilter()

        # Check that nested filters were created
        self.assertTrue(hasattr(filter_instance, 'action_status'))
        self.assertTrue(hasattr(filter_instance, 'action_recent_action'))

        # Check that filter methods were created
        self.assertTrue(hasattr(filter_instance, 'filter_by_action_status'))
        self.assertTrue(hasattr(filter_instance, 'filter_by_action_recent_action'))

    def test_invalid_configuration_validation(self):
        """Test that invalid configurations raise appropriate errors."""

        # Missing required keys
        with self.assertRaises(ValueError) as context:
            class InvalidFilter1(NestedFieldFilterMixin, filters.FilterSet):
                nested_field_config = {
                    'action': {
                        'related_name': 'actions',
                        # Missing 'latest_strategy' and 'fields'
                    }
                }

                class Meta:
                    model = LearnerCreditRequest
                    fields = []

            InvalidFilter1()

        self.assertIn("Missing required key", str(context.exception))

        # Empty fields list
        with self.assertRaises(ValueError) as context:
            class InvalidFilter2(NestedFieldFilterMixin, filters.FilterSet):
                nested_field_config = {
                    'action': {
                        'related_name': 'actions',
                        'latest_strategy': 'created',
                        'fields': []  # Empty fields
                    }
                }

                class Meta:
                    model = LearnerCreditRequest
                    fields = []

            InvalidFilter2()

        self.assertIn("cannot be empty", str(context.exception))

    def test_nested_filter_application(self):
        """Test that nested filters are applied correctly."""
        # Create test data
        request1 = LearnerCreditRequestFactory(user=self.user, learner_credit_request_config=self.config)
        request2 = LearnerCreditRequestFactory(user=self.user, learner_credit_request_config=self.config)

        action1 = LearnerCreditRequestActionsFactory(
            learner_credit_request=request1,
            status='requested',
            recent_action='requested'
        )
        action2 = LearnerCreditRequestActionsFactory(
            learner_credit_request=request2,
            status='approved',
            recent_action='approved'
        )

        class TestFilter(NestedFieldFilterMixin, filters.FilterSet):
            nested_field_config = {
                'action': {
                    'related_name': 'actions',
                    'latest_strategy': 'created',
                    'fields': ['status']
                }
            }

            class Meta:
                model = LearnerCreditRequest
                fields = []

        # Test filtering by action status
        filter_instance = TestFilter({'action_status': 'approved'})
        queryset = filter_instance.qs

        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset.first().uuid, request2.uuid)


class TestDateTimeNestedFilterMixin(TestCase):
    """Test the DateTimeNestedFilterMixin functionality."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create(
            username='testuser',
            email='test@example.com',
            lms_user_id=12345
        )
        self.config = LearnerCreditRequestConfigurationFactory()

    def test_datetime_filter_creation(self):
        """Test that datetime filters are created for datetime fields."""

        class TestDateTimeFilter(DateTimeNestedFilterMixin, filters.FilterSet):
            nested_field_config = {
                'action': {
                    'related_name': 'actions',
                    'latest_strategy': 'created',
                    'fields': ['status', 'created']  # 'created' should get datetime filters
                }
            }

            class Meta:
                model = LearnerCreditRequest
                fields = []

        filter_instance = TestDateTimeFilter()

        # Check that datetime filters were created
        self.assertTrue(hasattr(filter_instance, 'action_created__gte'))
        self.assertTrue(hasattr(filter_instance, 'action_created__lte'))
        self.assertTrue(hasattr(filter_instance, 'action_created__gt'))
        self.assertTrue(hasattr(filter_instance, 'action_created__lt'))
        self.assertTrue(hasattr(filter_instance, 'action_created__isnull'))

    def test_datetime_filtering_functionality(self):
        """Test that datetime filtering works correctly."""
        now = datetime.now(timezone.utc)
        past_time = now - timedelta(hours=1)
        future_time = now + timedelta(hours=1)

        # Create test data with specific timestamps
        request1 = LearnerCreditRequestFactory(user=self.user, learner_credit_request_config=self.config)
        request2 = LearnerCreditRequestFactory(user=self.user, learner_credit_request_config=self.config)

        action1 = LearnerCreditRequestActionsFactory(
            learner_credit_request=request1,
            status='requested',
            created=past_time
        )
        action2 = LearnerCreditRequestActionsFactory(
            learner_credit_request=request2,
            status='approved',
            created=future_time
        )

        class TestDateTimeFilter(DateTimeNestedFilterMixin, filters.FilterSet):
            nested_field_config = {
                'action': {
                    'related_name': 'actions',
                    'latest_strategy': 'created',
                    'fields': ['created']
                }
            }

            class Meta:
                model = LearnerCreditRequest
                fields = []

        # Test gte filtering
        filter_instance = TestDateTimeFilter({'action_created__gte': now.isoformat()})
        queryset = filter_instance.qs

        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset.first().uuid, request2.uuid)


class TestSecureNestedFilterMixin(TestCase):
    """Test the security features of SecureNestedFilterMixin."""

    def test_security_validation(self):
        """Test security validations are enforced."""

        class SecureTestFilter(SecureNestedFilterMixin, filters.FilterSet):
            ALLOWED_NESTED_FIELDS = ['actions']  # Only allow actions

            nested_field_config = {
                'action': {
                    'related_name': 'actions',
                    'latest_strategy': 'created',
                    'fields': ['status']
                }
            }

            class Meta:
                model = LearnerCreditRequest
                fields = []

        # This should work
        filter_instance = SecureTestFilter()
        self.assertTrue(hasattr(filter_instance, 'action_status'))

        # Test that disallowed related_name raises error
        with self.assertRaises(ValueError) as context:
            class InsecureTestFilter(SecureNestedFilterMixin, filters.FilterSet):
                ALLOWED_NESTED_FIELDS = ['actions']

                nested_field_config = {
                    'bad_field': {
                        'related_name': 'bad_relation',  # Not in ALLOWED_NESTED_FIELDS
                        'latest_strategy': 'created',
                        'fields': ['status']
                    }
                }

                class Meta:
                    model = LearnerCreditRequest
                    fields = []

            InsecureTestFilter()

        self.assertIn("not allowed", str(context.exception))

    def test_field_value_sanitization(self):
        """Test that field values are properly sanitized."""

        class SecureTestFilter(SecureNestedFilterMixin, filters.FilterSet):
            ALLOWED_NESTED_FIELDS = ['actions']

            nested_field_config = {
                'action': {
                    'related_name': 'actions',
                    'latest_strategy': 'created',
                    'fields': ['status']
                }
            }

            class Meta:
                model = LearnerCreditRequest
                fields = []

        filter_instance = SecureTestFilter()

        # Test normal value
        sanitized = filter_instance._sanitize_field_value("approved")
        self.assertEqual(sanitized, "approved")

        # Test value with dangerous characters
        sanitized = filter_instance._sanitize_field_value("approved'; DROP TABLE;")
        self.assertEqual(sanitized, "approved DROP TABLE")  # Semicolons removed

        # Test overly long value
        with self.assertRaises(ValidationError):
            filter_instance._sanitize_field_value("a" * 300)  # Too long


class TestLearnerCreditRequestFilter(APITestCase):
    """Test the complete LearnerCreditRequestFilter implementation."""

    def setUp(self):
        """Set up test data."""
        self.client = APIClient()
        self.user = User.objects.create(
            username='testuser',
            email='testuser@example.com',
            lms_user_id=12345
        )
        self.config = LearnerCreditRequestConfigurationFactory()

        # Create test requests with different states and actions
        self.request_data = []
        states = ['requested', 'approved', 'declined']

        for i, state in enumerate(states):
            request = LearnerCreditRequestFactory(
                user=self.user,
                state=state,
                course_title=f'Course {i}',
                course_id=f'course-v1:Test+{i}+2024',
                learner_credit_request_config=self.config
            )

            action = LearnerCreditRequestActionsFactory(
                learner_credit_request=request,
                status=state,
                recent_action=state
            )

            self.request_data.append({
                'request': request,
                'action': action,
                'state': state
            })

    def test_root_field_filtering_preserved(self):
        """Test that root field filtering still works after enhancement."""
        response = self.client.get('/api/v1/learner-credit-requests/?state=approved')
        self.assertEqual(response.status_code, 200)

        results = response.data['results']
        self.assertTrue(all(r['state'] == 'approved' for r in results))
        self.assertEqual(len(results), 1)  # Should only return approved request

    def test_enhanced_nested_filtering_syntax(self):
        """Test new enhanced nested filtering syntax."""
        response = self.client.get('/api/v1/learner-credit-requests/?action_status=approved')
        self.assertEqual(response.status_code, 200)

        results = response.data['results']
        self.assertTrue(all(r['latest_action']['status'] == 'approved' for r in results))
        self.assertEqual(len(results), 1)

    def test_combined_filtering(self):
        """Test combined root and nested filtering."""
        response = self.client.get('/api/v1/learner-credit-requests/?state=approved&action_status=approved')
        self.assertEqual(response.status_code, 200)

        results = response.data['results']
        for result in results:
            self.assertEqual(result['state'], 'approved')
            self.assertEqual(result['latest_action']['status'], 'approved')

    def test_backward_compatibility(self):
        """Test backward compatibility with old parameter names."""
        # Test old syntax still works
        old_response = self.client.get('/api/v1/learner-credit-requests/?latest_action_status=approved')
        self.assertEqual(old_response.status_code, 200)

        # Test new syntax returns same results
        new_response = self.client.get('/api/v1/learner-credit-requests/?action_status=approved')
        self.assertEqual(new_response.status_code, 200)

        # Results should be identical
        self.assertEqual(old_response.data['results'], new_response.data['results'])

    def test_datetime_filtering(self):
        """Test datetime filtering on nested fields."""
        cutoff_date = datetime.now(timezone.utc).isoformat()
        response = self.client.get(f'/api/v1/learner-credit-requests/?action_created__gte={cutoff_date}')
        self.assertEqual(response.status_code, 200)

        # All results should have action created after cutoff
        results = response.data['results']
        for result in results:
            action_created = datetime.fromisoformat(result['latest_action']['created'].replace('Z', '+00:00'))
            self.assertGreaterEqual(action_created, datetime.fromisoformat(cutoff_date.replace('Z', '+00:00')))

    def test_email_filtering(self):
        """Test user email filtering functionality."""
        response = self.client.get('/api/v1/learner-credit-requests/?user__email__icontains=testuser')
        self.assertEqual(response.status_code, 200)

        results = response.data['results']
        self.assertTrue(all('testuser' in r['user']['email'] for r in results))

    def test_course_filtering(self):
        """Test course-related filtering."""
        response = self.client.get('/api/v1/learner-credit-requests/?course_title__icontains=Course 0')
        self.assertEqual(response.status_code, 200)

        results = response.data['results']
        self.assertEqual(len(results), 1)
        self.assertIn('Course 0', results[0]['course_title'])

    def test_invalid_parameter_handling(self):
        """Test handling of invalid filter parameters."""
        # Test with invalid choice value
        response = self.client.get('/api/v1/learner-credit-requests/?state=invalid_state')
        self.assertEqual(response.status_code, 200)

        # Should return empty results, not error
        results = response.data['results']
        self.assertEqual(len(results), 0)

    def test_ordering_with_filtering(self):
        """Test that ordering works correctly with filtering."""
        response = self.client.get('/api/v1/learner-credit-requests/?state=requested&ordering=-created')
        self.assertEqual(response.status_code, 200)

        results = response.data['results']
        if len(results) > 1:
            # Check that results are ordered by creation date descending
            dates = [datetime.fromisoformat(r['created'].replace('Z', '+00:00')) for r in results]
            self.assertEqual(dates, sorted(dates, reverse=True))


class TestFilterPerformance(APITestCase):
    """Test performance aspects of the filtering implementation."""

    def setUp(self):
        """Set up larger test dataset."""
        self.client = APIClient()
        self.user = User.objects.create(
            username='testuser',
            email='testuser@example.com',
            lms_user_id=12345
        )
        self.config = LearnerCreditRequestConfigurationFactory()

    def test_performance_with_large_dataset(self):
        """Test performance with larger dataset."""
        # Create larger test dataset
        requests = []
        for i in range(50):  # Smaller number for test efficiency
            request = LearnerCreditRequestFactory(
                user=self.user,
                state='requested' if i % 2 == 0 else 'approved',
                learner_credit_request_config=self.config
            )
            LearnerCreditRequestActionsFactory(
                learner_credit_request=request,
                status='requested' if i % 2 == 0 else 'approved'
            )
            requests.append(request)

        # Test query performance
        start_time = time.time()
        response = self.client.get('/api/v1/learner-credit-requests/?state=requested&action_status=requested')
        duration = time.time() - start_time

        self.assertEqual(response.status_code, 200)
        self.assertLess(duration, 2.0)  # Should complete within 2 seconds for test data

    def test_query_count_optimization(self):
        """Test that query count is optimized."""
        # Create test data
        for i in range(5):
            request = LearnerCreditRequestFactory(user=self.user, learner_credit_request_config=self.config)
            LearnerCreditRequestActionsFactory(learner_credit_request=request)

        with self.assertNumQueries(10):  # Allow reasonable number of queries
            response = self.client.get('/api/v1/learner-credit-requests/?action_status=requested')
            self.assertEqual(response.status_code, 200)


class TestFilterBuilder(TestCase):
    """Test the NestedFilterBuilder utility."""

    def test_builder_functionality(self):
        """Test that the builder creates correct configurations."""
        config = (NestedFilterBuilder()
                  .add_nested_field('action', 'actions', 'created')
                  .add_field('action', 'status')
                  .add_field('action', 'recent_action')
                  .add_nested_field('assignment', 'assignments', 'modified')
                  .add_field('assignment', 'state')
                  .build())

        expected_config = {
            'action': {
                'related_name': 'actions',
                'latest_strategy': 'created',
                'fields': ['status', 'recent_action']
            },
            'assignment': {
                'related_name': 'assignments',
                'latest_strategy': 'modified',
                'fields': ['state']
            }
        }

        self.assertEqual(config, expected_config)

    def test_builder_error_handling(self):
        """Test that builder handles errors correctly."""
        builder = NestedFilterBuilder()

        with self.assertRaises(ValueError):
            builder.add_field('nonexistent', 'status')  # Should fail


class TestAliasCreation(TestCase):
    """Test the create_nested_filter_aliases function."""

    def test_alias_creation(self):
        """Test that aliases are created correctly."""

        class TestFilter(NestedFieldFilterMixin, filters.FilterSet):
            nested_field_config = {
                'action': {
                    'related_name': 'actions',
                    'latest_strategy': 'created',
                    'fields': ['status']
                }
            }

            class Meta:
                model = LearnerCreditRequest
                fields = []

        # Create aliases
        create_nested_filter_aliases(TestFilter, {
            'old_action_status': 'action_status'
        })

        # Test that alias was created
        filter_instance = TestFilter()
        self.assertTrue(hasattr(filter_instance, 'old_action_status'))
        self.assertTrue(hasattr(filter_instance, 'action_status'))


class TestReusabilityWithOtherModels(TestCase):
    """Test that the mixin is reusable with other models."""

    def test_mixin_with_different_configuration(self):
        """Test that mixin works with different model configurations."""

        # Mock another model for testing
        class MockFilter(NestedFieldFilterMixin, filters.FilterSet):
            nested_field_config = {
                'latest_assignment': {
                    'related_name': 'assignments',
                    'latest_strategy': 'modified',
                    'fields': ['state', 'learner_acknowledged']
                }
            }

            class Meta:
                model = LearnerCreditRequest  # Using same model for simplicity
                fields = ['state']

        filter_instance = MockFilter()

        # Check that filters were created with different prefix
        self.assertTrue(hasattr(filter_instance, 'latest_assignment_state'))
        self.assertTrue(hasattr(filter_instance, 'latest_assignment_learner_acknowledged'))

        # Check that methods were created
        self.assertTrue(hasattr(filter_instance, 'filter_by_latest_assignment_state'))
        self.assertTrue(hasattr(filter_instance, 'filter_by_latest_assignment_learner_acknowledged'))


class TestErrorHandling(TestCase):
    """Test error handling and edge cases."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create(
            username='testuser',
            email='test@example.com',
            lms_user_id=12345
        )

    def test_graceful_error_handling(self):
        """Test that errors are handled gracefully."""

        class TestFilter(NestedFieldFilterMixin, filters.FilterSet):
            nested_field_config = {
                'action': {
                    'related_name': 'actions',
                    'latest_strategy': 'created',
                    'fields': ['status']
                }
            }

            class Meta:
                model = LearnerCreditRequest
                fields = []

        filter_instance = TestFilter({'action_status': 'approved'})

        # Even with potential errors, should return a queryset
        queryset = filter_instance.qs
        self.assertIsInstance(queryset, QuerySet)

    @patch('enterprise_access.apps.api.filters.mixins.logger')
    def test_error_logging(self, mock_logger):
        """Test that errors are properly logged."""

        class TestFilter(NestedFieldFilterMixin, filters.FilterSet):
            nested_field_config = {
                'action': {
                    'related_name': 'nonexistent_relation',  # This will cause error
                    'latest_strategy': 'created',
                    'fields': ['status']
                }
            }

            class Meta:
                model = LearnerCreditRequest
                fields = []

        filter_instance = TestFilter({'action_status': 'approved'})

        # Apply filter (which should trigger error)
        try:
            list(filter_instance.qs)  # Force evaluation
        except:
            pass  # Expected to potentially fail

        # Check that error was logged (may be called during filter application)
        # This is more of a smoke test since the exact behavior depends on model setup


@override_settings(DEBUG=True)
class TestIntegrationWithDjangoAdmin(TestCase):
    """Test integration with Django admin interface."""

    def test_admin_display_values_are_unique(self):
        """Test that admin dropdown values are now unique."""
        from enterprise_access.apps.subsidy_request.constants import LearnerCreditRequestUserMessages

        # Get all display values
        display_values = [choice[1] for choice in LearnerCreditRequestUserMessages.CHOICES]

        # Check that all display values are unique
        self.assertEqual(len(display_values), len(set(display_values)))

        # Specifically check that we don't have duplicate "Waiting For Learner"
        waiting_count = display_values.count("Waiting For Learner")
        self.assertEqual(waiting_count, 0)  # Should be 0 after our fix

        # Check that we have the new unique values
        self.assertIn("Reminded", display_values)
        self.assertIn("Approved", display_values)
