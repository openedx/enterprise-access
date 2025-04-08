"""
Serializers for the provisioning app.
"""
import logging

from django_countries.serializer_fields import CountryField
from rest_framework import serializers

logger = logging.getLogger(__name__)


class BaseSerializer(serializers.Serializer):
    """
    Base implementation for request and response serializers.
    """
    def create(self, *args, **kwargs):
        return None

    def update(self, *args, **kwargs):
        return None


## All the REQUEST serializers go under here ##


class EnterpriseCustomerRequestSerializer(BaseSerializer):
    """
    Customer object serializer for provisioning requests.
    """
    name = serializers.CharField(
        help_text='The unique name of the Enterprise Customer.',
    )
    country = CountryField(
        help_text='The two letter ISO 3166-2 ISO code representing the customer country.',
    )
    slug = serializers.SlugField(
        help_text='An optional customer slug. One will be generated if not provided.',
        required=False,
        allow_blank=True,
    )


class PendingCustomerAdminRequestSerializer(BaseSerializer):
    """
    Pending admin serializer for provisioning requests.
    """
    user_email = serializers.EmailField(
        help_text='The email address of the requested admin.',
    )


class EnterpriseCatalogRequestSerializer(BaseSerializer):
    """
    Catalog object serializer for provisioning requests.
    """
    title = serializers.CharField(
        help_text='The name of the Enterprise Catalog.',
    )
    catalog_query_id = serializers.IntegerField(
        help_text='The id of the related Catalog Query.',
    )


class ProvisioningRequestSerializer(BaseSerializer):
    """
    Request serializer for provisioning create view.
    """
    enterprise_customer = EnterpriseCustomerRequestSerializer(
        help_text='Object describing the requested Enterprise Customer.',
    )
    pending_admins = PendingCustomerAdminRequestSerializer(
        help_text='List of objects containing requested customer admin email addresses.',
        many=True,
    )
    enterprise_catalog = EnterpriseCatalogRequestSerializer(
        help_text='Object describing the requested Enterprise Catalog.',
    )


## All the RESPONSE serializers go under here ##


class EnterpriseCustomerResponseSerializer(BaseSerializer):
    """
    Customer object serializer for provisioning responses.
    """
    uuid = serializers.UUIDField()
    name = serializers.CharField()
    country = CountryField()
    slug = serializers.SlugField(required=True, allow_blank=False)


class CreatedCustomerAdminResponseSerializer(BaseSerializer):
    """
    Pending admin serializer for provisioning responses.
    """
    user_email = serializers.EmailField()


class ExistingCustomerAdminResponseSerializer(BaseSerializer):
    """
    Existing admin serializer for provisioning responses.
    """
    user_email = serializers.EmailField()


class AdminObjectResponseSerializer(BaseSerializer):
    """
    Container serializer to describe created and existing
    admin emails in a provisioning response.
    """
    created_admins = CreatedCustomerAdminResponseSerializer(many=True)
    existing_admins = ExistingCustomerAdminResponseSerializer(many=True)


class EnterpriseCatalogResponseSerializer(BaseSerializer):
    """
    Catalog object serializer for provisioning responses.
    """
    uuid = serializers.UUIDField()
    enterprise_customer_uuid = serializers.UUIDField()
    title = serializers.CharField()
    catalog_query_id = serializers.IntegerField()


class ProvisioningResponseSerializer(BaseSerializer):
    """
    Response serializer for provisioning create view.
    """
    enterprise_customer = EnterpriseCustomerResponseSerializer()
    customer_admins = AdminObjectResponseSerializer()
    enterprise_catalog = EnterpriseCatalogResponseSerializer()
