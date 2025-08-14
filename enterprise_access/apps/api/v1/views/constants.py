"""
Constants for api/v1/views.
"""
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, inline_serializer
from rest_framework import serializers

from enterprise_access.apps.customer_billing.constants import CheckoutIntentState

# Schema examples for documentation
CHECKOUT_INTENT_EXAMPLES = [
    OpenApiExample(
        'Created State Example',
        summary='CheckoutIntent in created state',
        description='A newly created checkout intent awaiting payment',
        value={
            'uuid': '123e4567-e89b-12d3-a456-426614174000',
            'user': 1,
            'state': CheckoutIntentState.CREATED,
            'stripe_checkout_session_id': 'cs_test_a1b2c3d4e5f6g7h8i9j0',
            'enterprise_customer_uuid': '987e6543-e21b-12d3-a456-426614174000',
            'created': '2025-01-15T10:30:00.000Z',
            'modified': '2025-01-15T10:30:00.000Z',
            'error_message': None,
            'metadata': {}
        },
        response_only=True,
    ),
    OpenApiExample(
        'Paid State Example',
        summary='CheckoutIntent in paid state',
        description='Payment confirmed, awaiting fulfillment',
        value={
            'uuid': '123e4567-e89b-12d3-a456-426614174001',
            'user': 1,
            'state': CheckoutIntentState.PAID,
            'stripe_checkout_session_id': 'cs_test_b2c3d4e5f6g7h8i9j0k1',
            'enterprise_customer_uuid': '987e6543-e21b-12d3-a456-426614174000',
            'created': '2025-01-15T10:30:00.000Z',
            'modified': '2025-01-15T10:35:00.000Z',
            'error_message': None,
            'metadata': {
                'stripe_payment_intent': 'pi_1234567890',
                'stripe_customer': 'cus_1234567890',
                'amount_total': 50000
            }
        },
        response_only=True,
    ),
    OpenApiExample(
        'Fulfilled State Example',
        summary='CheckoutIntent in fulfilled state',
        description='Successfully provisioned subscription',
        value={
            'uuid': '123e4567-e89b-12d3-a456-426614174002',
            'user': 1,
            'state': CheckoutIntentState.FULFILLED,
            'stripe_checkout_session_id': 'cs_test_c3d4e5f6g7h8i9j0k1l2',
            'enterprise_customer_uuid': '987e6543-e21b-12d3-a456-426614174000',
            'created': '2025-01-15T10:30:00.000Z',
            'modified': '2025-01-15T10:40:00.000Z',
            'error_message': None,
            'metadata': {
                'stripe_payment_intent': 'pi_1234567890',
                'stripe_customer': 'cus_1234567890',
                'amount_total': 50000,
                'subscription_id': 'sub_1234567890',
                'provisioned_at': '2025-01-15T10:40:00.000Z'
            }
        },
        response_only=True,
    ),
    OpenApiExample(
        'Error State Example',
        summary='CheckoutIntent in error state',
        description='Failed during payment or provisioning',
        value={
            'uuid': '123e4567-e89b-12d3-a456-426614174003',
            'user': 1,
            'state': CheckoutIntentState.ERRORED_STRIPE_CHECKOUT,
            'stripe_checkout_session_id': 'cs_test_d4e5f6g7h8i9j0k1l2m3',
            'enterprise_customer_uuid': '987e6543-e21b-12d3-a456-426614174000',
            'created': '2025-01-15T10:30:00.000Z',
            'modified': '2025-01-15T10:35:00.000Z',
            'error_message': 'Payment failed: Card declined',
            'metadata': {
                'failure_reason': 'card_declined',
                'attempt_count': 1
            }
        },
        response_only=True,
    ),
]

PATCH_REQUEST_EXAMPLES = [
    OpenApiExample(
        'Update to Paid State',
        summary='Transition from created to paid',
        description='Updates state after successful payment',
        value={
            'state': CheckoutIntentState.PAID
        },
        request_only=True,
    ),
    OpenApiExample(
        'Update to Error State with Message',
        summary='Transition to error state',
        description='Updates state to error with descriptive message',
        value={
            'state': CheckoutIntentState.ERRORED_STRIPE_CHECKOUT,
            'error_message': 'Payment failed: Insufficient funds'
        },
        request_only=True,
    ),
    OpenApiExample(
        'Update Metadata',
        summary='Update metadata field',
        description='Add or update metadata without changing state',
        value={
            'metadata': {
                'retry_count': 2,
                'last_error': 'timeout',
                'customer_note': 'Retry after card update'
            }
        },
        request_only=True,
    ),
]

ERROR_RESPONSES = {
    400: OpenApiResponse(
        response=inline_serializer(
            name='ValidationError',
            fields={
                'state': serializers.ListField(
                    child=serializers.CharField(),
                    default=['Invalid state transition from created to fulfilled']
                )
            }
        ),
        description='Bad Request - Invalid state transition or invalid data',
        examples=[
            OpenApiExample(
                'Invalid State Transition',
                value={
                    'state': ['Invalid state transition from created to fulfilled']
                }
            ),
            OpenApiExample(
                'Invalid State Value',
                value={
                    'state': ['Invalid state: completed']
                }
            ),
        ]
    ),
    401: OpenApiResponse(
        response=inline_serializer(
            name='AuthenticationError',
            fields={
                'detail': serializers.CharField(default='Authentication credentials were not provided.')
            }
        ),
        description='Unauthorized - Authentication required',
    ),
    403: OpenApiResponse(
        response=inline_serializer(
            name='PermissionError',
            fields={
                'detail': serializers.CharField(default='You do not have permission to perform this action.')
            }
        ),
        description='Forbidden - User does not have permission',
    ),
    404: OpenApiResponse(
        response=inline_serializer(
            name='NotFoundError',
            fields={
                'detail': serializers.CharField(default='Not found.')
            }
        ),
        description='Not Found - CheckoutIntent does not exist or belongs to another user',
    ),
    429: OpenApiResponse(
        response=inline_serializer(
            name='RateLimitError',
            fields={
                'detail': serializers.CharField(default='Request was throttled. Expected available in 30 seconds.')
            }
        ),
        description='Too Many Requests - Rate limit exceeded',
    ),
}
