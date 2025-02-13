"""
Rest API views for the browse and request app.
"""
import logging

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from drf_spectacular.utils import extend_schema, extend_schema_view
from edx_rbac.decorators import permission_required
from edx_rbac.mixins import PermissionRequiredMixin
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from requests.exceptions import ConnectionError as RequestConnectionError
from requests.exceptions import HTTPError, Timeout
from rest_framework import filters, generics, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from enterprise_access.apps.api import serializers
from enterprise_access.apps.core import constants

logger = logging.getLogger(__name__)


class ProvisioningCreateView(PermissionRequiredMixin, generics.CreateAPIView):
    """
    Create view for provisioning.
    """
    authentication_classes = (JwtAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = serializers.ProvisioningRequestSerializer
    permission_required = constants.PROVISIONING_CREATE_PERMISSION

    def create(self, request, *args, **kwargs):
        return Response('Created asdf;lkajsdf;', status=status.HTTP_201_CREATED)
