"""
REST API views for the admin_portal_learner_profile app.
"""
import logging

from drf_spectacular.utils import extend_schema
from edx_rbac.decorators import permission_required
from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from enterprise_access.apps.admin_portal_learner_profile import api as admin_portal_learner_profile_api
from enterprise_access.apps.admin_portal_learner_profile import serializers
from enterprise_access.apps.core.constants import ADMIN_LEARNER_PROFILE_READ_PERMISSION

logger = logging.getLogger(__name__)


class AdminLearnerProfileViewSet(ViewSet):
    """
    A class that allows admins to look up subscriptions, enrollments, and group
    memberships for a learner.

    GET /api/v1/admin-view/learner_profile

    Expected params:
    - user_email (string): The email address for a learner within an enterprise.
    - lms_user_id (string): The unique id of the LMS user.
    - enterprise_customer_uuid (string): The UUID of an enterprise customer.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=['Admin Portal Learner Profile'],
        summary='Retrieve a profile for a learner from the admin portal.',
        request=serializers.AdminLearnerProfileRequestSerializer,
        responses={
            status.HTTP_200_OK: serializers.AdminLearnerProfileResponseSerializer
        }
    )
    @action(detail=False, methods=['get'])
    @permission_required(
        ADMIN_LEARNER_PROFILE_READ_PERMISSION,
        fn=lambda request, *args, **kwargs: request.query_params.get('enterprise_customer_uuid'))
    def learner_profile(self, request):
        """
        Retrieves all licenses, subscriptions, and enrollments associated with
        a learner's email address, LMS user ID, and enterprise.
        """
        serializer = serializers.AdminLearnerProfileRequestSerializer(data=request.query_params)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = serializer.validated_data
        enterprise_customer_uuid = validated_data['enterprise_customer_uuid']
        user_email = validated_data.get('user_email')
        lms_user_id = validated_data.get('lms_user_id')

        response_data = {
            'subscriptions': admin_portal_learner_profile_api.get_learner_subscriptions(
                enterprise_customer_uuid, user_email
            ),
            'group_memberships': admin_portal_learner_profile_api.get_group_memberships(
                enterprise_customer_uuid, lms_user_id
            ),
            'enrollments': admin_portal_learner_profile_api.get_enrollments(
                enterprise_customer_uuid, lms_user_id
            )
        }

        return Response(
            serializers.AdminLearnerProfileResponseSerializer(response_data).data,
            status=status.HTTP_200_OK
        )
