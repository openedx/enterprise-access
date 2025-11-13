"""
Tests for the CheckoutIntent viewset.
"""
import uuid
from datetime import timedelta

import ddt
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.reverse import reverse

from enterprise_access.apps.core.constants import (
    ALL_ACCESS_CONTEXT,
    SYSTEM_ENTERPRISE_LEARNER_ROLE,
    SYSTEM_ENTERPRISE_OPERATOR_ROLE,
    SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE
)
from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.constants import CheckoutIntentState
from enterprise_access.apps.customer_billing.models import CheckoutIntent
from test_utils import APITest

User = get_user_model()


@ddt.ddt
class CheckoutIntentViewSetTestCase(APITest):
    """
    Test cases for CheckoutIntent ViewSet.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user_2 = User.objects.create_user(
            username='testuser2',
            email='test2@example.com',
            password='testpass123'
        )
        cls.user_3 = User.objects.create_user(
            username='testuser3',
            email='test3@example.com',
            password='testpass123'
        )
        cls.user_4 = User.objects.create_user(
            username='testuser4',
            email='test4@example.com',
            password='testpass123'
        )
        cls.checkout_intent_2 = CheckoutIntent.objects.create(
            user=cls.user_2,
            enterprise_name="Active Enterprise 2",
            enterprise_slug="active-enterprise-2",
            state=CheckoutIntentState.PAID,
            quantity=25,
            expires_at=timezone.now() + timedelta(minutes=30),
            stripe_checkout_session_id='cs_test_456',
            country='US',
            terms_metadata={'version': '1.0', 'accepted_at': '2024-01-15T10:30:00Z'}
        )
        cls.checkout_intent_4 = CheckoutIntent.objects.create(
            user=cls.user_4,
            enterprise_name="Active Enterprise 4",
            enterprise_slug="active-enterprise-4",
            state=CheckoutIntentState.ERRORED_BACKOFFICE,
            quantity=25,
            expires_at=timezone.now() + timedelta(minutes=30),
            stripe_checkout_session_id='cs_test_987',
            country='US',
            terms_metadata={'version': '1.0', 'accepted_at': '2024-01-15T10:30:00Z'}
        )

    def setUp(self):
        """Set up test data."""
        super().setUp()

        self.checkout_intent_1 = CheckoutIntent.objects.create(
            user=self.user,
            enterprise_name="Active Enterprise",
            enterprise_slug="active-enterprise",
            state=CheckoutIntentState.CREATED,
            quantity=15,
            expires_at=timezone.now() + timedelta(minutes=30),
            stripe_checkout_session_id='cs_test_123',
            country='CA',
            terms_metadata={'version': '1.1', 'test_mode': True}
        )
        self.checkout_intent_3 = CheckoutIntent.objects.create(
            user=self.user_3,
            enterprise_name="Active Enterprise 3",
            enterprise_slug="active-enterprise-3",
            state=CheckoutIntentState.CREATED,
            quantity=27,
            expires_at=timezone.now() + timedelta(minutes=30),
            stripe_checkout_session_id='cs_test_789',
            country='GB',
            terms_metadata={'version': '2.0', 'features': ['analytics', 'reporting']}
        )

        # URL patterns
        self.list_url = reverse('api:v1:checkout-intent-list')
        self.detail_url_1 = reverse(
            'api:v1:checkout-intent-detail',
            kwargs={'id': self.checkout_intent_1.id}
        )
        self.detail_url_3 = reverse(
            'api:v1:checkout-intent-detail',
            kwargs={'id': self.checkout_intent_3.id}
        )
        # URLs for testing UUID lookup
        self.detail_url_by_uuid_1 = reverse(
            'api:v1:checkout-intent-detail',
            kwargs={'id': str(self.checkout_intent_1.uuid)}
        )

    def test_authentication_required(self):
        """Test that all endpoints require authentication."""
        # Test list endpoint
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # Test retrieve endpoint
        response = self.client.get(self.detail_url_1)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # Test patch endpoint
        response = self.client.patch(
            self.detail_url_1,
            {'state': 'paid'}
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_only_returns_users_own_records(self):
        """Test that list endpoint only returns authenticated user's records."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        ids = [item['id'] for item in response.data['results']]
        self.assertEqual([self.checkout_intent_1.id], ids)

    def test_retrieve_own_record(self):
        """Test that users can retrieve their own records."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        response = self.client.get(self.detail_url_1)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], self.checkout_intent_1.id)
        self.assertEqual(response.data['state'], 'created')

    def test_cannot_retrieve_other_users_record(self):
        """Test that users cannot retrieve other users' records."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        # Try to access user_3's checkout intent
        response = self.client.get(self.detail_url_3)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @ddt.data(
        {'current_state': CheckoutIntentState.CREATED, 'new_state': CheckoutIntentState.PAID},
        {'current_state': CheckoutIntentState.CREATED, 'new_state': CheckoutIntentState.EXPIRED},
        {'current_state': CheckoutIntentState.PAID, 'new_state': CheckoutIntentState.FULFILLED},
        {'current_state': CheckoutIntentState.PAID, 'new_state': CheckoutIntentState.ERRORED_BACKOFFICE},
        {'current_state': CheckoutIntentState.PAID, 'new_state': CheckoutIntentState.ERRORED_FULFILLMENT_STALLED},
        {'current_state': CheckoutIntentState.PAID, 'new_state': CheckoutIntentState.ERRORED_PROVISIONING},
        {'current_state': CheckoutIntentState.ERRORED_PROVISIONING, 'new_state': CheckoutIntentState.FULFILLED},
        {'current_state': CheckoutIntentState.EXPIRED, 'new_state': CheckoutIntentState.CREATED},
    )
    @ddt.unpack
    def test_valid_state_transitions(self, current_state, new_state):
        """Test valid state transitions."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])
        self.checkout_intent_1.state = current_state
        self.checkout_intent_1.save()

        response = self.client.patch(
            self.detail_url_1,
            {'state': new_state},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['state'], new_state)

        # Verify in database
        self.checkout_intent_1.refresh_from_db()
        self.assertEqual(self.checkout_intent_1.state, new_state)

    def test_invalid_state_transition(self):
        """Test that invalid state transitions are rejected."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        # Try invalid transition: created -> fulfilled
        response = self.client.patch(
            self.detail_url_1,
            {'state': 'fulfilled'},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response_data = response.json()
        self.assertIn('state', response_data)
        self.assertIn('Invalid state transition', str(response_data['state']))

        # Verify state hasn't changed in database
        self.checkout_intent_1.refresh_from_db()
        self.assertEqual(self.checkout_intent_1.state, 'created')

    def test_error_recovery_transition(self):
        """Test error state recovery transitions."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        # First transition to paid state
        response = self.client.patch(
            self.detail_url_1,
            {'state': 'paid'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Then transition to error state
        response = self.client.patch(
            self.detail_url_1,
            {'state': 'errored_provisioning'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Then recover to fulfilled
        response = self.client.patch(
            self.detail_url_1,
            {'state': 'fulfilled'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['state'], 'fulfilled')

    def test_cannot_transition_from_fulfilled(self):
        """Test that fulfilled is a terminal state."""
        other_user = UserFactory()
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }], user=other_user)

        # Create a fulfilled checkout intent
        fulfilled_intent = CheckoutIntent.objects.create(
            user=other_user,
            enterprise_name="Active Enterprise 5",
            enterprise_slug="active-enterprise-5",
            state=CheckoutIntentState.FULFILLED,
            quantity=27,
            expires_at=timezone.now() + timedelta(minutes=30),
            stripe_checkout_session_id='cs_test_78955',
            country='FR',
            terms_metadata={'version': '1.5', 'fulfilled': True}
        )

        detail_url = reverse(
            'api:v1:checkout-intent-detail',
            kwargs={'id': fulfilled_intent.id}
        )

        # Try to transition from fulfilled to any state
        response = self.client.patch(
            detail_url,
            {'state': 'paid'},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_read_only_fields_cannot_be_updated(self):
        """Test that read-only fields cannot be modified."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        original_id = self.checkout_intent_1.id
        original_session_id = self.checkout_intent_1.stripe_checkout_session_id

        response = self.client.patch(
            self.detail_url_1,
            {
                'id': 77,
                'stripe_checkout_session_id': 'new_session_id',
                'user': self.user_2.id
            },
            format='json'
        )

        # Request should succeed but read-only fields shouldn't change
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.checkout_intent_1.refresh_from_db()
        self.assertEqual(self.checkout_intent_1.id, original_id)
        self.assertEqual(
            self.checkout_intent_1.stripe_checkout_session_id,
            original_session_id
        )
        self.assertEqual(self.checkout_intent_1.user, self.user)

    def test_delete_method_not_allowed(self):
        """Test that DELETE method is not allowed."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        response = self.client.delete(self.detail_url_1)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_create_checkout_intent_success(self):
        """Test successful creation of checkout intent."""
        other_user = UserFactory()
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }], user=other_user)

        request_data = {
            'enterprise_slug': 'test-enterprise-post',
            'enterprise_name': 'Test Enterprise post',
            'quantity': 13,
            'country': 'NZ',
            'terms_metadata': {'version': '1.0', 'accepted_at': '2024-01-15T10:30:00Z'}
        }

        response = self.client.post(
            self.list_url,
            request_data,
        )
        response_data = response.json()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response_data['user'], other_user.id)
        self.assertEqual(response_data['enterprise_slug'], 'test-enterprise-post')
        self.assertEqual(response_data['enterprise_name'], 'Test Enterprise post')
        self.assertEqual(response_data['quantity'], 13)
        self.assertEqual(response_data['state'], CheckoutIntentState.CREATED)
        self.assertEqual(response_data['country'], 'NZ')
        self.assertEqual(response_data['terms_metadata'], {'version': '1.0', 'accepted_at': '2024-01-15T10:30:00Z'})

    def test_create_or_update_checkout_intent_success(self):
        """Test successful update of checkout intent, even if it happens through a POST."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        request_data = {
            'enterprise_slug': self.checkout_intent_1.enterprise_slug,
            'enterprise_name': self.checkout_intent_1.enterprise_name,
            'quantity': 33,
            'country': 'IT',
            'terms_metadata': {'version': '2.0', 'updated': True}
        }

        response = self.client.post(
            self.list_url,
            request_data,
        )
        response_data = response.json()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response_data['user'], self.user.id)
        self.assertEqual(response_data['enterprise_slug'], self.checkout_intent_1.enterprise_slug)
        self.assertEqual(response_data['enterprise_name'], self.checkout_intent_1.enterprise_name)
        self.assertEqual(response_data['quantity'], 33)
        self.assertEqual(response_data['state'], CheckoutIntentState.CREATED)
        self.assertEqual(response_data['country'], 'IT')
        self.assertEqual(response_data['terms_metadata'], {'version': '2.0', 'test_mode': True, 'updated': True})
        self.checkout_intent_1.refresh_from_db()
        self.assertEqual(self.checkout_intent_1.quantity, 33)
        self.assertEqual(self.checkout_intent_1.country, 'IT')
        self.assertEqual(self.checkout_intent_1.terms_metadata, {'version': '2.0', 'test_mode': True, 'updated': True})

    @ddt.data(
        # Invalid quantity cases:
        {'quantity': -1, 'enterprise_slug': 'valid', 'enterprise_name': 'Valid'},
        {'quantity': 0, 'enterprise_slug': 'valid', 'enterprise_name': 'Valid'},
        {'quantity': 'invalid', 'enterprise_slug': 'valid', 'enterprise_name': 'Valid'},
        # Missing slug/name when the other is provided.
        {'quantity': 10, 'enterprise_slug': '', 'enterprise_name': 'Valid'},
        {'quantity': 10, 'enterprise_name': 'Valid'},
        {'quantity': 10, 'enterprise_slug': 'valid', 'enterprise_name': ''},
        {'quantity': 10, 'enterprise_slug': 'valid'},
    )
    @ddt.unpack
    def test_create_checkout_intent_invalid_field_values(self, **invalid_payload):
        """Test creation fails with invalid field values."""
        other_user = UserFactory()
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }], user=other_user)

        response = self.client.post(
            self.list_url,
            invalid_payload,
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @ddt.data(
        {},  # Missing quantity.
        {'enterprise_slug': 'hello', 'quantity': 10},  # Missing enterprise_name.
        {'enterprise_name': 'Hello', 'quantity': 10},  # Missing enterprise_slug.
    )
    @ddt.unpack
    def test_create_checkout_intent_missing_required_fields(self, **payload):
        """Test creation fails when required fields are missing."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        # Test missing enterprise_slug
        response = self.client.post(
            self.list_url,
            payload,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        error_detail = list(response.json().values())[0][0]
        self.assertIn('required', error_detail)

    def test_create_checkout_intent_authentication_required(self):
        """Test that creation endpoint requires authentication."""
        response = self.client.post(
            self.list_url,
            {
                'enterprise_slug': 'test-enterprise',
                'enterprise_name': 'Test Enterprise',
                'quantity': 10,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_update_terms_metadata_and_country(self):
        """Test updating terms_metadata and country via PATCH."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        new_terms = {'version': '3.0', 'updated_via_patch': True, 'features': ['new_feature']}
        response = self.client.patch(
            self.detail_url_1,
            {
                'terms_metadata': new_terms,
                'country': 'AU'
            },
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['terms_metadata'], new_terms)
        self.assertEqual(response.data['country'], 'AU')

        # Verify in database
        self.checkout_intent_1.refresh_from_db()
        self.assertEqual(self.checkout_intent_1.terms_metadata, new_terms)
        self.assertEqual(self.checkout_intent_1.country, 'AU')

    @ddt.data(
        # Test that strings are rejected
        {'terms_metadata': 'invalid_string'},
        # Test that lists are rejected
        {'terms_metadata': ['invalid', 'list']},
        # Test that numbers are rejected
        {'terms_metadata': 123},
        # Test that booleans are rejected
        {'terms_metadata': True},
    )
    @ddt.unpack
    def test_invalid_terms_metadata_types_rejected(self, **invalid_data):
        """Test that non-dictionary types for terms_metadata are rejected."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        response = self.client.patch(
            self.detail_url_1,
            invalid_data,
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('terms_metadata', response.data)
        self.assertIn('must be a dictionary/object', str(response.data['terms_metadata']))

    def test_create_with_null_terms_metadata(self):
        """Test creating with null terms_metadata works."""
        other_user = UserFactory()
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }], user=other_user)

        request_data = {
            'enterprise_slug': 'test-enterprise-null',
            'enterprise_name': 'Test Enterprise Null',
            'quantity': 5,
            'terms_metadata': None
        }

        response = self.client.post(
            self.list_url,
            request_data,
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data['terms_metadata'])

    def test_create_with_empty_terms_metadata(self):
        """Test creating with empty dict terms_metadata works."""
        other_user = UserFactory()
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }], user=other_user)

        request_data = {
            'enterprise_slug': 'test-enterprise-empty',
            'enterprise_name': 'Test Enterprise Empty',
            'quantity': 8,
            'terms_metadata': {}
        }

        response = self.client.post(
            self.list_url,
            request_data,
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['terms_metadata'], {})

    def test_create_checkout_intent_without_slug_or_name_success(self):
        """
        Test that trying to create a checkout intent without an enterprise name/slug is allowed.
        """
        other_user = UserFactory()
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }], user=other_user)

        request_data = {
            'quantity': 13,
            'country': 'NZ',
            'terms_metadata': {'version': '1.0', 'accepted_at': '2024-01-15T10:30:00Z'}
        }

        response = self.client.post(
            self.list_url,
            request_data,
        )
        response_data = response.json()

        assert response.status_code == status.HTTP_201_CREATED
        assert response_data['user'] == other_user.id
        assert response_data['enterprise_slug'] is None
        assert response_data['enterprise_name'] is None
        assert response_data['quantity'] == 13
        assert response_data['state'] == CheckoutIntentState.CREATED
        assert response_data['country'] == 'NZ'
        assert response_data['terms_metadata'] == {'version': '1.0', 'accepted_at': '2024-01-15T10:30:00Z'}

    def test_create_checkout_intent_already_failed_returns_422(self):
        """
        Test that trying to reserve a new slug when the current user already has a failed intent returns HTTP 422.
        """
        self.set_jwt_cookie(
            [{
                'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
                'context': str(uuid.uuid4()),
            }],
            # Auth as a user which already has a failed (ERRORED_STRIPE_CHECKOUT) CheckoutIntent.
            user=self.user_4,
        )

        # No matter the request, if the existing slug is in a failed state, it should return a 422 error.
        request_data = {
            'enterprise_slug': 'new-slug',
            'enterprise_name': 'New Name',
            'quantity': 7,
        }
        response = self.client.post(self.list_url, request_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        assert 'already has a failed' in response.json()['detail']

    def test_create_checkout_intent_slug_conflict_returns_422(self):
        """
        Test that trying to reserve a slug that has already been reserved returns HTTP 422.
        """
        # Auth as a brand new user.
        other_user = UserFactory()
        self.set_jwt_cookie(
            [{
                'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
                'context': str(uuid.uuid4()),
            }],
            user=other_user,
        )

        # Attempt to reserve a slug that user 1 has already reserved.
        request_data = {
            'enterprise_slug': 'active-enterprise',
            'enterprise_name': 'Active Enterprise',
            'quantity': 7,
        }
        response = self.client.post(self.list_url, request_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        assert 'has already been reserved' in response.json()['detail']

    @ddt.data('RU', 'IR', 'KP', 'SY', 'CU')
    def test_patch_embargoed_country_rejected(self, embargoed_country_code):
        """Test that PATCH with embargoed countries is rejected."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        response = self.client.patch(
            self.detail_url_1,
            {'country': embargoed_country_code},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('country', response.data)
        self.assertIn('not supported', str(response.data['country'][0]))
        self.assertIn(embargoed_country_code, str(response.data['country'][0]))

        # Verify country hasn't changed in database
        self.checkout_intent_1.refresh_from_db()
        self.assertEqual(self.checkout_intent_1.country, 'CA')

    def test_patch_non_embargoed_country_succeeds(self):
        """Test that PATCH with non-embargoed countries succeeds."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        response = self.client.patch(
            self.detail_url_1,
            {'country': 'DE'},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['country'], 'DE')

        # Verify in database
        self.checkout_intent_1.refresh_from_db()
        self.assertEqual(self.checkout_intent_1.country, 'DE')

    def test_list_with_read_write_all_permission_returns_all_records(self):
        """Test that users with CHECKOUT_INTENT_READ_WRITE_ALL permission can see all checkout intents."""
        # Set JWT with customer billing operator role to get the read/write all permission
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE,
            'context': ALL_ACCESS_CONTEXT,
        }])

        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Should see all checkout intents from all users, 4 total
        ids = [item['id'] for item in response.data['results']]
        expected_ids = [
            self.checkout_intent_1.id,  # from self.user
            self.checkout_intent_2.id,  # from user_2 (class-level)
            self.checkout_intent_3.id,  # from user_3
            self.checkout_intent_4.id,  # from user_4 (class-level)
        ]
        self.assertEqual(len(ids), 4)
        self.assertEqual(set(ids), set(expected_ids))

    def test_list_without_permission_returns_only_user_records(self):
        """Test that users without special permissions only see their own checkout intents."""
        # This test duplicates test_list_only_returns_users_own_records but is included
        # for completeness to contrast with the permission-based test above
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        ids = [item['id'] for item in response.data['results']]
        self.assertEqual([self.checkout_intent_1.id], ids)

    def test_retrieve_other_users_record_with_read_write_all_permission(self):
        """Test that users with CHECKOUT_INTENT_READ_WRITE_ALL permission can retrieve other users' records."""
        # Set JWT with customer billing operator role to get the read/write all permission
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_OPERATOR_ROLE,
            'context': ALL_ACCESS_CONTEXT,
        }])

        # Try to access user_3's checkout intent (should succeed with permission)
        response = self.client.get(self.detail_url_3)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], self.checkout_intent_3.id)
        self.assertEqual(response.data['state'], 'created')

        # Also try accessing a class-level checkout intent from user_2
        detail_url_2 = reverse(
            'api:v1:checkout-intent-detail',
            kwargs={'id': self.checkout_intent_2.id}
        )
        response = self.client.get(detail_url_2)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], self.checkout_intent_2.id)
        self.assertEqual(response.data['state'], 'paid')

    def test_retrieve_by_uuid(self):
        """Test that users can retrieve their own records using UUID."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        response = self.client.get(self.detail_url_by_uuid_1)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], self.checkout_intent_1.id)
        self.assertEqual(response.data['uuid'], str(self.checkout_intent_1.uuid))

    def test_retrieve_by_invalid_lookup(self):
        """Test that invalid lookup values return appropriate error."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        invalid_url = reverse(
            'api:v1:checkout-intent-detail',
            kwargs={'id': 'invalid-lookup-value'}
        )
        response = self.client.get(invalid_url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Lookup value must be either a valid UUID or integer ID', str(response.data))

    def test_update_by_uuid(self):
        """Test that users can update their own records using UUID."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        response = self.client.patch(
            self.detail_url_by_uuid_1,
            {'state': 'paid'},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['state'], 'paid')

        # Verify in database
        self.checkout_intent_1.refresh_from_db()
        self.assertEqual(self.checkout_intent_1.state, 'paid')

    def test_cannot_retrieve_other_users_record_by_uuid(self):
        """Test that users cannot retrieve other users' records using UUID."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        other_uuid_url = reverse(
            'api:v1:checkout-intent-detail',
            kwargs={'id': str(self.checkout_intent_3.uuid)}
        )
        response = self.client.get(other_uuid_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_retrieve_nonexistent_uuid(self):
        """Test that retrieving with nonexistent UUID returns 404."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        nonexistent_uuid_url = reverse(
            'api:v1:checkout-intent-detail',
            kwargs={'id': str(uuid.uuid4())}  # Random UUID that doesn't exist
        )
        response = self.client.get(nonexistent_uuid_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
