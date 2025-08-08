"""
Tests for the CheckoutIntent viewset.
"""
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.reverse import reverse

from enterprise_access.apps.core.constants import SYSTEM_ENTERPRISE_LEARNER_ROLE
from enterprise_access.apps.core.tests.factories import UserFactory
from enterprise_access.apps.customer_billing.constants import CheckoutIntentState
from enterprise_access.apps.customer_billing.models import CheckoutIntent
from test_utils import APITest

User = get_user_model()


class CheckoutIntentViewSetTestCase(APITest):
    """
    Test cases for CheckoutIntent ViewSet.
    """

    def setUp(self):
        """Set up test data."""
        super().setUp()

        self.user_2 = User.objects.create_user(
            username='testuser2',
            email='test2@example.com',
            password='testpass123'
        )
        self.user_3 = User.objects.create_user(
            username='testuser3',
            email='test3@example.com',
            password='testpass123'
        )

        self.checkout_intent_1 = CheckoutIntent.objects.create(
            user=self.user,
            enterprise_name="Active Enterprise",
            enterprise_slug="active-enterprise",
            state=CheckoutIntentState.CREATED,
            quantity=15,
            expires_at=timezone.now() + timedelta(minutes=30),
            stripe_checkout_session_id='cs_test_123',
        )
        self.checkout_intent_2 = CheckoutIntent.objects.create(
            user=self.user_2,
            enterprise_name="Active Enterprise 2",
            enterprise_slug="active-enterprise-2",
            state=CheckoutIntentState.PAID,
            quantity=25,
            expires_at=timezone.now() + timedelta(minutes=30),
            stripe_checkout_session_id='cs_test_456',
        )
        self.checkout_intent_3 = CheckoutIntent.objects.create(
            user=self.user_3,
            enterprise_name="Active Enterprise 3",
            enterprise_slug="active-enterprise-3",
            state=CheckoutIntentState.CREATED,
            quantity=27,
            expires_at=timezone.now() + timedelta(minutes=30),
            stripe_checkout_session_id='cs_test_789',
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

    def test_valid_state_transition_created_to_paid(self):
        """Test valid state transition from created to paid."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        response = self.client.patch(
            self.detail_url_1,
            {'state': 'paid'},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['state'], 'paid')

        # Verify in database
        self.checkout_intent_1.refresh_from_db()
        self.assertEqual(self.checkout_intent_1.state, 'paid')

    def test_valid_state_transition_created_to_errored_stripe(self):
        """Test valid state transition from created to errored_stripe_checkout."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        response = self.client.patch(
            self.detail_url_1,
            {
                'state': 'errored_stripe_checkout',
            },
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['state'], 'errored_stripe_checkout')

    def test_valid_state_transition_paid_to_fulfilled(self):
        """Test valid state transition from paid to fulfilled."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }], user=self.user_2)

        # checkout_intent_2 is already in 'paid' state
        detail_url_2 = reverse(
            'api:v1:checkout-intent-detail',
            kwargs={'id': self.checkout_intent_2.id}
        )

        response = self.client.patch(
            detail_url_2,
            {'state': 'fulfilled'},
            format='json'
        )

        # self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['state'], 'fulfilled')

    def test_valid_state_transition_expired_to_created(self):
        """Test valid state transition from paid to fulfilled."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }], user=self.user_2)

        self.checkout_intent_2.state = CheckoutIntentState.EXPIRED
        self.checkout_intent_2.save()

        detail_url_2 = reverse(
            'api:v1:checkout-intent-detail',
            kwargs={'id': self.checkout_intent_2.id}
        )

        response = self.client.patch(
            detail_url_2,
            {'state': CheckoutIntentState.CREATED},
            format='json'
        )

        # self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['state'], CheckoutIntentState.CREATED)
        self.checkout_intent_2.refresh_from_db()
        self.assertEqual(self.checkout_intent_2.state, CheckoutIntentState.CREATED)

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
        self.assertIn('state', response.data)
        self.assertIn('Invalid state transition', response.data['state'])

        # Verify state hasn't changed in database
        self.checkout_intent_1.refresh_from_db()
        self.assertEqual(self.checkout_intent_1.state, 'created')

    def test_error_recovery_transition(self):
        """Test error state recovery transitions."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        # First transition to error state
        response = self.client.patch(
            self.detail_url_1,
            {'state': 'errored_stripe_checkout'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Then recover to paid
        response = self.client.patch(
            self.detail_url_1,
            {'state': 'paid'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['state'], 'paid')

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

    def test_post_method_not_allowed(self):
        """Test that POST method is not allowed."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        response = self.client.post(
            self.list_url,
            {
                'state': 'created',
                'stripe_checkout_session_id': 'cs_test_new',
            },
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_delete_method_not_allowed(self):
        """Test that DELETE method is not allowed."""
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }])

        response = self.client.delete(self.detail_url_1)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
