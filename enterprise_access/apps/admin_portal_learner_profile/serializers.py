"""
Serializers for the admin_portal_learner_profile app.
"""
import logging

from rest_framework import serializers

logger = logging.getLogger(__name__)


# pylint: disable=abstract-method
class AdminLearnerProfileRequestSerializer(serializers.Serializer):
    """Serializer for validating admin learner profile query parameters."""
    user_email = serializers.EmailField(required=True, help_text="The email address of the learner.")
    lms_user_id = serializers.IntegerField(required=True, help_text="The ID of the LMS user.")
    enterprise_customer_uuid = serializers.UUIDField(required=True, help_text="The UUID of the enterprise customer.")

    def validate(self, attrs):
        """Ensure all required fields are provided."""
        if not attrs.get('enterprise_customer_uuid'):
            raise serializers.ValidationError("enterprise_customer_uuid is required.")
        if not attrs.get('user_email') or not attrs.get('lms_user_id'):
            raise serializers.ValidationError("Both user_email and lms_user_id are required.")
        return attrs


# pylint: disable=abstract-method
class AdminLearnerProfileResponseSerializer(serializers.Serializer):
    """Serializer for structuring the admin learner profile response."""
    subscriptions = serializers.ListField(help_text="Details of the learner's subscription licenses.", required=False)
    group_memberships = serializers.ListField(help_text="Flex group memberships for the learner.", required=False)
    enrollments = serializers.DictField(
        child=serializers.ListField(),
        help_text="Course enrollments for the learner.",
        required=False
    )
