"""
REST API views for the Stripe PoC.
"""
import json
import logging

import requests
import stripe
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema
from edx_rbac.decorators import permission_required
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from enterprise_access.apps.api import serializers
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.core.constants import (
    CUSTOMER_BILLING_CREATE_PLAN_PERMISSION,
    CUSTOMER_BILLING_CREATE_PORTAL_SESSION_PERMISSION
)

stripe.api_key = settings.STRIPE_API_KEY
logger = logging.getLogger(__name__)

CUSTOMER_BILLING_API_TAG = 'Customer Billing'


class CustomerBillingStripeWebHookView(viewsets.ViewSet):
    """
    Viewset supporting the Stripe WebHook to receive events.
    """
    # This unauthenticated endpoint will rely on view logic to perform authentication via signature validation.
    permission_classes = (permissions.AllowAny,)

    @extend_schema(
        tags=[CUSTOMER_BILLING_API_TAG],
        summary='Listen for events from Stripe.',
    )
    @action(detail=False, methods=['post'])
    @csrf_exempt
    def stripe_webhook(self, request, *args, **kwargs):
        """
        Listen for events from Stripe, and take specific actions. Typically the action is to send a confirmation email.

        PoC Notes:
        * For a real production implementation we should implement signature validation:
          - https://docs.stripe.com/webhooks/signature
          - This endpoint is un-authenticated, so the only defense we have against spoofed events is signature
            validation.
        * For a real production implementation we should implement event de-duplication:
          - https://docs.stripe.com/webhooks/process-undelivered-events
          - This is a safeguard against the remote possibility that an event is sent twice. This could happen if the
            network connection cuts out at the exact moment between successfully processing an event and responding with
            HTTP 200, in which case Stripe will attemt to re-send the event since it does not know we successfully
            received it.
        """
        payload = request.body
        event = None

        try:
            event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)
        except ValueError:
            return Response(
                'Stripe WebHook event payload was invalid.',
                status=status.HTTP_400_BAD_REQUEST,
            )

        event_type = event["type"]
        logger.info(f'Received Stripe event: {event_type}')

        if event_type == 'invoice.paid':
            pass
        elif event_type == 'customer.subscription.trial_will_end':
            pass
        elif event_type == 'payment_method.attached':
            pass
        elif event_type == 'customer.subscription.deleted':
            pass

        return Response(status=status.HTTP_200_OK)


class CustomerBillingViewSet(viewsets.ViewSet):
    """
    Viewset supporting all operations pertaining to customer billing.
    """
    permission_classes = (permissions.IsAuthenticated,)
    authentication_classes = (JwtAuthentication,)

    @extend_schema(
        tags=[CUSTOMER_BILLING_API_TAG],
        summary='Create a new billing plan given form data from a prospective customer, and return an invoice.',
        request=serializers.CustomerBillingCreatePlanRequestSerializer,
    )
    @action(detail=False, methods=['post'])
    @permission_required(CUSTOMER_BILLING_CREATE_PLAN_PERMISSION)
    def create_plan(self, request, *args, **kwargs):
        """
        Create a new billing plan (as a free trial).  Response dict is a pass-through Stripe Checkout Session object.

        Response structure defined here: https://docs.stripe.com/api/checkout/sessions/create
        """
        serializer = serializers.CustomerBillingCreatePlanRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        validated_data = serializer.validated_data
        form_email = validated_data['email']
        form_slug = validated_data['slug']
        form_num_licenses = validated_data['num_licenses']
        form_stripe_price_id = validated_data['stripe_price_id']

        lms_client = LmsApiClient()

        # First, try to get the enterprise customer data. For this PoC, I'm not prepared to support existing customers,
        # so block the request if that happens.
        enterprise_customer_data = lms_client.get_enterprise_customer_data(enterprise_customer_slug=form_slug)
        if enterprise_customer_data:
            message = f'Existing customer found for slug {form_slug}. Cannot create plan.'
            logger.warning(message)
            return Response(message, status=status.HTTP_403_FORBIDDEN)
        else:
            logger.info(f'No existing customer found for slug {form_slug}. Creating plan.')

        # Eagerly find an existing Stripe customer if one already exists with the same email.
        stripe_customer_search_result = stripe.Customer.search(query=f"email: '{form_email}'")
        found_stripe_customer_by_email = next(iter(stripe_customer_search_result['data']), None)

        checkout_session = stripe.checkout.Session.create(
            # Passing None to ``customer`` causes Stripe to create a new one, so try first to use an existing customer.
            customer=found_stripe_customer_by_email['id'] if found_stripe_customer_by_email else None,
            mode="subscription",
            # Avoid needing to create custom frontends for PoC by using a hosted checkout page.
            ui_mode="custom",
            # This normally wouldn't work because the customer doesn't exist yet --- I'd propose we modify the admin
            # portal to support an empty state with a message like "turning cogs, check back later." if there's no
            # Enterprise Customer but there is a Stripe Customer.
            return_url=f"https://portal.edx.org/{form_slug}",
            line_items=[{
                "price": form_stripe_price_id,
                "quantity": form_num_licenses,
            }],
            # Defer payment collection until the last moment, then cancel
            # the subscription if payment info has not been submitted.
            subscription_data={
                "trial_period_days": 7,
                "trial_settings": {
                    "end_behavior": {"missing_payment_method": "cancel"},
                },
            },
        )
        return Response(checkout_session, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=[CUSTOMER_BILLING_API_TAG],
        summary='Create a new Customer Portal Session.',
    )
    @action(detail=True, methods=['get'])
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

        return Response(customer_portal_session, status=status.HTTP_200_OK)
