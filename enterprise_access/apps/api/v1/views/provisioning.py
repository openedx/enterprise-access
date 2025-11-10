"""
Rest API views for the browse and request app.
"""
import copy
import logging

from django.conf import settings
from drf_spectacular.utils import extend_schema
from edx_rbac.mixins import PermissionRequiredMixin
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import exceptions, generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from enterprise_access.apps.api import serializers
from enterprise_access.apps.api_client.license_manager_client import LicenseManagerApiClient
from enterprise_access.apps.core import constants
from enterprise_access.apps.customer_billing.models import CheckoutIntent
from enterprise_access.apps.provisioning.models import (
    GetCreateFirstPaidSubscriptionPlanStep,
    ProvisionNewCustomerWorkflow
)
from enterprise_access.apps.workflow.exceptions import UnitOfWorkException

logger = logging.getLogger(__name__)

PROVISIONING_API_TAG = 'Provisioning'


class ProvisioningException(exceptions.APIException):
    """
    General provisioning-related API exception.
    """
    status_code = 422
    default_detail = 'Could not execute this provisioning request'
    default_code = 'provisioning_error'


@extend_schema(
    tags=[PROVISIONING_API_TAG],
    summary='Create a new provisioning request.',
    request=serializers.ProvisioningRequestSerializer,
    responses={
        status.HTTP_200_OK: serializers.ProvisioningResponseSerializer,
        status.HTTP_201_CREATED: serializers.ProvisioningResponseSerializer,
    },
)
class ProvisioningCreateView(PermissionRequiredMixin, generics.CreateAPIView):
    """
    Create view for provisioning.
    """
    authentication_classes = (JwtAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)
    permission_required = constants.PROVISIONING_CREATE_PERMISSION

    def _transform_legacy_request_data(self, request_data: dict) -> dict:
        """
        Transform legacy request format to support backward compatibility.

        Converts old `subscription_plan` key to new `trial_subscription_plan` and
        `first_paid_subscription_plan` keys if the old format is detected.

        Args:
            request_data (dict): The incoming request data

        Returns:
            dict: Transformed request data with new keys
        """
        # If new format is already present, return as-is.
        if 'trial_subscription_plan' in request_data and 'first_paid_subscription_plan' in request_data:
            return request_data

        # If old format is present, transform it.
        if 'subscription_plan' in request_data:
            logger.warning(
                'Deprecated request format detected: `subscription_plan` key should be replaced with '
                '`trial_subscription_plan` and `first_paid_subscription_plan`'
            )

            subscription_plan = request_data.pop('subscription_plan')

            # Use the subscription_plan data as trial_subscription_plan
            request_data['trial_subscription_plan'] = subscription_plan

            # Synthesize first_paid_subscription_plan with required fields only
            request_data['first_paid_subscription_plan'] = {
                'title': f"{subscription_plan.get('title', 'Subscription')} - First Paid Plan",
                'product_id': settings.PROVISIONING_PAID_SUBSCRIPTION_PRODUCT_ID,
                'salesforce_opportunity_line_item': None,
            }

            logger.info(
                'Transformed legacy subscription_plan to trial_subscription_plan and '
                'synthesized first_paid_subscription_plan'
            )
            logger.info(
                'Transformed request payload: %s',
                str(request_data),
            )
            return request_data

        # If neither format detected, passthrough to serializer to inevitably fail validation and return HTTP 400.
        return request_data

    def create(self, request, *args, **kwargs):
        # TEMP: Transform legacy request data before serialization. See docstring.
        transformed_data = self._transform_legacy_request_data(copy.deepcopy(dict(request.data)))

        request_serializer = serializers.ProvisioningRequestSerializer(data=transformed_data)
        request_serializer.is_valid(raise_exception=True)

        customer_request_data = request_serializer.validated_data['enterprise_customer']
        admin_emails = [
            record.get('user_email')
            for record in request_serializer.validated_data['pending_admins']
        ]
        catalog_request_data = request_serializer.validated_data.get('enterprise_catalog')
        customer_agreement_data = request_serializer.validated_data.get('customer_agreement')
        trial_subscription_plan_data = request_serializer.validated_data['trial_subscription_plan']
        first_paid_subscription_plan_data = request_serializer.validated_data['first_paid_subscription_plan']

        workflow_input_dict = ProvisionNewCustomerWorkflow.generate_input_dict(
            customer_request_data,
            admin_emails,
            catalog_request_data,
            customer_agreement_data,
            trial_subscription_plan_data,
            first_paid_subscription_plan_data,
        )
        workflow = ProvisionNewCustomerWorkflow.objects.create(input_data=workflow_input_dict)

        try:
            workflow.execute()
        except UnitOfWorkException as exc:
            raise ProvisioningException(
                detail=f'Error in provisioning workflow: {exc}',
                code=exc.code,
            ) from exc

        response_serializer = serializers.ProvisioningResponseSerializer({
            'enterprise_customer': workflow.customer_output_dict(),
            'customer_admins': workflow.admin_users_output_dict(),
            'enterprise_catalog': workflow.catalog_output_dict(),
            'customer_agreement': workflow.customer_agreement_output_dict(),
            'trial_subscription_plan': workflow.trial_subscription_plan_output_dict(),
            'first_paid_subscription_plan': workflow.first_paid_subscription_plan_output_dict(),
            'subscription_plan_renewal': workflow.subscription_plan_renewal_output_dict(),
        })
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    tags=[PROVISIONING_API_TAG],
    summary='Update a SubscriptionPlan with Salesforce Opportunity Line Item.',
    request=serializers.SubscriptionPlanOLIUpdateSerializer,
    responses={
        status.HTTP_200_OK: serializers.SubscriptionPlanOLIUpdateResponseSerializer,
    },
)
class SubscriptionPlanOLIUpdateView(PermissionRequiredMixin, APIView):
    """
    Update a SubscriptionPlan's Salesforce Opportunity Line Item.
    Called by Salesforce when the paid OLI is created after initial provisioning.
    """
    authentication_classes = (JwtAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)
    permission_required = constants.PROVISIONING_CREATE_PERMISSION

    def post(self, request):
        """
        Update a subscription plan with Salesforce OLI.
        """
        serializer = serializers.SubscriptionPlanOLIUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            if serializer.validated_data.get('checkout_intent_uuid'):
                kwargs = {'uuid': serializer.validated_data['checkout_intent_uuid']}
            else:
                kwargs = {'id': serializer.validated_data['checkout_intent_id']}
            checkout_intent = CheckoutIntent.objects.get(**kwargs)
        except CheckoutIntent.DoesNotExist as exc:
            logger.error(f"CheckoutIntent not found: {kwargs}")
            raise exceptions.NotFound(f"CheckoutIntent {kwargs} not found") from exc

        if not checkout_intent.workflow:
            logger.error(f"No workflow found for CheckoutIntent {checkout_intent.uuid}")
            raise exceptions.ValidationError(
                f"CheckoutIntent {checkout_intent.uuid} has no associated workflow"
            )

        # Find the subscription plan step
        subscription_steps = GetCreateFirstPaidSubscriptionPlanStep.objects.filter(
            workflow_record_uuid=checkout_intent.workflow.uuid
        )
        target_product_id = settings.PROVISIONING_PAID_SUBSCRIPTION_PRODUCT_ID
        is_trial = serializer.validated_data.get('is_trial', False)
        if is_trial:
            raise NotImplementedError('Modifying trial plans not supported.')

        # Filter for paid plan based on input
        subscription_plan_uuid = None
        for step in subscription_steps:
            if step.input_data and step.input_data['product_id'] == target_product_id:
                if step.output_object:
                    subscription_plan_uuid = step.output_object.uuid
                    logger.info(
                        f"Found subscription plan UUID {subscription_plan_uuid} via workflow "
                        f"for CheckoutIntent {checkout_intent.uuid}"
                    )
                    break

        if not subscription_plan_uuid:
            logger.error(
                f"No subscription plan found for CheckoutIntent {checkout_intent.uuid} "
                f"with is_trial={is_trial}"
            )
            raise exceptions.NotFound(
                f"No subscription plan found for CheckoutIntent {checkout_intent.uuid}"
            )

        # Call License Manager API to update the plan
        license_manager_client = LicenseManagerApiClient()

        try:
            salesforce_oli = serializer.validated_data['salesforce_opportunity_line_item']
            license_manager_client.update_subscription_plan(
                subscription_uuid=str(subscription_plan_uuid),
                salesforce_opportunity_line_item=salesforce_oli,
            )

            logger.info(
                f"Successfully updated subscription plan {subscription_plan_uuid} "
                f"with Salesforce OLI {salesforce_oli}"
            )

            response_serializer = serializers.SubscriptionPlanOLIUpdateResponseSerializer({
                'success': True,
                'subscription_plan_uuid': subscription_plan_uuid,
                'salesforce_opportunity_line_item': salesforce_oli,
                'checkout_intent_uuid': checkout_intent.uuid,
                'checkout_intent_id': checkout_intent.id,
            })
            return Response(response_serializer.data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(
                f"Failed to update subscription plan {subscription_plan_uuid} "
                f"with OLI {salesforce_oli}: {str(e)}"
            )
            raise exceptions.APIException(
                detail=f"Failed to update subscription plan: {str(e)}"
            )
