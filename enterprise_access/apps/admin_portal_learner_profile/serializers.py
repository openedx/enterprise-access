"""
Serializers for the admin_portal_learner_profile app.
"""
import logging

from rest_framework import serializers

logger = logging.getLogger(__name__)


class ErrorOrField(serializers.Field):
    """
    A field that wraps an existing field (base_field) to allow either its normal value
    or an error dictionary in the form {"error": "error text"}.
    """
    def __init__(self, base_field, **kwargs):
        self.base_field = base_field
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        '''
        Validate the input data. If it's an error dictionary, return it as-is.'
        '''
        # If the value is an error dictionary, validate its structure.
        if isinstance(data, dict) and "error" in data:
            return data

        # Otherwise, delegate validation to the base field.
        return self.base_field.to_internal_value(data)

    def to_representation(self, value):
        '''
        Serialize the value. If it's an error dictionary, return it as-is.'
        '''
        # If the value is already an error dict, return as-is.
        if isinstance(value, dict) and "error" in value:
            return value

        # Otherwise, let the base field handle serialization.
        return self.base_field.to_representation(value)


class AdminLearnerProfileRequestSerializer(serializers.Serializer):
    """Serializer for validating admin learner profile query parameters."""
    user_email = serializers.EmailField(required=True, help_text="The email address of the learner.")
    lms_user_id = serializers.IntegerField(required=True, help_text="The ID of the LMS user.")
    enterprise_customer_uuid = serializers.UUIDField(required=True, help_text="The UUID of the enterprise customer.")


class AdminLearnerProfileResponseSerializer(serializers.Serializer):
    """Serializer for structuring the admin learner profile response."""
    subscriptions = ErrorOrField(
        base_field=serializers.ListField(
            help_text="Details of the learner's subscription licenses.",
            required=False
        ),
        required=False
    )
    group_memberships = ErrorOrField(
        base_field=serializers.ListField(
            help_text="Flex group memberships for the learner.",
            required=False
        ),
        required=False
    )
    enrollments = ErrorOrField(
        base_field=serializers.DictField(
            child=serializers.ListField(),
            help_text="Course enrollments for the learner.",
            required=False
        ),
        required=False
    )
