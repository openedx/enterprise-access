"""
Rest API views for the browse and request app.
"""
import logging

import requests
from drf_spectacular.utils import extend_schema
from edx_rbac.mixins import PermissionRequiredMixin
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import exceptions, generics, permissions, status
from rest_framework.response import Response

from enterprise_access.apps.api import serializers
from enterprise_access.apps.core import constants
from enterprise_access.apps.provisioning import api as provisioning_api

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
        try:
            created_customer = provisioning_api.get_or_create_enterprise_customer(
                name=customer_request_data['name'],
                country=customer_request_data['country'],
                slug=customer_request_data['slug'],
            )
        except requests.exceptions.HTTPError as exc:
            raise ProvisioningException(
                detail=f'Error get/creating customer record: {exc}',
                code='customer_provisioning_error',
            ) from exc

        admin_emails = [
            record.get('user_email')
            for record in request_serializer.validated_data['pending_admins']
        ]

        try:
            customer_admins = provisioning_api.get_or_create_enterprise_admin_users(
                enterprise_customer_uuid=created_customer['uuid'],
                user_emails=admin_emails,
            )
        except requests.exceptions.HTTPError as exc:
            raise ProvisioningException(
                detail=f'Error get/creating admin records: {exc}',
                code='admin_provisioning_error',
            ) from exc

        response_serializer = serializers.ProvisioningResponseSerializer({
            'enterprise_customer': created_customer,
            'pending_admins': customer_admins,
        })
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )
