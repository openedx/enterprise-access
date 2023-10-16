"""
Serializers for the `AssignmentConfiguration` model.
"""
import logging

from rest_framework import serializers

from enterprise_access.apps.content_assignments.models import AssignmentConfiguration

logger = logging.getLogger(__name__)


class AssignmentConfigurationResponseSerializer(serializers.ModelSerializer):
    """
    A read-only Serializer for responding to requests for ``AssignmentConfiguration`` records.
    """
    subsidy_access_policy = serializers.SerializerMethodField(
        help_text="The Assignment-based access policy related to this assignment configuration, if any.",
    )

    class Meta:
        model = AssignmentConfiguration
        fields = [
            'uuid',
            'subsidy_access_policy',
            'enterprise_customer_uuid',
            'active',
        ]
        read_only_fields = fields

    def get_subsidy_access_policy(self, obj):
        """
        Returns a string-ified policy UUID for this assignment configuration, if one exists.
        """
        if policy := obj.policy:
            return str(policy.uuid)
        return None


class AssignmentConfigurationCreateRequestSerializer(serializers.ModelSerializer):
    """
    Serializer to validate request data for create() (POST) operations.
    """
    class Meta:
        model = AssignmentConfiguration
        fields = [
            'uuid',
            'enterprise_customer_uuid',
            'active',
        ]
        read_only_fields = [
            'uuid',
            'active',
        ]
        extra_kwargs = {
            'uuid': {'read_only': True},
            'active': {'read_only': True},
        }

    def to_representation(self, instance):
        """
        Once an AssignmentConfiguration has been created, we want to serialize more fields from the instance than are
        required in this, the input serializer.
        """
        read_serializer = AssignmentConfigurationResponseSerializer(instance)
        return read_serializer.data


# pylint: disable=abstract-method
class AssignmentConfigurationDeleteRequestSerializer(serializers.Serializer):
    """
    Request Serializer for DELETE parameters to an API call to deactivate an AssignmentConfiguration.

    For view: AssignmentConfigurationViewSet.destroy
    """
    reason = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="Optional description (free form text) for why the AssignmentConfiguration is being deactivated.",
    )


class AssignmentConfigurationUpdateRequestSerializer(serializers.ModelSerializer):
    """
    Request Serializer for PUT or PATCH requests to update an AssignmentConfiguration.

    For views: AssignmentConfigurationViewSet.update and AssignmentConfigurationViewSet.partial_update.
    """
    class Meta:
        model = AssignmentConfiguration
        fields = (
            'active',
        )
        extra_kwargs = {
            'active': {
                'allow_null': False,
                'required': False,
            },
        }

    def validate(self, attrs):
        """
        Raises a ValidationError if any field not explicitly declared as a field in this serializer definition is
        provided as input.
        """
        unknown = sorted(set(self.initial_data) - set(self.fields))
        if unknown:
            raise serializers.ValidationError("Field(s) are not updatable: {}".format(", ".join(unknown)))
        return attrs

    def to_representation(self, instance):
        """
        Once an AssignmentConfiguration has been updated, we want to serialize more fields from the instance than are
        required in this, the input serializer.
        """
        read_serializer = AssignmentConfigurationResponseSerializer(instance)
        return read_serializer.data
