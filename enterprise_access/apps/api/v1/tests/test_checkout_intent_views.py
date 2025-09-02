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

from enterprise_access.apps.core.constants import SYSTEM_ENTERPRISE_LEARNER_ROLE
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
        cls.checkout_intent_2 = CheckoutIntent.objects.create(
            user=cls.user_2,
            enterprise_name="Active Enterprise 2",
            enterprise_slug="active-enterprise-2",
            state=CheckoutIntentState.PAID,
            quantity=25,
            expires_at=timezone.now() + timedelta(minutes=30),
            stripe_checkout_session_id='cs_test_456',
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

    @ddt.data(
        {'current_state': CheckoutIntentState.CREATED, 'new_state': CheckoutIntentState.PAID},
        {'current_state': CheckoutIntentState.CREATED, 'new_state': CheckoutIntentState.ERRORED_STRIPE_CHECKOUT},
        {'current_state': CheckoutIntentState.CREATED, 'new_state': CheckoutIntentState.EXPIRED},
        {'current_state': CheckoutIntentState.PAID, 'new_state': CheckoutIntentState.FULFILLED},
        {'current_state': CheckoutIntentState.PAID, 'new_state': CheckoutIntentState.ERRORED_PROVISIONING},
        {'current_state': CheckoutIntentState.ERRORED_STRIPE_CHECKOUT, 'new_state': CheckoutIntentState.PAID},
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
        self.checkout_intent_1.refresh_from_db()
        self.assertEqual(self.checkout_intent_1.quantity, 33)
        self.assertEqual(self.checkout_intent_1.country, 'IT')

    @ddt.data(
        {'quantity': -1},
        {'quantity': 0},
        {'quantity': 'invalid'},
        {'enterprise_slug': ''},
        {'enterprise_name': ''},
    )
    @ddt.unpack
    def test_create_checkout_intent_invalid_field_values(self, **invalid_field):
        """Test creation fails with invalid field values."""
        other_user = UserFactory()
        self.set_jwt_cookie([{
            'system_wide_role': SYSTEM_ENTERPRISE_LEARNER_ROLE,
            'context': str(uuid.uuid4()),
        }], user=other_user)

        request_data = {
            'enterprise_slug': 'test-enterprise',
            'enterprise_name': 'Test Enterprise',
            'quantity': 10,
        }
        request_data.update(invalid_field)

        response = self.client.post(
            self.list_url,
            request_data,
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # The field name should be in the error response
        invalid_field_name = list(invalid_field.keys())[0]
        self.assertIn(invalid_field_name, response.data)

    @ddt.data(
        {'enterprise_name': 'hello', 'quantity': 10},
        {'enterprise_slug': 'hello', 'quantity': 10},
        {'enterprise_name': 'hello', 'enterprise_slug': 'foo'},
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
