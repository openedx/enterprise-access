"""
Serializers for the `LearnerContentAssignment` model.
"""
import logging

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from enterprise_access.apps.content_assignments.constants import (
    AssignmentActions,
    AssignmentLearnerStates,
    AssignmentRecentActionTypes,
    LearnerContentAssignmentStateChoices
)
from enterprise_access.apps.content_assignments.models import LearnerContentAssignment, LearnerContentAssignmentAction

logger = logging.getLogger(__name__)


class LearnerContentAssignmentActionSerializer(serializers.ModelSerializer):
    """
    A read-only Serializer for responding to requests for ``LearnerContentAssignmentAction`` records.
    """
    class Meta:
        model = LearnerContentAssignmentAction
        fields = [
            'created',
            'modified',
            'uuid',
            'action_type',
            'completed_at',
            'error_reason',

            # Intentionally hide traceback from response, since this is primarily for developers/on-call and could
            # over-communicate secrets.
            # 'traceback',
        ]
        read_only_fields = fields


class LearnerContentAssignmentRecentActionSerializer(serializers.Serializer):
    """
    Structured data about the most recent action, meant to power a frontend table column.
    """
    action_type = serializers.ChoiceField(
        help_text='Type of the recent action.',
        choices=AssignmentRecentActionTypes.CHOICES,
        source='recent_action',
    )
    timestamp = serializers.DateTimeField(
        help_text='Date and time when the action was taken.',
        source='recent_action_time',
    )


class LearnerContentAssignmentResponseSerializer(serializers.ModelSerializer):
    """
    A read-only Serializer for responding to requests for ``LearnerContentAssignment`` records.
    """
    # This causes the related AssignmentConfiguration to be serialized as a UUID (in the response).
    assignment_configuration = serializers.PrimaryKeyRelatedField(read_only=True)

    actions = LearnerContentAssignmentActionSerializer(
        help_text='All actions associated with this assignment.',
        many=True,
    )

    class Meta:
        model = LearnerContentAssignment
        fields = [
            'uuid',
            'assignment_configuration',
            'learner_email',
            'lms_user_id',
            'content_key',
            'content_title',
            'content_quantity',
            'state',
            'transaction_uuid',
            'last_notification_at',
            'actions',
        ]
        read_only_fields = fields


class LearnerContentAssignmentAdminResponseSerializer(LearnerContentAssignmentResponseSerializer):
    """
    A read-only Serializer for responding to requests for ``LearnerContentAssignment`` records FOR ADMINS.

    Important: This serializer depends on extra dynamic fields annotated by calling
    ``LearnerContentAssignment.annotate_dynamic_fields_onto_queryset()`` on the assignment queryset.
    """

    recent_action = LearnerContentAssignmentRecentActionSerializer(
        help_text='Structured data about the most recent action. Meant to power a frontend table column.',
        source='*',
    )
    learner_state = serializers.ChoiceField(
        help_text=(
            'learner_state is an admin-facing dynamic state, not to be confused with `state`. Meant to power a '
            'frontend table column.'
        ),
        choices=AssignmentLearnerStates.CHOICES,
    )

    class Meta(LearnerContentAssignmentResponseSerializer.Meta):
        fields = LearnerContentAssignmentResponseSerializer.Meta.fields + [
            'recent_action',
            'learner_state',
        ]
        read_only_fields = fields
