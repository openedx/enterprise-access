"""
Serializers for the `LearnerContentAssignment` model.
"""
import logging

from rest_framework import serializers

from enterprise_access.apps.content_assignments.models import LearnerContentAssignment

logger = logging.getLogger(__name__)


class LearnerContentAssignmentResponseSerializer(serializers.ModelSerializer):
    """
    A read-only Serializer for responding to requests for ``LearnerContentAssignment`` records.
    """
    # This causes the related AssignmentConfiguration to be serialized as a UUID (in the response).
    assignment_configuration = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = LearnerContentAssignment
        fields = [
            'uuid',
            'assignment_configuration',
            'learner_email',
            'lms_user_id',
            'content_key',
            'content_quantity',
            'state',
            'transaction_uuid',
            'last_notification_at',
        ]
        read_only_fields = fields
