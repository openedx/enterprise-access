"""
REST API views for the billing provider (Stripe) integration.
"""
import json
import logging

import requests
import stripe
from django.conf import settings
from django.http import HttpResponseServerError
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, extend_schema_view
from edx_rbac.decorators import permission_required
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from enterprise_access.apps.api import serializers
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.core.constants import CUSTOMER_BILLING_CREATE_PORTAL_SESSION_PERMISSION
from enterprise_access.apps.customer_billing.api import (
    CreateCheckoutSessionValidationError,
    create_free_trial_checkout_session
)
from enterprise_access.apps.customer_billing.models import CheckoutIntent
from enterprise_access.apps.customer_billing.stripe_event_handlers import StripeEventHandler

from .constants import CHECKOUT_INTENT_EXAMPLES, ERROR_RESPONSES, PATCH_REQUEST_EXAMPLES

stripe.api_key = settings.STRIPE_API_KEY
logger = logging.getLogger(__name__)

CUSTOMER_BILLING_API_TAG = 'Customer Billing'


class CustomerBillingViewSet(viewsets.ViewSet):
    """
    Viewset supporting operations pertaining to customer billing.
    """
    authentication_classes = (JwtAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(
        tags=[CUSTOMER_BILLING_API_TAG],
        summary='Listen for events from Stripe.',
    )
    @action(
        detail=False,
        methods=['post'],
        url_path='stripe-webhook',
        # Authentication performed via signature validation.
        # TODO: Move inline authentication logic to custom authentication class which returns a
        # configured Stripe system user.
        authentication_classes=(),
        # TODO: After adopting a custom authentication class, replace this permission class with one
        # that reads the request.user and validates it against the configured system user representing
        # Stripe.
        permission_classes=(permissions.AllowAny,),
    )
    @csrf_exempt
    def stripe_webhook(self, request):
        """
        Listen for events from Stripe, and take specific actions. Typically the action is to send a confirmation email.

        TODO:
        * For a real production implementation we should implement event de-duplication:
          - https://docs.stripe.com/webhooks/process-undelivered-events
          - This is a safeguard against the remote possibility that an event is sent twice. This could happen if the
            network connection cuts out at the exact moment between successfully processing an event and responding with
            HTTP 200, in which case Stripe will attemt to re-send the event since it does not know we successfully
            received it.
        """
        payload = request.body
        event = None

        # TODO: move inline authentication logic into a custom authentication class.
        try:
            # TODO: migrate deprecated `construct_from()` call to newer `construct_event()`.
            event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)
        except ValueError:
            return Response(
                'Stripe WebHook event payload was invalid.',
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Could throw an exception. Do NOT swallow the exception because we
        # need the error response to trigger webhook retries.
        StripeEventHandler.dispatch(event)

        return Response(status=status.HTTP_200_OK)

    @extend_schema(
        tags=[CUSTOMER_BILLING_API_TAG],
        summary='Create a new checkout session given form data from a prospective customer.',
        request=serializers.CustomerBillingCreateCheckoutSessionRequestSerializer,
        responses={
            status.HTTP_201_CREATED: serializers.CustomerBillingCreateCheckoutSessionSuccessResponseSerializer,
            status.HTTP_422_UNPROCESSABLE_ENTITY: (
                serializers.CustomerBillingCreateCheckoutSessionValidationFailedResponseSerializer
            ),
        },
    )
    @action(
        detail=False,
        methods=['post'],
        url_path='create-checkout-session',
    )
    def create_checkout_session(self, request, *args, **kwargs):
        """
        Create a new Stripe checkout session for a free trial and return it's client_secret.

        Notes:
        * This endpoint is designed to be called AFTER logistration, but BEFORE displaying a payment entry form.  A
          Stripe "Checkout Session" object is a prerequisite to rendering the Stripe embedded component for payment
          entry.
        * The @permission_required() decorator has NOT been added. This endpoint only requires an authenticated LMS
          user, which is more permissive than our usual requirement for a user with an enterprise role.
        * This endpoint is NOT idempotent and will create new checkout sessions on each subsequent call.
          TODO: introduce an idempotency key and a new model to hold pending requests.

        Request/response structure:

            POST /api/v1/customer-billing/create_checkout_session
            >>> {
            >>>     "admin_email": "dr@evil.inc",
            >>>     "enterprise_slug": "my-sluggy"
            >>>     "quantity": 7,
            >>>     "stripe_price_id": "price_1MoBy5LkdIwHu7ixZhnattbh"
            >>> }
            HTTP 201 CREATED
            >>> {
            >>>     "checkout_session_client_secret": "cs_Hu7ixZhnattbh1MoBy5LkdIw"
            >>> }
            HTTP 422 UNPROCESSABLE ENTITY (only admin_email validation failed)
            >>> {
            >>>     "admin_email": {
            >>>         "error_code": "not_registered",
            >>>         "developer_message": "The provided email has not yet been registered."
            >>>     }
            >>> }
            HTTP 422 UNPROCESSABLE ENTITY (only enterprise_slug validation failed)
            >>> {
            >>>     "enterprise_slug": {
            >>>         "error_code": "existing_enterprise_customer_for_admin",
            >>>         "developer_message": "Slug invalid: Admin belongs to existing customer..."
            >>>     }
            >>> }
        """
        serializer = serializers.CustomerBillingCreateCheckoutSessionRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        validated_data = serializer.validated_data

        # Simplify tracking create_plan requests using k="v" machine-readable formatting.
        logger.info(
            'Handling request to create free trial plan. '
            f'enterprise_slug="{validated_data["enterprise_slug"]}" '
            f'quantity="{validated_data["quantity"]}" '
            f'stripe_price_id="{validated_data["stripe_price_id"]}"'
        )
        try:
            session = create_free_trial_checkout_session(
                user=request.user,
                **serializer.validated_data,
            )
        except CreateCheckoutSessionValidationError as exc:
            response_serializer = serializers.CustomerBillingCreateCheckoutSessionValidationFailedResponseSerializer(
                data=exc.validation_errors_by_field,
            )
            if not response_serializer.is_valid():
                return HttpResponseServerError()
            return Response(response_serializer.data, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        response_serializer = serializers.CustomerBillingCreateCheckoutSessionSuccessResponseSerializer(
            data={'checkout_session_client_secret': session.client_secret},
        )
        if not response_serializer.is_valid():
            return HttpResponseServerError()
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=[CUSTOMER_BILLING_API_TAG],
        summary='Create a new Customer Portal Session.',
    )
    @action(
        detail=True,
        methods=['get'],
        url_path='create-portal-session',
    )
    # UUID in path is used as the "permission object" for role-based auth.
    @permission_required(CUSTOMER_BILLING_CREATE_PORTAL_SESSION_PERMISSION, fn=lambda request, pk: pk)
    def create_portal_session(self, request, pk=None, **kwargs):
        """
        Create a new Customer Portal Session.  Response dict contains "url" key
        that should be attached to a button that the customer clicks.

        Response structure defined here: https://docs.stripe.com/api/customer_portal/sessions/create
        """
        lms_client = LmsApiClient()
        # First, fetch the enterprise customer data.
        try:
            enterprise_customer_data = lms_client.get_enterprise_customer_data(pk)
        except requests.exceptions.HTTPError:
            return Response(None, status=status.HTTP_404_NOT_FOUND)

        # Next, create a stripe customer portal session.
        customer_portal_session = stripe.billing_portal.Session.create(
            customer=enterprise_customer_data['stripe_customer_id'],
            return_url=f"https://portal.edx.org/{enterprise_customer_data['slug']}",
        )

        # TODO: pull out session fields actually needed, and structure a response.
        return Response(customer_portal_session, status=status.HTTP_200_OK)


@extend_schema_view(
    list=extend_schema(
        summary='List CheckoutIntents',
        description=(
            'Retrieve a list of CheckoutIntent records for the authenticated user.\n'
            'This endpoint returns only the CheckoutIntent records that belong to the '
            'currently authenticated user.'
        ),
        responses={
            200: OpenApiResponse(
                response=serializers.CheckoutIntentReadOnlySerializer,
                description='Successful response with paginated results',
                examples=CHECKOUT_INTENT_EXAMPLES,
            ),
            **{k: v for k, v in ERROR_RESPONSES.items() if k in [401, 403, 429]},
        },
        tags=['Customer Billing'],
        operation_id='list_checkout_intents',
    ),
    retrieve=extend_schema(
        summary='Retrieve CheckoutIntent',
        description=(
            'Retrieve a specific CheckoutIntent by UUID.\n'
            'This endpoint is designed to support polling from the frontend to check '
            'the fulfillment state after a successful Stripe checkout.\n'
            'Users can only retrieve their own CheckoutIntent records.\n'
        ),
        responses={
            200: OpenApiResponse(
                response=serializers.CheckoutIntentReadOnlySerializer,
                description='Successful response',
                examples=CHECKOUT_INTENT_EXAMPLES,
            ),
            **ERROR_RESPONSES,
        },
        tags=['Customer Billing'],
        operation_id='retrieve_checkout_intent',
    ),
    partial_update=extend_schema(
        summary='Update CheckoutIntent State',
        description=(
            'Update the state of a CheckoutIntent.\n'
            'This endpoint is used to transition the CheckoutIntent through its lifecycle states. '
            'Only valid state transitions are allowed.\n'
            'Users can only update their own CheckoutIntent records.\n'
            '## Allowed State Transitions\n'
            '```\n'
            'created → paid\n'
            'created → errored_stripe_checkout\n'
            'paid → fulfilled\n'
            'paid → errored_provisioning\n'
            'errored_stripe_checkout → paid\n'
            'errored_provisioning → paid\n'
            '```\n'
            '## Integration Points\n'
            '- **Stripe Webhook**: Transitions from `created` to `paid` after successful payment\n'
            '- **Fulfillment Service**: Transitions from `paid` to `fulfilled` after provisioning\n'
            '- **Error Recovery**: Allows retry from error states back to `paid`\n\n'
        ),
        parameters=[
            OpenApiParameter(
                name='id',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                required=True,
                description='id of the CheckoutIntent to update',
            ),
        ],
        request=serializers.CheckoutIntentUpdateRequestSerializer,
        examples=PATCH_REQUEST_EXAMPLES,
        responses={
            200: OpenApiResponse(
                response=serializers.CheckoutIntentReadOnlySerializer,
                description='Successfully updated',
                examples=CHECKOUT_INTENT_EXAMPLES,
            ),
            **ERROR_RESPONSES,
        },
        tags=['Customer Billing'],
        operation_id='update_checkout_intent',
    ),
)
class CheckoutIntentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for CheckoutIntent model.

    Provides list, retrieve, and partial_update actions for CheckoutIntent records.
    Users can only access their own CheckoutIntent records.
    """
    authentication_classes = (JwtAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)
    lookup_field = 'id'

    # Only allow GET and PATCH operations
    http_method_names = ['get', 'patch', 'post', 'head', 'options']

    def get_serializer_class(self):
        """
        Use different serializers for different actions.
        """
        if self.action in ['partial_update', 'update']:
            return serializers.CheckoutIntentUpdateRequestSerializer
        elif self.action in ['create']:
            return serializers.CheckoutIntentCreateRequestSerializer
        return serializers.CheckoutIntentReadOnlySerializer

    def get_queryset(self):
        """
        Filter queryset to only include CheckoutIntent records
        belonging to the authenticated user.
        """
        user = self.request.user
        return CheckoutIntent.objects.filter(user=user).select_related('user')

    def partial_update(self, request, *args, **kwargs):
        """
        Override partial_update to validate state transitions.
        """
        instance = self.get_object()

        # Check if state is being updated
        new_state = request.data.get('state')
        if new_state:
            if not CheckoutIntent.is_valid_state_transition(instance.state, new_state):
                raise ValidationError(detail={
                    'state': f'Invalid state transition from {instance.state} to {new_state}'
                })

            logger.info(
                f'CheckoutIntent {instance.id} state transition: '
                f'{instance.state} -> {new_state} by user {request.user.id}'
            )

        return super().partial_update(request, *args, **kwargs)
