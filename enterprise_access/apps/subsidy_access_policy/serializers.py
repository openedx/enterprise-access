"""
Serializers for subsidy_access_policy app.
"""

from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from rest_framework import serializers


class PolicyRedeemRequestSerializer(serializers.Serializer):
    group_uuid = serializers.UUIDField(required=True)
    learner_id = serializers.IntegerField(required=True)
    content_key = serializers.CharField(required=True)

    def validate_content_key(self, value):
        """
        Validate `content_key`.
        """
        try:
            CourseKey.from_string(value)
        except InvalidKeyError:
            raise serializers.ValidationError(f"Invalid course key: {value}")

        return value
