"""
Rest API views for the browse and request app.
"""
import logging

from drf_spectacular.utils import extend_schema
from edx_rbac.mixins import PermissionRequiredMixin
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import generics, permissions, status
from rest_framework.response import Response

from enterprise_access.apps.api import serializers
from enterprise_access.apps.core import constants

logger = logging.getLogger(__name__)

PROVISIONING_API_TAG = 'Provisioning'


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
        return Response('ack', status=status.HTTP_201_CREATED)
