"""
REST API views for the billing provider (Stripe) integration.
"""
import logging
import uuid

import stripe
from django.conf import settings
from django.http import HttpResponseServerError
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, extend_schema_view
from edx_rbac.decorators import permission_required
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import exceptions, mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from enterprise_access.apps.api import serializers
from enterprise_access.apps.api.authentication import StripeWebhookAuthentication
from enterprise_access.apps.core.constants import (
    ALL_ACCESS_CONTEXT,
    CHECKOUT_INTENT_READ_WRITE_ALL_PERMISSION,
    CUSTOMER_BILLING_CREATE_PORTAL_SESSION_PERMISSION,
    STRIPE_EVENT_SUMMARY_READ_PERMISSION
)
from enterprise_access.apps.customer_billing.api import (
    CreateCheckoutSessionFailedConflict,
    CreateCheckoutSessionSlugReservationConflict,
    CreateCheckoutSessionValidationError,
    create_free_trial_checkout_session
)
from enterprise_access.apps.customer_billing.models import CheckoutIntent, StripeEventSummary
from enterprise_access.apps.customer_billing.stripe_event_handlers import StripeEventHandler

from .constants import CHECKOUT_INTENT_EXAMPLES, ERROR_RESPONSES, PATCH_REQUEST_EXAMPLES

stripe.api_key = settings.STRIPE_API_KEY
logger = logging.getLogger(__name__)

CUSTOMER_BILLING_API_TAG = 'Customer Billing'
STRIPE_EVENT_API_TAG = 'Stripe Event Summary'


class CheckoutIntentPermission(permissions.BasePermission):
    """
    Check for existence of a CheckoutIntent related to the requesting user,
    but only for some views.
    """
    def has_permission(self, request, view):
        if view.action != 'create_checkout_portal_session':
            return True

        checkout_intent_pk = request.parser_context['kwargs']['pk']

        # Try UUID lookup first, then fall back to id lookup
        try:
            uuid_value = uuid.UUID(checkout_intent_pk)
            intent_record = CheckoutIntent.objects.filter(uuid=uuid_value).first()
        except (ValueError, TypeError):
            # Fall back to id lookup
            try:
                int_value = int(checkout_intent_pk)
                intent_record = CheckoutIntent.objects.filter(pk=int_value).first()
            except (ValueError, TypeError):
                return False

        if not intent_record:
            return False

        if intent_record.user != request.user:
            return False

        return True


class CustomerBillingViewSet(viewsets.ViewSet):
    """
    Viewset supporting operations pertaining to customer billing.
    """
    authentication_classes = (JwtAuthentication,)
    permission_classes = (permissions.IsAuthenticated, CheckoutIntentPermission)

    @extend_schema(
        tags=[CUSTOMER_BILLING_API_TAG],
        summary='Listen for events from Stripe.',
    )
    @action(
        detail=False,
        methods=['post'],
        url_path='stripe-webhook',
        authentication_classes=(StripeWebhookAuthentication,),
        permission_classes=(permissions.AllowAny,),
    )
    @csrf_exempt
    def stripe_webhook(self, request):
        """
        Listen for events from Stripe, and take specific actions. Typically the action is to send a confirmation email.

        Authentication is performed via Stripe signature validation in StripeWebhookAuthentication.

        TODO:
        * For a real production implementation we should implement event de-duplication:
          - https://docs.stripe.com/webhooks/process-undelivered-events
          - This is a safeguard against the remote possibility that an event is sent twice. This could happen if the
            network connection cuts out at the exact moment between successfully processing an event and responding with
            HTTP 200, in which case Stripe will attempt to re-send the event since it does not know we successfully
            received it.
        """
        # Event must be parsed and verified by the authentication class.
        event = getattr(request, '_stripe_event', None)
        if event is None:
            # This should not occur if StripeWebhookAuthentication is applied.
            return Response(
                'Stripe WebHook event missing after authentication.',
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
        except (CreateCheckoutSessionSlugReservationConflict, CreateCheckoutSessionFailedConflict) as exc:
            response_serializer = serializers.CustomerBillingCreateCheckoutSessionValidationFailedResponseSerializer(
                errors=exc.non_field_errors,
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
        summary='Create a new Customer Portal Session from the Admin portal MFE.',
    )
    @action(
        detail=False,
        methods=['get'],
        url_path='create-enterprise-admin-portal-session',
    )
    # # UUID in path is used as the "permission object" for role-based auth.
    @permission_required(
        CUSTOMER_BILLING_CREATE_PORTAL_SESSION_PERMISSION,
        fn=lambda request, **kwargs: request.GET.get('enterprise_customer_uuid') or kwargs.get(
            'enterprise_customer_uuid')
    )
    def create_enterprise_admin_portal_session(self, request, **kwargs):
        """
        Create a new Customer Portal Session for the Admin Portal MFE.  Response dict contains "url" key
        that should be attached to a button that the customer clicks.

        Response structure defined here: https://docs.stripe.com/api/customer_portal/sessions/create
        """
        enterprise_uuid = request.query_params.get('enterprise_customer_uuid')
        if not enterprise_uuid:
            msg = "enterprise_customer_uuid parameter is required."
            logger.error(msg)
            return Response(msg, status=status.HTTP_400_BAD_REQUEST)

        checkout_intent = CheckoutIntent.objects.filter(enterprise_uuid=enterprise_uuid).first()
        origin_url = request.META.get("HTTP_ORIGIN")

        if not checkout_intent:
            msg = f"No checkout intent for id, for enterprise_uuid: {enterprise_uuid}"
            logger.error(f"No checkout intent for id, for enterprise_uuid: {enterprise_uuid}")
            return Response(msg, status=status.HTTP_404_NOT_FOUND)

        stripe_customer_id = checkout_intent.stripe_customer_id
        enterprise_slug = checkout_intent.enterprise_slug

        if not (stripe_customer_id or enterprise_slug):
            msg = f"No stripe customer id or enterprise slug associated to enterprise_uuid:{enterprise_uuid}"
            logger.error(msg)
            return Response(msg, status=status.HTTP_404_NOT_FOUND)

        try:
            customer_portal_session = stripe.billing_portal.Session.create(
                customer=stripe_customer_id,
                return_url=f"{origin_url}/{enterprise_slug}",
            )
        except stripe.StripeError as e:
            # TODO: Long term we should be explicit to different types of Stripe error exceptions available
            # https://docs.stripe.com/api/errors/handling, https://docs.stripe.com/error-handling
            msg = f"StripeError creating billing portal session for CheckoutIntent {checkout_intent}: {e}"
            logger.exception(msg)
            return Response(msg, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except Exception as e:  # pylint: disable=broad-except
            msg = f"General exception creating billing portal session for CheckoutIntent {checkout_intent}: {e}"
            logger.exception(msg)
            return Response(msg, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        # TODO: pull out session fields actually needed, and structure a response.
        return Response(
            customer_portal_session,
            status=status.HTTP_200_OK,
            content_type='application/json',
        )

    @extend_schema(
        tags=[CUSTOMER_BILLING_API_TAG],
        summary='Create a new Customer Portal Session from the enterprise checkout MFE.',
    )
    @action(
        detail=True,
        methods=['get'],
        url_path='create-checkout-portal-session',
    )
    def create_checkout_portal_session(self, request, pk=None):
        """
        Create a new Customer Portal Session for the enterprise checkout MFE.  Response dict contains "url" key
        that should be attached to a button that the customer clicks.

        Response structure defined here: https://docs.stripe.com/api/customer_portal/sessions/create
        """
        origin_url = request.META.get("HTTP_ORIGIN")

        # Try UUID lookup first, then fall back to id lookup
        try:
            uuid_value = uuid.UUID(pk)
            checkout_intent = CheckoutIntent.objects.filter(uuid=uuid_value).first()
        except (ValueError, TypeError):
            # Fall back to id lookup
            try:
                int_value = int(pk)
                checkout_intent = CheckoutIntent.objects.filter(pk=int_value).first()
            except (ValueError, TypeError):
                return Response(
                    'Invalid lookup value: must be either a valid UUID or integer ID',
                    status=status.HTTP_400_BAD_REQUEST
                )

        if not checkout_intent:
            msg = f"No checkout intent for id, for requesting user {request.user.id}"
            logger.error(msg)
            return Response(msg, status=status.HTTP_404_NOT_FOUND)

        stripe_customer_id = checkout_intent.stripe_customer_id
        if not stripe_customer_id:
            msg = f"No stripe customer id associated to CheckoutIntent {checkout_intent}"
            logger.error(msg)
            return Response(msg, status=status.HTTP_404_NOT_FOUND)

        if not checkout_intent:
            msg = f"No checkout intent for id {pk}"
            logger.error(f"No checkout intent for id {pk}")
            return Response(msg, status=status.HTTP_404_NOT_FOUND)

        stripe_customer_id = checkout_intent.stripe_customer_id
        enterprise_slug = checkout_intent.enterprise_slug

        if not (stripe_customer_id or enterprise_slug):
            msg = f"No stripe customer id or enterprise slug associated to checkout_intent_id:{pk}"
            logger.error(f"No stripe customer id or enterprise slug associated to checkout_intent_id:{pk}")
            return Response(msg, status=status.HTTP_404_NOT_FOUND)

        try:
            customer_portal_session = stripe.billing_portal.Session.create(
                customer=stripe_customer_id,
                return_url=f"{origin_url}/billing-details/success",
            )
        except stripe.StripeError as e:
            # TODO: Long term we should be explicit to different types of Stripe error exceptions available
            # https://docs.stripe.com/api/errors/handling, https://docs.stripe.com/error-handling
            msg = f"StripeError creating billing portal session for CheckoutIntent {checkout_intent}: {e}"
            logger.exception(msg)
            return Response(msg, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except Exception as e:  # pylint: disable=broad-except
            msg = f"General exception creating billing portal session for CheckoutIntent {checkout_intent}: {e}"
            logger.exception(msg)
            return Response(msg, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        # TODO: pull out session fields actually needed, and structure a response.
        return Response(
            customer_portal_session,
            status=status.HTTP_200_OK,
            content_type='application/json',
        )


@extend_schema_view(
    list=extend_schema(
        summary='List CheckoutIntents',
        description=(
            'Retrieve a list of CheckoutIntent records for the authenticated user. '
            'This endpoint returns only the CheckoutIntent records that belong to the '
            'currently authenticated user, unless the user is staff, in which case '
            '**all** records are returned.'
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
            'Retrieve a specific CheckoutIntent by either ID or UUID. '
            'This endpoint is designed to support polling from the frontend to check '
            'the fulfillment state after a successful Stripe checkout. '
            'Users can only retrieve their own CheckoutIntent records. '
            'Supports lookup by either:\n'
            '- Integer ID (e.g., `/api/v1/checkout-intents/123/`)\n'
            '- UUID (e.g., `/api/v1/checkout-intents/550e8400-e29b-41d4-a716-446655440000/`)\n'
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
            'Update the state of a CheckoutIntent. '
            'This endpoint is used to transition the CheckoutIntent through its lifecycle states. '
            'Only valid state transitions are allowed. '
            'Users can only update their own CheckoutIntent records. '
            'Supports lookup by either:\n'
            '- Integer ID (e.g., `/checkout-intents/123/`)\n'
            '- UUID (e.g., `/checkout-intents/550e8400-e29b-41d4-a716-446655440000/`)\n'
            '\n'
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
            '- **Fulfillment**: Transitions from `paid` to `fulfilled` after provisioning\n'
            '- **Error Recovery**: Allows retry from error states back to `paid`\n\n'
        ),
        parameters=[
            OpenApiParameter(
                name='id',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                required=True,
                description='ID or UUID of the CheckoutIntent to update',
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
    Users can only access their own CheckoutIntent records, unless the user is staff,
    in which case all records can be accessed.

    This ViewSet intentionally does not utilize edx-rbac for permission checking,
    because most use cases involve requesting users who are not yet expected
    to have been granted any enterprise roles. Instead, we manage authorization
    via the ``get_queryset()`` method.

    Supports lookup by either 'id' (integer) or 'uuid' (UUID).
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
        belonging to the authenticated user, unless the requesting user
        has permission to read and write *all* CheckoutIntent records.
        """
        user = self.request.user
        base_queryset = CheckoutIntent.objects.filter(user=user)
        if user.is_staff:
            base_queryset = CheckoutIntent.objects.all()
        return base_queryset.select_related('user')

    def get_object(self):
        """
        Override get_object to support lookup by either id or uuid.

        Attempts to parse the lookup value as UUID first, then falls back to integer id.
        This allows clients to use either field for retrieving CheckoutIntent objects.
        """
        queryset = self.filter_queryset(self.get_queryset())
        lookup_value = self.kwargs[self.lookup_url_kwarg or self.lookup_field]

        try:
            uuid_value = uuid.UUID(lookup_value)
            filter_kwargs = {'uuid': uuid_value}
        except (ValueError, TypeError):
            try:
                int_value = int(lookup_value)
                filter_kwargs = {'id': int_value}
            except (ValueError, TypeError) as exc:
                raise exceptions.ValidationError(
                    'Lookup value must be either a valid UUID or integer ID'
                ) from exc

        try:
            obj = queryset.get(**filter_kwargs)
        except CheckoutIntent.DoesNotExist as exc:
            raise exceptions.NotFound('CheckoutIntent not found') from exc

        self.check_object_permissions(self.request, obj)
        return obj


def stripe_event_summary_permission_detail_fn(request, *args, **kwargs):
    """
    Helper to use with @permission_required on retrieve endpoint.

    Args:
        uuid (str): UUID representing an SubscriptionPlan object.
    """
    if not (subs_plan_uuid := request.query_params.get('subscription_plan_uuid')):
        raise exceptions.ValidationError(detail='subscription_plan_uuid query param is required')

    summary = StripeEventSummary.objects.filter(
        subscription_plan_uuid=subs_plan_uuid,
    ).select_related(
        'checkout_intent',
    ).first()
    if not (summary and summary.checkout_intent):
        return None
    return summary.checkout_intent.enterprise_uuid


class StripeEventSummaryViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """
    ViewSet for StripeEventSummary model.

    Provides retrieve action for StripeEventSummary records.
    """
    authentication_classes = (JwtAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def get_serializer_class(self):
        """
        Return read only serializer.
        """
        return serializers.StripeEventSummaryReadOnlySerializer

    def get_queryset(self):
        """
        Either return full queryset, or filter by all objects associated with
        a subscription_plan_uuid
        """
        subscription_plan_uuid = self.request.query_params.get('subscription_plan_uuid')
        if not subscription_plan_uuid:
            raise exceptions.ValidationError(detail='subscription_plan_uuid query param is required')
        return StripeEventSummary.objects.filter(
            subscription_plan_uuid=subscription_plan_uuid,
        ).select_related(
            'checkout_intent',
        )

    @extend_schema(
        tags=[STRIPE_EVENT_API_TAG],
        summary='Retrieves stripe event summaries.',
        responses={
            status.HTTP_200_OK: serializers.StripeEventSummaryReadOnlySerializer,
            status.HTTP_403_FORBIDDEN: None,
        },
    )
    @permission_required(
        STRIPE_EVENT_SUMMARY_READ_PERMISSION,
        fn=stripe_event_summary_permission_detail_fn,
    )
    def list(self, request, *args, **kwargs):
        """
        Lists ``StripeEventSummary`` records, filtered by given subscription plan uuid.
        """
        return super().list(request, *args, **kwargs)

    @action(
        detail=False,
        methods=['get'],
        url_path='first-invoice-upcoming-amount-due',
    )
    def first_upcoming_invoice_amount_due(self, request, *args, **kwargs):
        """
        Given a license-manager SubscriptionPlan uuid, returns an upcoming
        invoice amount due, dervied from Stripe's preview invoice API.
        """
        subscription_plan_uuid = self.request.query_params.get('subscription_plan_uuid')
        summary = StripeEventSummary.objects.filter(
            event_type='customer.subscription.created',
            subscription_plan_uuid=subscription_plan_uuid,
        ).first()
        if not (subscription_plan_uuid and summary):
            return Response({})
        return Response({
            'upcoming_invoice_amount_due': summary.upcoming_invoice_amount_due,
            'currency': summary.currency,
        })
