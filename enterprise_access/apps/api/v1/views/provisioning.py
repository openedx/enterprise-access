"""
Rest API views for the browse and request app.
"""
import logging

from drf_spectacular.utils import extend_schema
from edx_rbac.mixins import PermissionRequiredMixin
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import exceptions, generics, permissions, status
from rest_framework.response import Response

from enterprise_access.apps.api import serializers
from enterprise_access.apps.core import constants
from enterprise_access.apps.provisioning.models import ProvisionNewCustomerWorkflow
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

    def create(self, request, *args, **kwargs):
        request_serializer = serializers.ProvisioningRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)

        customer_request_data = request_serializer.validated_data['enterprise_customer']
        admin_emails = [
            record.get('user_email')
            for record in request_serializer.validated_data['pending_admins']
        ]
        catalog_request_data = request_serializer.validated_data['enterprise_catalog']
        customer_agreement_data = request_serializer.validated_data['customer_agreement']
        subscription_plan_data = request_serializer.validated_data['subscription_plan']

        workflow_input_dict = ProvisionNewCustomerWorkflow.generate_input_dict(
            customer_request_data, admin_emails, catalog_request_data, customer_agreement_data, subscription_plan_data
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
            'subscription_plan': workflow.subscription_plan_output_dict(),
        })
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )
