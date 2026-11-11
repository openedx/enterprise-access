"""
REST API views for the billing provider (Stripe) integration.
"""
import logging
import uuid

import stripe
from django.conf import settings
from django.db.models import Q
from django.http import Http404, HttpResponseServerError
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, extend_schema_view
from edx_api_doc_tools import query_parameter
from edx_api_doc_tools import schema as api_doc_schema
from edx_django_utils.cache import TieredCache
from edx_rbac.decorators import permission_required
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import exceptions, mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from enterprise_access.apps.api import serializers
from enterprise_access.apps.api.authentication import StripeWebhookAuthentication
from enterprise_access.apps.core.constants import (
    BILLING_MANAGEMENT_ACCESS_PERMISSION,
    CUSTOMER_BILLING_CREATE_PORTAL_SESSION_PERMISSION,
    LEARNER_CREDIT_TOP_UP_PERMISSION,
    STRIPE_EVENT_SUMMARY_READ_PERMISSION
)
from enterprise_access.apps.customer_billing.api import (
    CreateCheckoutSessionFailedConflict,
    CreateCheckoutSessionSlugReservationConflict,
    CreateCheckoutSessionValidationError,
    create_free_trial_checkout_session,
    evaluate_top_up_eligibility_for_policies,
    get_stripe_customer_id_for_enterprise,
    is_top_up_enabled
)
from enterprise_access.apps.customer_billing.constants import TOP_UP_ERRORS
from enterprise_access.apps.customer_billing.models import (
    CheckoutIntent,
    SelfServiceSubscriptionRenewal,
    SspProduct,
    StripeEventSummary
)
from enterprise_access.apps.customer_billing.pricing_api import (
    StripePricingError,
    get_all_stripe_prices,
    get_ssp_product_pricing
)
from enterprise_access.apps.customer_billing.stripe_api import get_stripe_customer
from enterprise_access.apps.customer_billing.stripe_event_handlers import StripeEventHandler
from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy

from .constants import CHECKOUT_INTENT_EXAMPLES, ERROR_RESPONSES, PATCH_REQUEST_EXAMPLES
from .subsidy_access_policy import SubsidyAPIUnavailableException

stripe.api_key = settings.STRIPE_API_KEY
logger = logging.getLogger(__name__)

SSP_PRODUCT_TYPE_ESSENTIALS = 'Essentials'
SSP_PRODUCT_TYPE_TEAMS = 'Teams'

CUSTOMER_BILLING_API_TAG = 'Customer Billing'
STRIPE_EVENT_API_TAG = 'Stripe Event Summary'


class SspProductViewSet(viewsets.ReadOnlyModelViewSet):
    """Public read-only API endpoint for academy-backed SSP products."""

    authentication_classes = ()
    permission_classes = (permissions.AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = 'ssp_product'
    lookup_field = 'slug'
    serializer_class = serializers.SspEssentialsProductResponseSerializer

    def get_queryset(self):
        return SspProduct.objects.filter(is_active=True, academy_uuid__isnull=False).order_by('slug')

    def get_object(self):
        queryset = self.get_queryset()
        lookup_value = self.kwargs[self.lookup_field]
        product = queryset.filter(
            Q(slug=lookup_value) | Q(stripe_price_lookup_key=lookup_value)
        ).first()
        if not product:
            raise Http404
        self.check_object_permissions(self.request, product)
        return product

    def _include_pricing(self):
        include_pricing = self.request.query_params.get('include_pricing', 'true').strip().lower()
        return include_pricing not in {'false', '0', 'no'}

    # pricing orchestration and serialization responsibilities have been moved
    # into `pricing_api.get_all_stripe_prices()` and the serializer respectively.

    @extend_schema(
        tags=[CUSTOMER_BILLING_API_TAG],
        summary='List SSP products',
        description='Public list of active SSP products and optional Stripe-backed pricing.',
        parameters=[
            OpenApiParameter(
                name='include_pricing',
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Set false to skip Stripe pricing lookup and return metadata only.',
            ),
        ],
        responses={
            status.HTTP_200_OK: serializers.SspEssentialsProductResponseSerializer(many=True),
        },
    )
    def list(self, request, *args, **kwargs):
        products = list(self.get_queryset())
        pricing_by_lookup_key = {}
        if self._include_pricing():
            try:
                all_prices = get_all_stripe_prices()
                pricing_by_lookup_key = {
                    p.stripe_price_lookup_key: all_prices.get(p.stripe_price_lookup_key)
                    for p in products
                }
            except StripePricingError:
                logger.exception("Failed to fetch Stripe prices for SSP products.")
                pricing_by_lookup_key = {}

        serializer = self.get_serializer(
            products,
            many=True,
            context={"pricing": pricing_by_lookup_key, 'request': request},
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=[CUSTOMER_BILLING_API_TAG],
        summary='Retrieve SSP product',
        description='Public retrieve of one SSP product by slug. Returns metadata only.',
        responses={
            status.HTTP_200_OK: serializers.SspEssentialsProductResponseSerializer,
            status.HTTP_404_NOT_FOUND: OpenApiResponse(description='SSP product not found'),
        },
    )
    def retrieve(self, request, *args, **kwargs):
        product = self.get_object()
        pricing_by_slug = {}
        serializer = self.get_serializer(product, context={"pricing": pricing_by_slug, 'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


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
            >>>     "stripe_price_id": "price_1MoBy5LkdIwHu7ixZhnattbh",
            >>>     "ssp_product_slug": "ai-academy-yearly"
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
            f'stripe_price_id="{validated_data.get("stripe_price_id")}" '
            f'ssp_product_slug="{validated_data.get("ssp_product_slug")}" '
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
            data={'checkout_session_client_secret': session['client_secret']},
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
            ).to_dict()
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
            msg = f"No checkout intent for id {pk}"
            logger.error(f"No checkout intent for id {pk}")
            return Response(msg, status=status.HTTP_404_NOT_FOUND)

        stripe_customer_id = checkout_intent.stripe_customer_id

        if not stripe_customer_id:
            msg = f"No stripe customer id associated to CheckoutIntent {checkout_intent}"
            logger.error(msg)
            return Response(msg, status=status.HTTP_404_NOT_FOUND)

        try:
            customer_portal_session = stripe.billing_portal.Session.create(
                customer=stripe_customer_id,
                return_url=f"{origin_url}/billing-details/success",
            ).to_dict()
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
        Deprecated first-invoice-upcoming-amount-due endpoint.

        Temporary passthrough to aid with transitioning to get-stripe-subscription-plan-info.
        """
        return self.get_stripe_subscription_plan_info(request, *args, **kwargs)

    @action(
        detail=False,
        methods=['get'],
        url_path='get-stripe-subscription-plan-info',
    )
    @permission_required(
        STRIPE_EVENT_SUMMARY_READ_PERMISSION,
        fn=stripe_event_summary_permission_detail_fn,
    )
    def get_stripe_subscription_plan_info(self, request, *args, **kwargs):
        """
        Given a license-manager SubscriptionPlan uuid, returns information needed for the
        Subscription management page on admin portal, like the upcoming subscription price
        and if the subscription has been cancelled
        """
        subscription_plan_uuid = self.request.query_params.get('subscription_plan_uuid')
        if not subscription_plan_uuid:
            raise exceptions.ValidationError(detail='subscription_plan_uuid query param is required')
        created_event_summary = StripeEventSummary.objects.filter(
            event_type='customer.subscription.created',
            subscription_plan_uuid=subscription_plan_uuid,
        ).select_related('checkout_intent').order_by('-stripe_event_created_at').first()
        updated_event_summary = StripeEventSummary.objects.filter(
            event_type='customer.subscription.updated',
            subscription_plan_uuid=subscription_plan_uuid,
        ).select_related('checkout_intent').order_by('-stripe_event_created_at').first()
        checkout_intent_uuid, canceled_date, currency, upcoming_invoice_amount_due = None, None, None, None
        is_canceled = False
        renewed_subscription_plan_uuid = None

        # TODO: Once paid->paid renewals are generated, filter only on prior_subscription_plan_uuid
        # and drop the renewed_subscription_plan_uuid condition. Today we need both sides of the OR
        # so that both the trial plan and the first paid plan can locate the same renewal record.
        # Once paid->paid renewals exist, a paid plan could match two renewals (the one where it is
        # the renewed plan, and a future one where it is the prior plan), making it ambiguous which
        # to use, and it would be odd to key off an already-completed renewal for information about
        # a future cancellation.
        first_related_renewal = SelfServiceSubscriptionRenewal.objects.filter(
            Q(prior_subscription_plan_uuid=subscription_plan_uuid) |
            Q(renewed_subscription_plan_uuid=subscription_plan_uuid)
        ).select_related('checkout_intent').order_by('created').first()
        if first_related_renewal:
            checkout_intent_uuid = first_related_renewal.checkout_intent.uuid
            is_canceled = first_related_renewal.is_canceled
            renewed_subscription_plan_uuid = first_related_renewal.renewed_subscription_plan_uuid
            canceled_date = first_related_renewal.subscription_cancel_at

        if updated_event_summary:
            # Fall back to the event summary's cancel_at for records predating the
            # subscription_cancel_at field on SelfServiceSubscriptionRenewal.
            if canceled_date is None:
                canceled_date = updated_event_summary.subscription_cancel_at
            checkout_intent_uuid = updated_event_summary.checkout_intent.uuid \
                if updated_event_summary.checkout_intent else checkout_intent_uuid

        if created_event_summary:
            currency = created_event_summary.currency
            # upcoming invoice amount is ONLY populated when trial SubscriptionPlan UUID is given as a query parameter
            # TODO: enhance Self-Service Purchasing to support non-trial query params.
            upcoming_invoice_amount_due = created_event_summary.upcoming_invoice_amount_due
            checkout_intent_uuid = created_event_summary.checkout_intent.uuid \
                if created_event_summary.checkout_intent else checkout_intent_uuid

        response_serializer = serializers.StripeSubscriptionPlanInfoResponseSerializer(
            data={
                'upcoming_invoice_amount_due': upcoming_invoice_amount_due,
                'currency': currency,
                'canceled_date': canceled_date,
                'checkout_intent_uuid': checkout_intent_uuid,
                'is_canceled': is_canceled,
                'renewed_subscription_plan_uuid': renewed_subscription_plan_uuid,
            },
        )
        if not subscription_plan_uuid:
            raise exceptions.NotFound("No associated subscription plan uuid was found")
        if not (updated_event_summary or created_event_summary):
            raise exceptions.NotFound("No Stripe subscription data found for this plan")
        response_serializer.is_valid(raise_exception=True)
        return Response(response_serializer.data, status=status.HTTP_200_OK)


BILLING_MANAGEMENT_API_TAG = 'Billing Management'


class BillingManagementViewSet(viewsets.ViewSet):
    """
    Viewset supporting operations for the Billing Management API.
    This is a new API for managing billing and subscription information.
    """
    authentication_classes = (JwtAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(
        tags=[BILLING_MANAGEMENT_API_TAG],
        summary='Placeholder endpoint for billing management API.',
        description='This endpoint serves as a placeholder for the billing management API.',
    )
    @action(
        detail=False,
        methods=['get'],
        url_path='health-check',
    )
    def health_check(self, request):
        """
        Health check endpoint for the billing management API.
        """
        return Response(
            {'status': 'healthy'},
            status=status.HTTP_200_OK,
        )

    def _get_stripe_customer_id_for_enterprise(self, enterprise_uuid):
        """
        Return the enterprise's Stripe customer ID, or None if it has none.

        Reads EnterpriseStripeCustomer first, then the newest CheckoutIntent with a Stripe customer, so an
        enterprise provisioned without self-service checkout is found too.
        """
        stripe_customer_id = get_stripe_customer_id_for_enterprise(enterprise_uuid)
        if not stripe_customer_id:
            logger.warning(f'No Stripe customer found for enterprise_uuid: {enterprise_uuid}')
        return stripe_customer_id

    def _get_checkout_intent_for_enterprise(self, enterprise_uuid):
        """
        Return the enterprise's newest CheckoutIntent that has a Stripe customer, or None.

        The subscription actions read the subscription's state from this intent's StripeEventSummary records, so they
        need the intent itself, not just a Stripe customer ID.
        """
        checkout_intent = CheckoutIntent.get_latest_for_customer(
            customer_uuid=enterprise_uuid,
            require_stripe_customer=True,
        )
        if not checkout_intent:
            logger.warning(
                f'No checkout intent with stripe customer ID found for enterprise_uuid: {enterprise_uuid}'
            )
        return checkout_intent

    @staticmethod
    def _get_product_type_from_checkout_intent(checkout_intent):
        """
        Derive the public product_type label from the checkout intent's SSP product.

        Returns Teams for non-academy products and Essentials for academy-backed products.
        """
        return SSP_PRODUCT_TYPE_ESSENTIALS if checkout_intent.ssp_product.academy_uuid else SSP_PRODUCT_TYPE_TEAMS

    def _get_active_subscription_for_customer(self, stripe_customer_id):
        """
        Helper method to retrieve the active subscription for a Stripe customer.

        Args:
            stripe_customer_id: The Stripe customer ID

        Returns:
            Stripe Subscription object if found, None otherwise
        """
        subscriptions_response = stripe.Subscription.list(
            customer=stripe_customer_id,
            limit=1,
            status='active',
        )

        if not subscriptions_response.data:
            return None

        return subscriptions_response.data[0]

    @staticmethod
    def _get_payment_method_status(payment_method):
        """
        Helper method to map Stripe payment method status to API status values.

        Args:
            payment_method: Stripe PaymentMethod object

        Returns:
            str: One of 'verified', 'pending', or 'failed'
        """
        pm_type = payment_method.get('type')

        # Cards don't require verification - always verified
        if pm_type == 'card':
            return 'verified'

        # Bank accounts require verification
        if pm_type == 'us_bank_account':
            bank_account = payment_method.get('us_bank_account', {})
            # Check status from nested status_details or top-level status
            stripe_status = bank_account.get('status_details', {}).get('status') or payment_method.get('status')

            # Map Stripe status to our API values
            if stripe_status == 'verified':
                return 'verified'
            elif stripe_status in ('verification_required', 'new'):
                return 'pending'
            elif stripe_status in ('verification_failed', 'errored'):
                return 'failed'

        # Default to verified for unknown types
        return 'verified'

    @extend_schema(
        methods=['GET'],
        tags=[BILLING_MANAGEMENT_API_TAG],
        summary='Get customer billing address',
        description='Retrieve the billing address for a Stripe customer associated with an enterprise.',
        parameters=[
            OpenApiParameter(
                name='enterprise_customer_uuid',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=True,
                description='UUID of the enterprise customer',
            ),
        ],
        responses={
            status.HTTP_200_OK: serializers.BillingAddressResponseSerializer,
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(description='Missing or invalid enterprise_customer_uuid'),
            status.HTTP_403_FORBIDDEN: OpenApiResponse(description='Permission denied'),
            status.HTTP_404_NOT_FOUND: OpenApiResponse(description='Enterprise customer or Stripe customer not found'),
            status.HTTP_422_UNPROCESSABLE_ENTITY: OpenApiResponse(description='Stripe API call failed'),
        },
    )
    @extend_schema(
        methods=['POST'],
        tags=[BILLING_MANAGEMENT_API_TAG],
        summary='Update customer billing address',
        description='Update the billing address for a Stripe customer associated with an enterprise.',
        parameters=[
            OpenApiParameter(
                name='enterprise_customer_uuid',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=True,
                description='UUID of the enterprise customer',
            ),
        ],
        request=serializers.BillingAddressUpdateRequestSerializer,
        responses={
            status.HTTP_200_OK: serializers.BillingAddressResponseSerializer,
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(description='Missing UUID or invalid request data'),
            status.HTTP_403_FORBIDDEN: OpenApiResponse(description='Permission denied'),
            status.HTTP_404_NOT_FOUND: OpenApiResponse(description='Enterprise customer or Stripe customer not found'),
            status.HTTP_422_UNPROCESSABLE_ENTITY: OpenApiResponse(description='Stripe API call failed'),
        },
    )
    @action(
        detail=False,
        methods=['get', 'post'],
        url_path='address',
    )
    @permission_required(
        BILLING_MANAGEMENT_ACCESS_PERMISSION,
        fn=lambda request, **kwargs: request.GET.get('enterprise_customer_uuid')
    )
    def address(self, request, **kwargs):
        """
        Handle billing address endpoint.

        GET: Retrieve the billing address for a Stripe customer.
        POST: Update the billing address for a Stripe customer.

        The enterprise_customer_uuid query parameter is used to:
        1. Find the CheckoutIntent for the enterprise
        2. Extract the Stripe customer ID from the CheckoutIntent
        3. Retrieve/update the customer's billing address from/to Stripe
        """
        if request.method == 'POST':
            return self._update_address(request)
        else:  # GET
            return self._get_address(request)

    def _get_address(self, request):
        """
        Retrieve the billing address for a Stripe customer.
        """
        enterprise_uuid = request.query_params.get('enterprise_customer_uuid')
        if not enterprise_uuid:
            return Response(
                {'error': 'enterprise_customer_uuid query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Get Stripe customer ID using helper method
            stripe_customer_id = self._get_stripe_customer_id_for_enterprise(enterprise_uuid)
            if not stripe_customer_id:
                return Response(
                    {'error': 'Stripe customer not found for this enterprise'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Get the Stripe customer
            stripe_customer_obj = get_stripe_customer(stripe_customer_id)
            if not stripe_customer_obj:
                return Response(
                    {'error': 'Stripe customer not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            stripe_customer = stripe_customer_obj.to_dict()

            # Extract address fields from Stripe customer object
            address_data = {
                'name': stripe_customer.get('name'),
                'email': stripe_customer.get('email'),
                'phone': stripe_customer.get('phone'),
            }

            # Extract address from Stripe's address object if it exists
            if stripe_customer.get('address'):
                address = stripe_customer['address']
                address_data.update({
                    'country': address.get('country'),
                    'address_line_1': address.get('line1'),
                    'address_line_2': address.get('line2'),
                    'city': address.get('city'),
                    'state': address.get('state'),
                    'postal_code': address.get('postal_code'),
                })

            # Serialize and return the response
            response_serializer = serializers.BillingAddressResponseSerializer(data=address_data)
            if not response_serializer.is_valid():
                logger.error(f'Failed to serialize billing address: {response_serializer.errors}')
                return Response(
                    {'error': 'Failed to process billing address'},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY
                )

            return Response(response_serializer.data, status=status.HTTP_200_OK)

        except stripe.error.StripeError as e:
            logger.exception(f'Stripe API error retrieving customer {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': f'Stripe API error: {str(e)}'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.exception(f'Unexpected error retrieving billing address for {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': 'An unexpected error occurred'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

    def _update_address(self, request):
        """
        Update the billing address for a Stripe customer.
        """
        enterprise_uuid = request.query_params.get('enterprise_customer_uuid')
        if not enterprise_uuid:
            return Response(
                {'error': 'enterprise_customer_uuid query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate request data
        request_serializer = serializers.BillingAddressUpdateRequestSerializer(data=request.data)
        if not request_serializer.is_valid():
            return Response(request_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = request_serializer.validated_data

        try:
            # Get Stripe customer ID using helper method
            stripe_customer_id = self._get_stripe_customer_id_for_enterprise(enterprise_uuid)
            if not stripe_customer_id:
                return Response(
                    {'error': 'Stripe customer not found for this enterprise'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Update the Stripe customer with the new address information
            stripe_customer = stripe.Customer.modify(
                stripe_customer_id,
                name=validated_data.get('name'),
                email=validated_data.get('email'),
                phone=validated_data.get('phone'),
                address={
                    'line1': validated_data.get('address_line_1'),
                    'line2': validated_data.get('address_line_2', ''),
                    'city': validated_data.get('city'),
                    'state': validated_data.get('state'),
                    'postal_code': validated_data.get('postal_code'),
                    'country': validated_data.get('country'),
                },
            ).to_dict()

            # Invalidate cached customer data after successful update
            cache_key = f"stripe_get_stripe_customer_{stripe_customer_id}"
            TieredCache.delete_all_tiers(cache_key)
            logger.info(f'Invalidated cache for Stripe customer {stripe_customer_id} after address update')

            # Extract address fields from updated Stripe customer object
            address_data = {
                'name': stripe_customer.get('name'),
                'email': stripe_customer.get('email'),
                'phone': stripe_customer.get('phone'),
            }

            # Extract address from Stripe's address object if it exists
            if stripe_customer.get('address'):
                address = stripe_customer['address']
                address_data.update({
                    'country': address.get('country'),
                    'address_line_1': address.get('line1'),
                    'address_line_2': address.get('line2'),
                    'city': address.get('city'),
                    'state': address.get('state'),
                    'postal_code': address.get('postal_code'),
                })

            # Serialize and return the response
            response_serializer = serializers.BillingAddressResponseSerializer(data=address_data)
            if not response_serializer.is_valid():
                logger.error(f'Failed to serialize updated billing address: {response_serializer.errors}')
                return Response(
                    {'error': 'Failed to process updated billing address'},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY
                )

            return Response(response_serializer.data, status=status.HTTP_200_OK)

        except stripe.error.StripeError as e:
            logger.exception(f'Stripe API error updating customer {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': f'Stripe API error: {str(e)}'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.exception(f'Unexpected error updating billing address for {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': 'An unexpected error occurred'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

    @extend_schema(
        methods=['GET'],
        tags=[BILLING_MANAGEMENT_API_TAG],
        summary='List payment methods',
        description='Retrieve all saved payment methods for a Stripe customer associated with an enterprise.',
        parameters=[
            OpenApiParameter(
                name='enterprise_customer_uuid',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=True,
                description='UUID of the enterprise customer',
            ),
        ],
        responses={
            status.HTTP_200_OK: serializers.PaymentMethodsListResponseSerializer,
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(description='Missing or invalid enterprise_customer_uuid'),
            status.HTTP_403_FORBIDDEN: OpenApiResponse(description='Permission denied'),
            status.HTTP_404_NOT_FOUND: OpenApiResponse(description='Enterprise customer or Stripe customer not found'),
            status.HTTP_422_UNPROCESSABLE_ENTITY: OpenApiResponse(description='Stripe API call failed'),
        },
    )
    @extend_schema(
        methods=['POST'],
        tags=[BILLING_MANAGEMENT_API_TAG],
        summary='Attach payment method',
        description=(
            'Attach a Stripe payment method (created client-side via Stripe Elements) '
            'to the enterprise customer.'
        ),
        parameters=[
            OpenApiParameter(
                name='enterprise_customer_uuid',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=True,
                description='UUID of the enterprise customer',
            ),
        ],
        request=serializers.AttachPaymentMethodRequestSerializer,
        responses={
            status.HTTP_200_OK: serializers.AttachPaymentMethodResponseSerializer,
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(
                description='Missing or invalid enterprise_customer_uuid or payment_method_id'
            ),
            status.HTTP_403_FORBIDDEN: OpenApiResponse(description='Permission denied'),
            status.HTTP_404_NOT_FOUND: OpenApiResponse(
                description='Enterprise customer, Stripe customer, or payment method not found'
            ),
            status.HTTP_409_CONFLICT: OpenApiResponse(
                description='Payment method is already attached to another customer'
            ),
            status.HTTP_422_UNPROCESSABLE_ENTITY: OpenApiResponse(description='Stripe API call failed'),
        },
    )
    @action(
        detail=False,
        methods=['get', 'post'],
        url_path='payment-methods',
    )
    @permission_required(
        BILLING_MANAGEMENT_ACCESS_PERMISSION,
        fn=lambda request, **kwargs: request.GET.get('enterprise_customer_uuid')
    )
    def payment_methods(self, request, **kwargs):
        """
        Handle payment methods endpoint.

        GET: List all payment methods for a Stripe customer.
        POST: Attach a payment method to the Stripe customer.

        The enterprise_customer_uuid query parameter is used to:
        1. Find the CheckoutIntent for the enterprise
        2. Extract the Stripe customer ID from the CheckoutIntent
        3. Retrieve/attach payment methods from/to Stripe
        """
        if request.method == 'POST':
            return self._attach_payment_method(request)
        else:  # GET
            return self._list_payment_methods(request)

    def _list_payment_methods(self, request):
        """
        List all payment methods for a Stripe customer.
        """
        enterprise_uuid = request.query_params.get('enterprise_customer_uuid')
        if not enterprise_uuid:
            return Response(
                {'error': 'enterprise_customer_uuid query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Get Stripe customer ID using helper method
            stripe_customer_id = self._get_stripe_customer_id_for_enterprise(enterprise_uuid)
            if not stripe_customer_id:
                return Response(
                    {'error': 'Stripe customer not found for this enterprise'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Get the Stripe customer to find the default payment method
            stripe_customer = stripe.Customer.retrieve(stripe_customer_id).to_dict()
            default_payment_method_id = stripe_customer.get('invoice_settings', {}).get('default_payment_method')

            # List all payment methods for the customer
            # Query all types by omitting the type filter (cards, bank accounts, etc.)
            payment_methods_response = stripe.PaymentMethod.list(
                customer=stripe_customer_id,
                limit=100,  # Reasonable limit for pagination
            )

            # Transform payment methods into response format
            payment_methods = []
            for pm in payment_methods_response.data:
                pm = pm.to_dict()
                payment_method_data = {
                    'id': pm.get('id'),
                    'type': pm.get('type'),
                    'is_default': pm.get('id') == default_payment_method_id,
                    'status': self._get_payment_method_status(pm),
                }

                # Add card-specific fields if available
                if pm.get('card'):
                    card = pm['card']
                    payment_method_data.update({
                        'last4': card.get('last4'),
                        'brand': card.get('brand'),
                        'exp_month': card.get('exp_month'),
                        'exp_year': card.get('exp_year'),
                    })
                # Add bank account specific fields if applicable
                elif pm.get('us_bank_account'):
                    bank_account = pm['us_bank_account']
                    payment_method_data['last4'] = bank_account.get('last4')

                payment_methods.append(payment_method_data)

            # Serialize and return the response
            response_data = {'payment_methods': payment_methods}
            response_serializer = serializers.PaymentMethodsListResponseSerializer(data=response_data)
            if not response_serializer.is_valid():
                logger.error(f'Failed to serialize payment methods: {response_serializer.errors}')
                return Response(
                    {'error': 'Failed to process payment methods'},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY
                )

            return Response(response_serializer.data, status=status.HTTP_200_OK)

        except stripe.error.StripeError as e:
            logger.exception(f'Stripe API error retrieving payment methods for {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': f'Stripe API error: {str(e)}'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.exception(f'Unexpected error retrieving payment methods for {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': 'An unexpected error occurred'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

    def _attach_payment_method(self, request):
        """
        Attach a Stripe payment method to the enterprise customer.

        The payment_method_id is created client-side via Stripe Elements for PCI compliance,
        then attached to the customer server-side to prevent unauthorized access.

        The enterprise_customer_uuid query parameter is used to:
        1. Find the CheckoutIntent for the enterprise
        2. Extract the Stripe customer ID from the CheckoutIntent
        3. Attach the payment method to the Stripe customer
        """
        enterprise_uuid = request.query_params.get('enterprise_customer_uuid')
        if not enterprise_uuid:
            return Response(
                {'error': 'enterprise_customer_uuid query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate request body
        request_serializer = serializers.AttachPaymentMethodRequestSerializer(data=request.data)
        if not request_serializer.is_valid():
            return Response(request_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        payment_method_id = request_serializer.validated_data.get('payment_method_id')

        try:
            # Get Stripe customer ID using helper method
            stripe_customer_id = self._get_stripe_customer_id_for_enterprise(enterprise_uuid)
            if not stripe_customer_id:
                return Response(
                    {'error': 'Stripe customer not found for this enterprise'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Retrieve payment method to verify it exists and check ownership
            try:
                payment_method = stripe.PaymentMethod.retrieve(payment_method_id).to_dict()
            except stripe.error.InvalidRequestError as e:
                logger.warning(f'Payment method {payment_method_id} not found: {str(e)}')
                return Response(
                    {'error': 'Payment method not found'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Check if payment method is already attached to a customer
            existing_customer = payment_method.get('customer')
            if existing_customer:
                if existing_customer == stripe_customer_id:
                    # Already attached to this customer - return success (true idempotency)
                    logger.info(
                        f'Payment method {payment_method_id} already attached to customer {stripe_customer_id}'
                    )
                else:
                    # Attached to a different customer - cannot attach
                    logger.warning(
                        f'Payment method {payment_method_id} already attached to different customer {existing_customer}'
                    )
                    return Response(
                        {'error': 'Payment method is already attached to another customer'},
                        status=status.HTTP_409_CONFLICT
                    )
            else:
                # Not attached to any customer - proceed with attach
                stripe.PaymentMethod.attach(
                    payment_method_id,
                    customer=stripe_customer_id,
                )

            # Return success response
            response_data = {
                'message': 'Payment method added successfully',
                'payment_method_id': payment_method_id,
            }
            response_serializer = serializers.AttachPaymentMethodResponseSerializer(data=response_data)
            if not response_serializer.is_valid():
                logger.error(f'Failed to serialize attach response: {response_serializer.errors}')
                return Response(
                    {'error': 'Failed to process response'},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY
                )

            return Response(response_serializer.data, status=status.HTTP_200_OK)

        except stripe.error.StripeError as e:
            logger.exception(f'Stripe API error attaching payment method for {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': f'Stripe API error: {str(e)}'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.exception(f'Unexpected error attaching payment method for {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': 'An unexpected error occurred'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

    @extend_schema(
        tags=[BILLING_MANAGEMENT_API_TAG],
        summary='Set default payment method',
        description='Set a payment method as the default for a Stripe customer associated with an enterprise.',
        parameters=[
            OpenApiParameter(
                name='enterprise_customer_uuid',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=True,
                description='UUID of the enterprise customer',
            ),
            OpenApiParameter(
                name='id',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                required=True,
                description='ID of the payment method to set as default',
            ),
        ],
        request=serializers.SetDefaultPaymentMethodRequestSerializer,
        responses={
            status.HTTP_200_OK: OpenApiResponse(description='Payment method set as default successfully'),
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(description='Missing or invalid enterprise_customer_uuid'),
            status.HTTP_403_FORBIDDEN: OpenApiResponse(description='Permission denied'),
            status.HTTP_404_NOT_FOUND: OpenApiResponse(
                description='Enterprise customer, Stripe customer, or payment method not found'
            ),
            status.HTTP_422_UNPROCESSABLE_ENTITY: OpenApiResponse(description='Stripe API call failed'),
        },
    )
    @action(
        detail=False,
        methods=['post'],
        url_path='payment-methods/(?P<payment_method_id>[^/]+)/set-default',
    )
    @permission_required(
        BILLING_MANAGEMENT_ACCESS_PERMISSION,
        fn=lambda request, **kwargs: request.GET.get('enterprise_customer_uuid')
    )
    def set_default_payment_method(self, request, payment_method_id=None, **kwargs):
        """
        Set a payment method as the default for a Stripe customer.

        The enterprise_customer_uuid query parameter is used to:
        1. Find the CheckoutIntent for the enterprise
        2. Extract the Stripe customer ID from the CheckoutIntent
        3. Verify the payment method belongs to this customer
        4. Set it as the default payment method
        """
        enterprise_uuid = request.query_params.get('enterprise_customer_uuid')
        if not enterprise_uuid:
            return Response(
                {'error': 'enterprise_customer_uuid query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not payment_method_id:
            return Response(
                {'error': 'payment_method_id is required in the URL path'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Get Stripe customer ID using helper method
            stripe_customer_id = self._get_stripe_customer_id_for_enterprise(enterprise_uuid)
            if not stripe_customer_id:
                return Response(
                    {'error': 'Stripe customer not found for this enterprise'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Verify the payment method exists and belongs to this customer
            payment_method = stripe.PaymentMethod.retrieve(payment_method_id).to_dict()
            if not payment_method or payment_method.get('customer') != stripe_customer_id:
                logger.warning(
                    f'Payment method {payment_method_id} does not belong to customer {stripe_customer_id}'
                )
                return Response(
                    {'error': 'Payment method not found or does not belong to this customer'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Set the payment method as the default
            stripe.Customer.modify(
                stripe_customer_id,
                invoice_settings={'default_payment_method': payment_method_id},
            )

            # Invalidate cached customer data after successful update
            cache_key = f"stripe_get_stripe_customer_{stripe_customer_id}"
            TieredCache.delete_all_tiers(cache_key)
            logger.info(
                f'Invalidated cache for Stripe customer {stripe_customer_id} '
                'after setting default payment method'
            )

            return Response(
                {'message': 'Payment method set as default successfully'},
                status=status.HTTP_200_OK
            )

        except stripe.error.InvalidRequestError as e:
            logger.warning(f'Invalid Stripe request for payment method {payment_method_id}: {str(e)}')
            return Response(
                {'error': 'Payment method not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except stripe.error.StripeError as e:
            logger.exception(f'Stripe API error setting default payment method for {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': f'Stripe API error: {str(e)}'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.exception(f'Unexpected error setting default payment method for {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': 'An unexpected error occurred'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

    @extend_schema(
        tags=[BILLING_MANAGEMENT_API_TAG],
        summary='Delete payment method',
        description='Remove a payment method from a Stripe customer associated with an enterprise.',
        parameters=[
            OpenApiParameter(
                name='enterprise_customer_uuid',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=True,
                description='UUID of the enterprise customer',
            ),
            OpenApiParameter(
                name='id',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                required=True,
                description='ID of the payment method to delete',
            ),
        ],
        responses={
            status.HTTP_200_OK: OpenApiResponse(description='Payment method deleted successfully'),
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(description='Missing or invalid enterprise_customer_uuid'),
            status.HTTP_403_FORBIDDEN: OpenApiResponse(description='Permission denied'),
            status.HTTP_404_NOT_FOUND: OpenApiResponse(
                description='Enterprise customer, Stripe customer, or payment method not found'
            ),
            status.HTTP_409_CONFLICT: OpenApiResponse(
                description='Cannot delete only payment method or must change default first'
            ),
            status.HTTP_422_UNPROCESSABLE_ENTITY: OpenApiResponse(description='Stripe API call failed'),
        },
    )
    @action(
        detail=False,
        methods=['delete'],
        url_path='payment-methods/(?P<payment_method_id>[^/]+)',
    )
    @permission_required(
        BILLING_MANAGEMENT_ACCESS_PERMISSION,
        fn=lambda request, **kwargs: request.GET.get('enterprise_customer_uuid')
    )
    def delete_payment_method(self, request, payment_method_id=None, **kwargs):
        """
        Delete a payment method from a Stripe customer.

        The enterprise_customer_uuid query parameter is used to:
        1. Find the CheckoutIntent for the enterprise
        2. Extract the Stripe customer ID from the CheckoutIntent
        3. Verify the payment method belongs to this customer
        4. Check constraints (only payment method, or is default with others)
        5. Detach the payment method from the customer
        """
        enterprise_uuid = request.query_params.get('enterprise_customer_uuid')
        if not enterprise_uuid:
            return Response(
                {'error': 'enterprise_customer_uuid query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not payment_method_id:
            return Response(
                {'error': 'payment_method_id is required in the URL path'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Get Stripe customer ID using helper method
            stripe_customer_id = self._get_stripe_customer_id_for_enterprise(enterprise_uuid)
            if not stripe_customer_id:
                return Response(
                    {'error': 'Stripe customer not found for this enterprise'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Verify the payment method exists and belongs to this customer
            payment_method = stripe.PaymentMethod.retrieve(payment_method_id).to_dict()
            if not payment_method or payment_method.get('customer') != stripe_customer_id:
                logger.warning(
                    f'Payment method {payment_method_id} does not belong to customer {stripe_customer_id}'
                )
                return Response(
                    {'error': 'Payment method not found or does not belong to this customer'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Get the customer to check payment method count and default
            stripe_customer = stripe.Customer.retrieve(stripe_customer_id).to_dict()
            default_payment_method_id = stripe_customer.get('invoice_settings', {}).get('default_payment_method')

            # List all payment methods for the customer
            payment_methods_response = stripe.PaymentMethod.list(
                customer=stripe_customer_id,
                type='card',
                limit=100,
            )

            # Count total payment methods (including other types if they exist)
            # For now, we check only card type, but in future might need to include other types
            total_payment_methods = len(payment_methods_response.data)

            # Check if this is the only payment method
            if total_payment_methods <= 1:
                return Response(
                    {'error': 'Cannot delete the only payment method on the account'},
                    status=status.HTTP_409_CONFLICT
                )

            # Check if this is the default and others exist
            if payment_method_id == default_payment_method_id:
                return Response(
                    {'error': 'Set another method as default before deleting this one'},
                    status=status.HTTP_409_CONFLICT
                )

            # Detach the payment method from the customer
            stripe.PaymentMethod.detach(payment_method_id)

            return Response(
                {'message': 'Payment method deleted successfully'},
                status=status.HTTP_200_OK
            )

        except stripe.error.InvalidRequestError as e:
            logger.warning(f'Invalid Stripe request for payment method {payment_method_id}: {str(e)}')
            return Response(
                {'error': 'Payment method not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except stripe.error.StripeError as e:
            logger.exception(
                f'Stripe API error deleting payment method {payment_method_id} for {enterprise_uuid}: {str(e)}'
            )
            return Response(
                {'error': f'Stripe API error: {str(e)}'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.exception(f'Unexpected error deleting payment method for {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': 'An unexpected error occurred'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

    @extend_schema(
        tags=[BILLING_MANAGEMENT_API_TAG],
        summary='List transactions',
        description=(
            'Retrieve paginated invoice/transaction history for a Stripe customer associated with an enterprise.'
        ),
        parameters=[
            OpenApiParameter(
                name='enterprise_customer_uuid',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=True,
                description='UUID of the enterprise customer',
            ),
            OpenApiParameter(
                name='page_token',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Stripe pagination cursor for continuing a paginated list',
            ),
            OpenApiParameter(
                name='limit',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Number of transactions to return per page (default 10, max 25)',
            ),
        ],
        responses={
            status.HTTP_200_OK: serializers.TransactionsListResponseSerializer,
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(
                description='Missing or invalid enterprise_customer_uuid or parameters'
            ),
            status.HTTP_403_FORBIDDEN: OpenApiResponse(description='Permission denied'),
            status.HTTP_404_NOT_FOUND: OpenApiResponse(description='Enterprise customer or Stripe customer not found'),
            status.HTTP_422_UNPROCESSABLE_ENTITY: OpenApiResponse(description='Stripe API call failed'),
        },
    )
    @action(detail=False, methods=['get'], url_path='transactions')
    @permission_required(
        BILLING_MANAGEMENT_ACCESS_PERMISSION,
        fn=lambda request, **kwargs: request.GET.get('enterprise_customer_uuid')
    )
    def list_transactions(self, request, **kwargs):
        """
        List transactions/invoices for a Stripe customer.

        The enterprise_customer_uuid query parameter is used to:
        1. Find the CheckoutIntent for the enterprise
        2. Extract the Stripe customer ID from the CheckoutIntent
        3. Retrieve paginated invoices from Stripe
        4. Fetch charge information for receipt URLs
        """
        enterprise_uuid = request.query_params.get('enterprise_customer_uuid')
        if not enterprise_uuid:
            return Response(
                {'error': 'enterprise_customer_uuid query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get and validate pagination parameters
        page_token = request.query_params.get('page_token')
        try:
            limit = int(request.query_params.get('limit', 10))
            if limit < 1 or limit > 25:
                limit = 10
        except (ValueError, TypeError):
            limit = 10

        try:
            # Get Stripe customer ID using helper method
            stripe_customer_id = self._get_stripe_customer_id_for_enterprise(enterprise_uuid)
            if not stripe_customer_id:
                return Response(
                    {'error': 'Stripe customer not found for this enterprise'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Retrieve invoices from Stripe with pagination
            invoices_response = stripe.Invoice.list(
                customer=stripe_customer_id,
                limit=limit,
                starting_after=page_token,
            )

            # Transform invoices to transaction format
            transactions = []
            for invoice in invoices_response.data:
                invoice = invoice.to_dict()
                transaction = {
                    'id': invoice.get('id'),
                    'created': invoice.get('created'),  # Unix timestamp from Stripe
                    'amount': invoice.get('amount_paid'),  # Amount in cents
                    'currency': invoice.get('currency', 'usd').lower(),
                    'status': self._normalize_invoice_status(invoice.get('status')),
                    'description': invoice.get('description'),
                    'invoice_pdf_url': invoice.get('hosted_invoice_url'),
                }

                # Attempt to get receipt_url from associated charge
                try:
                    if invoice.get('charge'):
                        charge = stripe.Charge.retrieve(invoice.get('charge')).to_dict()
                        transaction['receipt_url'] = charge.get('receipt_url')
                except stripe.error.StripeError as e:
                    logger.warning(f'Could not retrieve charge {invoice.get("charge")} for receipt URL: {str(e)}')
                    transaction['receipt_url'] = None

                transactions.append(transaction)

            # Determine if there are more results
            next_page_token = None
            if invoices_response.has_more:
                # Get the last invoice's ID for pagination
                if transactions:
                    next_page_token = transactions[-1]['id']

            # Serialize and return response
            response_data = {
                'transactions': transactions,
                'next_page_token': next_page_token,
            }

            serializer = serializers.TransactionsListResponseSerializer(response_data)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except stripe.error.InvalidRequestError as e:
            logger.warning(f'Invalid Stripe request for customer {stripe_customer_id}: {str(e)}')
            return Response(
                {'error': 'Invalid request to Stripe API'},
                status=status.HTTP_400_BAD_REQUEST
            )
        except stripe.error.StripeError as e:
            logger.exception(f'Stripe API error retrieving transactions for {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': f'Stripe API error: {str(e)}'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.exception(f'Unexpected error retrieving transactions for {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': 'An unexpected error occurred'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

    @staticmethod
    def _normalize_invoice_status(stripe_status):
        """
        Normalize Stripe invoice status to canonical form.

        Stripe returns statuses like 'draft', 'open', 'paid', 'uncollectible', 'void'.
        We normalize to: 'paid', 'open', 'void', 'uncollectible'
        """
        if stripe_status in ['paid']:
            return 'paid'
        elif stripe_status in ['draft', 'open']:
            return 'open'
        elif stripe_status in ['void']:
            return 'void'
        elif stripe_status in ['uncollectible']:
            return 'uncollectible'
        return 'open'  # Default to open

    @extend_schema(
        tags=[BILLING_MANAGEMENT_API_TAG],
        summary='Retrieve subscription',
        description=(
            'Retrieve an overview of the current Stripe subscription state '
            'associated with an enterprise.'
        ),
        parameters=[
            OpenApiParameter(
                name='enterprise_customer_uuid',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=True,
                description='UUID of the enterprise customer',
            ),
        ],
        responses={
            status.HTTP_200_OK: serializers.StripeSubscriptionResponseSerializer,
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(description='Missing or invalid enterprise_customer_uuid'),
            status.HTTP_403_FORBIDDEN: OpenApiResponse(description='Permission denied'),
            status.HTTP_404_NOT_FOUND: OpenApiResponse(description='Enterprise customer or Stripe customer not found'),
            status.HTTP_422_UNPROCESSABLE_ENTITY: OpenApiResponse(description='Failed to retrieve subscription data'),
        },
    )
    @action(detail=False, methods=['get'], url_path='subscription')
    @permission_required(
        BILLING_MANAGEMENT_ACCESS_PERMISSION,
        fn=lambda request, **kwargs: request.GET.get('enterprise_customer_uuid')
    )
    def get_subscription(self, request, **kwargs):
        """
        Get subscription status for a Stripe customer.

        Returns subscription details if an active subscription exists, otherwise returns null.
        Subscription data is retrieved from StripeEventSummary which stores the most recent
        state from Stripe webhook events.
        """
        enterprise_uuid = request.query_params.get('enterprise_customer_uuid')
        if not enterprise_uuid:
            return Response(
                {'error': 'enterprise_customer_uuid query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Get CheckoutIntent for the enterprise
            checkout_intent = self._get_checkout_intent_for_enterprise(enterprise_uuid)
            if not checkout_intent:
                return Response(
                    {'error': 'Checkout Intent not found for this enterprise'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Get the most recent subscription event summary for subscription status
            subscription_summary = StripeEventSummary.get_latest_for_checkout_intent(
                checkout_intent=checkout_intent,
                event_type__in=['customer.subscription.updated', 'customer.subscription.created'],
            )

            # If no subscription events, return empty response
            if not subscription_summary:
                return Response({}, status=status.HTTP_200_OK)

            # Check if subscription status indicates an active subscription
            if subscription_summary.subscription_status not in ['active', 'trialing']:
                return Response({}, status=status.HTTP_200_OK)

            # Get the latest invoice event for pricing data (invoice_unit_amount, invoice_quantity)
            # Invoice events have the actual billing amounts, subscription events don't
            invoice_summary = StripeEventSummary.get_latest_for_checkout_intent(
                checkout_intent=checkout_intent,
                event_type__in=['invoice.paid', 'invoice.created'],
            )

            # Calculate yearly amount and license count from invoice data
            yearly_amount = None
            license_count = None
            if invoice_summary:
                license_count = invoice_summary.invoice_quantity
                if (
                        invoice_summary.invoice_unit_amount is not None and
                        invoice_summary.invoice_quantity is not None
                ):
                    yearly_amount = invoice_summary.invoice_unit_amount * invoice_summary.invoice_quantity

            # During trial periods, the invoice may be $0. Fall back to upcoming_invoice_amount_due
            # which is populated on customer.subscription.created events
            if not yearly_amount:
                created_summary = StripeEventSummary.get_latest_for_checkout_intent(
                    checkout_intent=checkout_intent,
                    event_type='customer.subscription.created',
                )
                if created_summary and created_summary.upcoming_invoice_amount_due:
                    yearly_amount = created_summary.upcoming_invoice_amount_due

            # Extract cancel_at timestamp
            cancel_at = None
            if subscription_summary.subscription_cancel_at:
                cancel_at = int(subscription_summary.subscription_cancel_at.timestamp())

            # Extract current_period_end timestamp
            current_period_end = None
            if subscription_summary.subscription_period_end:
                current_period_end = int(subscription_summary.subscription_period_end.timestamp())

            sub_data = {
                'id': subscription_summary.stripe_subscription_id,
                'status': subscription_summary.subscription_status,
                'cancel_at_period_end': bool(subscription_summary.subscription_cancel_at),
                'cancel_at': cancel_at,
                'current_period_end': current_period_end,
                'currency': subscription_summary.currency,
                'product_type': self._get_product_type_from_checkout_intent(checkout_intent),
                'yearly_amount': yearly_amount,
                'license_count': license_count,
                'academy_name': getattr(checkout_intent.ssp_product, 'academy_title', None),
            }

            serializer = serializers.StripeSubscriptionResponseSerializer(data=sub_data)
            serializer.is_valid(raise_exception=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except Exception as e:  # pylint: disable=broad-except
            logger.exception(f'Unexpected error retrieving subscription for {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': 'An unexpected error occurred'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

    @staticmethod
    def _get_plan_type_from_subscription(subscription):
        """
        Derive plan_type from Stripe subscription's price/product metadata.

        Looks for 'plan_type' key in price metadata first, then product metadata.
        Returns 'Other' if no matching metadata is found.

        Expected plan_type values: 'Teams', 'Essentials', 'LearnerCredit', 'Other'
        """
        try:
            subscription = subscription.to_dict()
            # Get the price object from the subscription
            items = subscription.get('items', {}).get('data', [])
            if not items:
                return 'Other'

            price_id = items[0].get('price', {}).get('id')
            if not price_id:
                return 'Other'

            # Retrieve the price to access metadata
            price = stripe.Price.retrieve(price_id).to_dict()

            # Check price metadata first
            price_metadata = price.get('metadata', {})
            if 'plan_type' in price_metadata:
                return price_metadata.get('plan_type', 'Other')

            # Check product metadata
            product_id = price.get('product')
            if product_id:
                product = stripe.Product.retrieve(product_id).to_dict()
                product_metadata = product.get('metadata', {})
                if 'plan_type' in product_metadata:
                    return product_metadata.get('plan_type', 'Other')

            return 'Other'

        except stripe.error.StripeError as e:
            logger.warning(f'Could not retrieve plan type from Stripe metadata: {str(e)}')
            return 'Other'
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Unexpected error deriving plan_type: {str(e)}')
            return 'Other'

    @staticmethod
    def _is_subscription_cancellable(subscription):
        """
        Check if a subscription is eligible for cancellation/reinstatement.

        Validates by matching the subscription's price ID against SSP product pricing.
        This is more reliable than checking metadata which may not be consistently set.

        Returns:
            bool: True if subscription price matches an SSP product, False otherwise
        """
        try:
            subscription = subscription.to_dict()
            # Get the price ID from the subscription
            items = subscription.get('items', {}).get('data', [])
            if not items:
                logger.warning('Subscription has no items')
                return False

            # Get SSP product pricing data
            ssp_pricing = get_ssp_product_pricing()

            # Check if ANY item's price matches an SSP product
            for item in items:
                price_id = item.get('price', {}).get('id')
                if not price_id:
                    continue

                # Check if this subscription's price_id is contained in the SSP product config
                for product_key, price_data in ssp_pricing.items():
                    if price_data.get('id') == price_id:
                        logger.info(f'Subscription price {price_id} matches SSP product {product_key}')
                        return True

            logger.warning('No subscription items match any SSP product')
            return False

        except Exception as e:  # pylint: disable=broad-except
            logger.exception(f'Error checking if subscription is cancellable: {str(e)}')
            return False

    @staticmethod
    def _get_yearly_amount(subscription):
        """
        Calculate yearly amount from subscription items.

        Returns the total yearly recurring revenue for the subscription.
        """
        try:
            subscription = subscription.to_dict()
            items = subscription.get('items', {}).get('data', [])
            if not items:
                return 0

            total_amount = 0
            for item in items:
                price_obj = item.get('price', {})
                quantity = item.get('quantity', 1)

                # If price object doesn't have unit_amount, retrieve full price details
                if 'unit_amount' not in price_obj:
                    price_id = price_obj.get('id')
                    if price_id:
                        try:
                            price_obj = stripe.Price.retrieve(price_id).to_dict()
                        except stripe.error.StripeError:
                            logger.warning(f'Could not retrieve price {price_id}')
                            continue

                # Get the unit amount
                unit_amount = price_obj.get('unit_amount', 0)

                # Calculate yearly amount based on billing period
                billing_period = price_obj.get('recurring', {}).get('interval')
                if billing_period == 'year':
                    total_amount += unit_amount * quantity
                elif billing_period == 'month':
                    total_amount += (unit_amount * 12) * quantity
                elif billing_period == 'week':
                    total_amount += (unit_amount * 52) * quantity

            return total_amount

        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Could not calculate yearly amount: {str(e)}')
            return 0

    @staticmethod
    def _get_license_count(subscription):
        """
        Get license count from subscription items.

        Uses the quantity from subscription items as license count.
        Returns the sum of quantities across all items.
        """
        try:
            subscription = subscription.to_dict()
            items = subscription.get('items', {}).get('data', [])
            if not items:
                return 0

            total_licenses = 0
            for item in items:
                quantity = item.get('quantity', 0)
                total_licenses += quantity

            return total_licenses

        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Could not get license count: {str(e)}')
            return 0

    @extend_schema(
        tags=[BILLING_MANAGEMENT_API_TAG],
        summary='Cancel subscription',
        description=(
            'Request cancellation of a subscription at the end of the current billing period. '
            'Only available for Teams and Essentials plans.'
        ),
        parameters=[
            OpenApiParameter(
                name='enterprise_customer_uuid',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=True,
                description='UUID of the enterprise customer',
            ),
        ],
        responses={
            status.HTTP_200_OK: serializers.StripeSubscriptionResponseSerializer,
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(description='Missing or invalid enterprise_customer_uuid'),
            status.HTTP_403_FORBIDDEN: OpenApiResponse(
                description='Permission denied or plan type does not support cancellation'
            ),
            status.HTTP_404_NOT_FOUND: OpenApiResponse(
                description='Enterprise customer, Stripe customer, or active subscription not found'
            ),
            status.HTTP_409_CONFLICT: OpenApiResponse(description='Subscription is already scheduled for cancellation'),
            status.HTTP_422_UNPROCESSABLE_ENTITY: OpenApiResponse(description='Stripe API call failed'),
        },
    )
    @action(detail=False, methods=['post'], url_path='subscription/cancel')
    @permission_required(
        BILLING_MANAGEMENT_ACCESS_PERMISSION,
        fn=lambda request, **kwargs: request.GET.get('enterprise_customer_uuid')
    )
    def cancel_subscription(self, request, **kwargs):
        """
        Cancel a subscription at the end of the current billing period.

        Only Teams and Essentials plans can be cancelled. Returns 403 for other plan types.
        Returns 409 if the subscription is already scheduled for cancellation.
        """
        enterprise_uuid = request.query_params.get('enterprise_customer_uuid')
        if not enterprise_uuid:
            return Response(
                {'error': 'enterprise_customer_uuid query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Get CheckoutIntent for the enterprise
            checkout_intent = self._get_checkout_intent_for_enterprise(enterprise_uuid)
            if not checkout_intent:
                return Response(
                    {'error': 'Stripe customer not found for this enterprise'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Get the most recent subscription event summary to check current state
            event_summary = StripeEventSummary.objects.filter(
                checkout_intent=checkout_intent,
                event_type__in=['customer.subscription.updated', 'customer.subscription.created'],
            ).order_by('-stripe_event_created_at').first()

            if not event_summary or not event_summary.stripe_subscription_id:
                return Response(
                    {'error': 'No active subscription found for this enterprise'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Check if subscription is active
            if event_summary.subscription_status not in ['active', 'trialing']:
                return Response(
                    {'error': 'No active subscription found for this enterprise'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Check if already scheduled for cancellation
            if event_summary.subscription_cancel_at:
                return Response(
                    {'error': 'Subscription is already scheduled for cancellation'},
                    status=status.HTTP_409_CONFLICT
                )

            # Verify cancellation eligibility by checking if subscription price matches SSP products
            # Get subscription from Stripe to check eligibility
            subscription = stripe.Subscription.retrieve(event_summary.stripe_subscription_id)
            if not self._is_subscription_cancellable(subscription):
                return Response(
                    {'error': 'Subscription cancellation is not available for your plan type'},
                    status=status.HTTP_403_FORBIDDEN
                )

            # Cancel the subscription at period end
            updated_subscription = stripe.Subscription.modify(
                event_summary.stripe_subscription_id,
                cancel_at_period_end=True,
            ).to_dict()

            # Build response with updated subscription data
            sub_data = {
                'id': updated_subscription.get('id'),
                'status': updated_subscription.get('status'),
                'cancel_at_period_end': updated_subscription.get('cancel_at_period_end', False),
                'current_period_end': updated_subscription.get('current_period_end'),
                'product_type': self._get_product_type_from_checkout_intent(checkout_intent),
                'academy_name': getattr(checkout_intent.ssp_product, 'academy_title', None),
            }

            serializer = serializers.StripeSubscriptionResponseSerializer(data=sub_data)
            serializer.is_valid(raise_exception=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except stripe.error.InvalidRequestError as e:
            logger.warning(f'Invalid Stripe request for enterprise {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': 'Invalid request to Stripe API'},
                status=status.HTTP_400_BAD_REQUEST
            )
        except stripe.error.StripeError as e:
            logger.exception(f'Stripe API error cancelling subscription for {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': f'Stripe API error: {str(e)}'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.exception(f'Unexpected error cancelling subscription for {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': 'An unexpected error occurred'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

    @extend_schema(
        tags=[BILLING_MANAGEMENT_API_TAG],
        summary='Reinstate subscription',
        description=(
            'Remove a scheduled cancellation from a subscription. '
            'Only available for Teams and Essentials plans.'
        ),
        parameters=[
            OpenApiParameter(
                name='enterprise_customer_uuid',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=True,
                description='UUID of the enterprise customer',
            ),
        ],
        responses={
            status.HTTP_200_OK: serializers.StripeSubscriptionResponseSerializer,
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(description='Missing or invalid enterprise_customer_uuid'),
            status.HTTP_403_FORBIDDEN: OpenApiResponse(
                description='Permission denied or plan type does not support reinstatement'
            ),
            status.HTTP_404_NOT_FOUND: OpenApiResponse(
                description='Enterprise customer, Stripe customer, or active subscription not found'
            ),
            status.HTTP_409_CONFLICT: OpenApiResponse(
                description='Subscription is not pending cancellation or period has ended'
            ),
            status.HTTP_422_UNPROCESSABLE_ENTITY: OpenApiResponse(description='Stripe API call failed'),
        },
    )
    @action(detail=False, methods=['post'], url_path='subscription/reinstate')
    @permission_required(
        BILLING_MANAGEMENT_ACCESS_PERMISSION,
        fn=lambda request, **kwargs: request.GET.get('enterprise_customer_uuid')
    )
    def reinstate_subscription(self, request, **kwargs):
        """
        Reinstate a subscription that is scheduled for cancellation.

        Only Teams and Essentials plans can be reinstated. Returns 403 for other plan types.
        Returns 409 if the subscription is not currently scheduled for cancellation or if the period has ended.
        """
        enterprise_uuid = request.query_params.get('enterprise_customer_uuid')
        if not enterprise_uuid:
            return Response(
                {'error': 'enterprise_customer_uuid query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Get CheckoutIntent for the enterprise
            checkout_intent = self._get_checkout_intent_for_enterprise(enterprise_uuid)
            if not checkout_intent:
                return Response(
                    {'error': 'Stripe customer not found for this enterprise'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Get the most recent subscription event summary to check current state
            event_summary = StripeEventSummary.objects.filter(
                checkout_intent=checkout_intent,
                event_type__in=['customer.subscription.updated', 'customer.subscription.created'],
            ).order_by('-stripe_event_created_at').first()

            if not event_summary or not event_summary.stripe_subscription_id:
                return Response(
                    {'error': 'No active subscription found for this enterprise'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Check if subscription is active
            if event_summary.subscription_status not in ['active', 'trialing']:
                return Response(
                    {'error': 'No active subscription found for this enterprise'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Check if NOT scheduled for cancellation
            if not event_summary.subscription_cancel_at:
                return Response(
                    {'error': 'Subscription is not currently scheduled for cancellation'},
                    status=status.HTTP_409_CONFLICT
                )

            # Check if the period has already ended
            period_has_ended = False
            if event_summary.subscription_period_end:
                period_has_ended = event_summary.subscription_period_end.timestamp() <= timezone.now().timestamp()
            if period_has_ended:
                return Response(
                    {'error': 'The subscription period has already ended'},
                    status=status.HTTP_409_CONFLICT
                )

            # Verify reinstatement eligibility by checking if subscription price matches SSP products
            # Get subscription from Stripe to check eligibility
            subscription = stripe.Subscription.retrieve(event_summary.stripe_subscription_id)
            if not self._is_subscription_cancellable(subscription):
                return Response(
                    {'error': 'Subscription reinstatement is not available for your plan type'},
                    status=status.HTTP_403_FORBIDDEN
                )

            # Reinstate the subscription
            updated_subscription = stripe.Subscription.modify(
                event_summary.stripe_subscription_id,
                cancel_at_period_end=False,
            ).to_dict()

            # Build response with updated subscription data
            sub_data = {
                'id': updated_subscription.get('id'),
                'status': updated_subscription.get('status'),
                'cancel_at_period_end': updated_subscription.get('cancel_at_period_end', False),
                'current_period_end': updated_subscription.get('current_period_end'),
                'product_type': self._get_product_type_from_checkout_intent(checkout_intent),
                'academy_name': getattr(checkout_intent.ssp_product, 'academy_title', None),
            }

            serializer = serializers.StripeSubscriptionResponseSerializer(data=sub_data)
            serializer.is_valid(raise_exception=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except stripe.error.InvalidRequestError as e:
            logger.warning(f'Invalid Stripe request for enterprise {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': 'Invalid request to Stripe API'},
                status=status.HTTP_400_BAD_REQUEST
            )
        except stripe.error.StripeError as e:
            logger.exception(f'Stripe API error reinstating subscription for {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': f'Stripe API error: {str(e)}'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.exception(f'Unexpected error reinstating subscription for {enterprise_uuid}: {str(e)}')
            return Response(
                {'error': 'An unexpected error occurred'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )


LEARNER_CREDIT_TOP_UP_API_TAG = 'Learner Credit Top-Ups'


def _top_up_error_response(key, status_code, **message_kwargs):
    """
    Build a top-up error response: ``{"error_code": ..., "detail": ...}``.
    """
    error_code, message = TOP_UP_ERRORS[key]
    return Response({'error_code': error_code, 'detail': message.format(**message_kwargs)}, status=status_code)


class LearnerCreditTopUpViewSet(viewsets.ViewSet):
    """
    Viewset for Learner Credit top-ups. Today it only exposes the eligibility check; the quote and
    pay endpoints (chunks 7 and 11) will be added here and must reuse ``evaluate_top_up_eligibility_for_policies``.

    Like the billing-management endpoints, authorization is edx-rbac keyed on the
    ``enterprise_customer_uuid`` query parameter.
    """
    authentication_classes = (JwtAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def initial(self, request, *args, **kwargs):
        """
        With ``FEATURE_TOP_UP_LEARNER_CREDIT`` off the endpoints do not exist (404), regardless of the caller's role.
        """
        super().initial(request, *args, **kwargs)
        if not is_top_up_enabled():
            raise Http404

    @extend_schema(
        tags=[LEARNER_CREDIT_TOP_UP_API_TAG],
        summary='Check whether budgets are eligible for a Learner Credit top-up.',
        description=(
            'Returns one result per active budget of the enterprise, or only the requested budget when '
            '``policy_uuid`` is given. Ineligibility is reported in the body (200), not as an error status.'
        ),
        parameters=[
            OpenApiParameter(
                name='enterprise_customer_uuid', type=OpenApiTypes.UUID, location=OpenApiParameter.QUERY,
                required=True, description='UUID of the enterprise customer',
            ),
            OpenApiParameter(
                name='policy_uuid', type=OpenApiTypes.UUID, location=OpenApiParameter.QUERY,
                required=False, description='Restrict the result to this budget (policy).',
            ),
        ],
        responses={
            status.HTTP_200_OK: serializers.TopUpEligibilityResponseSerializer,
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(description='Invalid policy_uuid'),
            status.HTTP_403_FORBIDDEN: OpenApiResponse(description='Permission denied'),
            status.HTTP_404_NOT_FOUND: OpenApiResponse(
                description='Feature off; ``budget_not_found`` (policy_uuid not in this enterprise); or '
                            '``no_budgets_found`` (the enterprise has no active budgets).'
            ),
            status.HTTP_503_SERVICE_UNAVAILABLE: OpenApiResponse(
                description='enterprise-subsidy failed (e.g. a 5xx), or every listed budget\'s subsidy answered '
                            '403/404. A budget whose subsidy alone answers 403/404 reports ``budget_not_active``.'
            ),
        },
    )
    # ``/api-docs/`` is rendered by drf-yasg, which ignores ``extend_schema``; declare the query parameters there too.
    @api_doc_schema(parameters=[
        query_parameter('enterprise_customer_uuid', str, 'UUID of the enterprise customer (required)'),
        query_parameter('policy_uuid', str, 'Restrict the result to this budget (policy) UUID (optional)'),
    ])
    @action(detail=False, methods=['get'], url_path='eligibility')
    @permission_required(
        LEARNER_CREDIT_TOP_UP_PERMISSION,
        fn=lambda request, **kwargs: request.GET.get('enterprise_customer_uuid')
    )
    def eligibility(self, request, **kwargs):
        """
        Evaluate top-up eligibility for the enterprise's budgets.
        """
        params = serializers.TopUpEligibilityQueryParamsSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        enterprise_customer_uuid = params.validated_data['enterprise_customer_uuid']
        policy_uuid = params.validated_data.get('policy_uuid')

        policies = SubsidyAccessPolicy.objects.filter(enterprise_customer_uuid=enterprise_customer_uuid)
        if policy_uuid:
            # An inactive budget, or a policy of another enterprise, is simply not found.
            policies = policies.filter(uuid=policy_uuid, active=True)
            if not policies.exists():
                return _top_up_error_response(
                    'BUDGET_NOT_FOUND', status.HTTP_404_NOT_FOUND,
                    policy_uuid=policy_uuid, enterprise_customer_uuid=enterprise_customer_uuid,
                )
        else:
            # Every ``active=True`` policy; a retired one that is still active reports ``budget_not_active``.
            policies = policies.filter(active=True)
            if not policies.exists():
                # Also what an operator (who may query any enterprise) sees for a mistyped enterprise_customer_uuid.
                return _top_up_error_response(
                    'NO_BUDGETS_FOUND', status.HTTP_404_NOT_FOUND, enterprise_customer_uuid=enterprise_customer_uuid,
                )

        try:
            # A budget whose subsidy alone answers 403/404 reports ``budget_not_active``. When listing, every
            # subsidy failing that way is treated as an outage; an explicitly requested budget just reports it.
            results = evaluate_top_up_eligibility_for_policies(
                policies.order_by('created'), raise_if_all_unreachable=not policy_uuid,
            )
        except OSError as exc:  # SubsidyAPIHTTPError and requests' connection errors and timeouts are OSErrors
            logger.exception('Top-up eligibility: subsidy unavailable for enterprise %s', enterprise_customer_uuid)
            raise SubsidyAPIUnavailableException() from exc
        return Response(serializers.TopUpEligibilityResponseSerializer({'results': results}).data)
