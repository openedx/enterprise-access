"""
Serializers for the `AssignmentConfiguration` model.
"""
import logging

from rest_framework import serializers

from enterprise_access.apps.content_assignments.models import AssignmentConfiguration
from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy

logger = logging.getLogger(__name__)


class AssignmentConfigurationResponseSerializer(serializers.ModelSerializer):
    """
    A read-only Serializer for responding to requests for ``AssignmentConfiguration`` records.
    """
    # This causes the related SubsidyAccessPolicy to be serialized as a UUID (in the response).
    subsidy_access_policy = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = AssignmentConfiguration
        fields = [
            'uuid',
            'subsidy_access_policy',
            'enterprise_customer_uuid',
            'active',
        ]
        read_only_fields = fields


class AssignmentConfigurationCreateRequestSerializer(serializers.ModelSerializer):
    """
    Serializer to validate request data for create() (POST) operations.
    """
    # This causes field validation to check for a UUID (in the request) and validates that a SubsidyAccessPolicy
    # actually exists with that UUID.
    subsidy_access_policy = serializers.PrimaryKeyRelatedField(queryset=SubsidyAccessPolicy.objects.all())

    class Meta:
        model = AssignmentConfiguration
        fields = [
            'uuid',
            'subsidy_access_policy',
            'active',
        ]
        read_only_fields = [
            'uuid',
            'active',
        ]
        extra_kwargs = {
            'uuid': {'read_only': True},
            'subsidy_access_policy': {
                'allow_null': False,
                'required': True,
            },
            'active': {'read_only': True},
        }

    @property
    def calling_view(self):
        """
        Return the view that called this serializer.
        """
        return self.context['view']

    def create(self, validated_data):
        """
        Get or create or reactivate an AssignmentConfiguration object.
        """
        # First, search for any AssignmentConfigs that share the requested SubsidyAccessPolicy, and return that if found
        # (activating it if necessary). We will essentially treat the 'subsidy_access_policy' as the idempotency key for
        # de-duplication purposes.
        existing_subsidy_access_policy = validated_data['subsidy_access_policy']
        found_assignment_config = existing_subsidy_access_policy.assignment_configuration
        if found_assignment_config:
            if not found_assignment_config.active:
                found_assignment_config.active = True
                found_assignment_config.save()
            self.calling_view.set_assignment_config_created(False)
            return found_assignment_config
        self.calling_view.set_assignment_config_created(True)

        # Copy the enterprise customer UUID from the SubsidyAccessPolicy into the new AssignmentConfiguration object.
        validated_data['enterprise_customer_uuid'] = existing_subsidy_access_policy.enterprise_customer_uuid

        # Actually create the new AssignmentConfiguration.
        new_assignment_config = super().create(validated_data)

        # Manually link the new AssignmentConfiguration to the existing SubsidyAccessPolicy.  For some reason this
        # reverse relationship is not automatically created by virtue of validated_data having the
        # 'subsidy_access_policy' key.
        existing_subsidy_access_policy.assignment_configuration = new_assignment_config
        existing_subsidy_access_policy.save()

        return new_assignment_config

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
