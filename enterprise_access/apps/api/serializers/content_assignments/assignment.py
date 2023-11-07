"""
Serializers for the `LearnerContentAssignment` model.
"""
import logging

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from enterprise_access.apps.content_assignments.constants import (
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
    error_reason = serializers.SerializerMethodField()

    class Meta(LearnerContentAssignmentResponseSerializer.Meta):
        fields = LearnerContentAssignmentResponseSerializer.Meta.fields + [
            'recent_action',
            'learner_state',
            'error_reason',
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField)
    def get_error_reason(self, assignment):
        """
        Resolves the error reason for the assignment, if any, for display purposes based on
        any associated actions.
        """
        # If the assignment is not in an errored state, there should be no error reason.
        if assignment.state != LearnerContentAssignmentStateChoices.ERRORED:
            return None

        # Assignment is an errored state, so determine the appropriate error reason so clients don't need to.
        related_actions_with_error = assignment.actions.filter(error_reason__isnull=False).order_by('-created')
        if not related_actions_with_error:
            logger.warning(
                'LearnerContentAssignment with UUID %s is in an errored state, but has no related '
                'actions in an error state.',
                assignment.uuid,
            )
            return None

        # Get the most recently errored action.
        return related_actions_with_error.first().error_reason


class CoursePartnerSerializer(serializers.Serializer):
    """
    Serialized partner ``name`` and ``logo_image_url`` for content_metadata of an assignment.
    """
    name = serializers.CharField(help_text='The partner name')
    logo_image_url = serializers.CharField(help_text='The URL for the parter logo image')


class ContentMetadataForAssignmentSerializer(serializers.Serializer):
    """
    Serializer to help return additional content metadata for assignments.  These fields should
    map more or less 1-1 to the fields in content metadata dicts returned from the
    enterprise-catalog `get_content_metadata` response payload.
    """
    start_date = serializers.SerializerMethodField(
        help_text='The start date of the course',
    )
    end_date = serializers.SerializerMethodField(
        help_text='The end date of the course',
    )
    enroll_by_date = serializers.SerializerMethodField(
        help_text='The date by which the learner must accept/enroll',
    )
    content_price = serializers.SerializerMethodField(
        help_text='The price, in USD, of this content',
    )
    partners = serializers.SerializerMethodField()

    @extend_schema_field(serializers.DateTimeField)
    def get_start_date(self, obj):
        return obj.get('normalized_metadata', {}).get('start_date')

    @extend_schema_field(serializers.DateTimeField)
    def get_end_date(self, obj):
        return obj.get('normalized_metadata', {}).get('end_date')

    @extend_schema_field(serializers.DateTimeField)
    def get_enroll_by_date(self, obj):
        return obj.get('normalized_metadata', {}).get('enroll_by_date')

    @extend_schema_field(serializers.IntegerField)
    def get_content_price(self, obj):
        return obj.get('normalized_metadata', {}).get('content_price')

    @extend_schema_field(CoursePartnerSerializer)
    def get_partners(self, obj):
        """
        See ``get_course_partners()`` in
        enterprise-catalog/enterprise_catalog/apps/catalog/algolia_utils.py
        """
        partners = []
        owners = obj.get('owners') or []

        for owner in owners:
            partner_name = owner.get('name')
            if partner_name:
                partner_metadata = {
                    'name': partner_name,
                    'logo_image_url': owner.get('logo_image_url'),
                }
                partners.append(partner_metadata)

        return CoursePartnerSerializer(partners, many=True).data


class LearnerContentAssignmentWithContentMetadataResponseSerializer(LearnerContentAssignmentResponseSerializer):
    """
    Read-only serializer for LearnerContentAssignment records that also includes additional content metadata,
    fetched from the catalog service (or cache).
    """
    content_metadata = serializers.SerializerMethodField(
        help_text='Additional content metadata fetched from the catalog service or cache.',
    )

    class Meta(LearnerContentAssignmentResponseSerializer.Meta):
        fields = LearnerContentAssignmentResponseSerializer.Meta.fields + ['content_metadata']
        read_only_fields = fields

    @extend_schema_field(ContentMetadataForAssignmentSerializer)
    def get_content_metadata(self, obj):
        """
        Serializers content metadata for the assignment, if available.
        """
        metadata_lookup = self.context.get('content_metadata')
        if metadata_lookup and (assignment_content_metadata := metadata_lookup.get(obj.content_key)):
            return ContentMetadataForAssignmentSerializer(assignment_content_metadata).data
        return None
