"""
Serializers for the provisioning app.
"""
import logging

from django.conf import settings
from django_countries.serializer_fields import CountryField

from drf_spectacular.utils import extend_schema_field
from requests.exceptions import HTTPError
from rest_framework import serializers

logger = logging.getLogger(__name__)


## All the REQUEST serializers go under here ##


class EnterpriseCustomerRequestSerializer(serializers.Serializer):
    """
    """
    name = serializers.CharField()
    country = CountryField()
    slug = serializers.SlugField(required=False, allow_blank=True)


class PendingCustomerAdminRequestSerializer(serializers.Serializer):
    """
    """
    user_email = serializers.EmailField()


class ProvisioningRequestSerializer(serializers.Serializer):
    """
    """
    enterprise_customer = EnterpriseCustomerRequestSerializer()
    pending_admins = PendingCustomerAdminRequestSerializer(many=True)
    

## All the RESPONSE serializers go under here ##


class EnterpriseCustomerResponseSerializer(serializers.Serializer):
    """
    """
    uuid = serializers.UUIDField()
    name = serializers.CharField()
    country = CountryField()
    slug = serializers.SlugField(required=False, allow_blank=True)


class PendingCustomerAdminResponseSerializer(serializers.Serializer):
    """
    """
    user_email = serializers.EmailField()


class ProvisioningResponseSerializer(serializers.Serializer):
    """
    """
    enterprise_customer = EnterpriseCustomerResponseSerializer()
    pending_admins = PendingCustomerAdminResponseSerializer(many=True)
