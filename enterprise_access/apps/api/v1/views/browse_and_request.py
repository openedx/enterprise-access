"""
Rest API views for the browse and request app.
"""
import logging

from celery import chain
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Count
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from edx_rbac.decorators import permission_required
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from requests.exceptions import ConnectionError as RequestConnectionError
from requests.exceptions import HTTPError, Timeout
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ParseError
from rest_framework.response import Response

from enterprise_access.apps.api import serializers
from enterprise_access.apps.api.constants import LICENSE_UNASSIGNED_STATUS
from enterprise_access.apps.api.exceptions import SubsidyRequestCreationError, SubsidyRequestError
from enterprise_access.apps.api.filters import (
    SubsidyRequestCustomerConfigurationFilterBackend,
    SubsidyRequestFilterBackend
)
from enterprise_access.apps.api.mixins import UserDetailsFromJwtMixin
from enterprise_access.apps.api.tasks import (
    assign_coupon_codes_task,
    assign_licenses_task,
    decline_enterprise_subsidy_requests_task,
    send_notification_email_for_request,
    unlink_users_from_enterprise_task,
    update_coupon_code_requests_after_assignments_task,
    update_license_requests_after_assignments_task
)
from enterprise_access.apps.api.utils import (
    get_enterprise_uuid_from_query_params,
    get_enterprise_uuid_from_request_data,
    validate_uuid
)
from enterprise_access.apps.api_client.ecommerce_client import EcommerceApiClient
from enterprise_access.apps.api_client.license_manager_client import LicenseManagerApiClient
from enterprise_access.apps.core import constants
from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy
from enterprise_access.apps.subsidy_request.constants import SegmentEvents, SubsidyRequestStates, SubsidyTypeChoices
from enterprise_access.apps.subsidy_request.models import (
    CouponCodeRequest,
    LearnerCreditRequest,
    LicenseRequest,
    SubsidyRequestCustomerConfiguration
)
from enterprise_access.apps.track.segment import track_event
from enterprise_access.utils import get_subsidy_model

from .utils import PaginationWithPageCount

logger = logging.getLogger(__name__)


class SubsidyRequestViewSet(UserDetailsFromJwtMixin, viewsets.ModelViewSet):
    """
    Base Viewset for subsidy requests.
    """

    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = serializers.SubsidyRequestSerializer
    list_lookup_field = 'enterprise_customer_uuid'

    authentication_classes = (JwtAuthentication,)

    filter_backends = (filters.OrderingFilter, DjangoFilterBackend, SubsidyRequestFilterBackend, filters.SearchFilter)
    filterset_fields = ('uuid', 'user__email', 'course_id', 'enterprise_customer_uuid')
    pagination_class = PaginationWithPageCount

    search_fields = ['user__email']

    http_method_names = ['get', 'post']

    subsidy_type = None

    def _validate_subsidy_request_uuids(self, subsidy_request_uuids):
        """
        Args:
            subsidy_request_uuids: a list of one or more valid uuid strings
        Raises:
            SubsidyRequestError: if subsidy UUID(s) are not preset or invalid
        """
        if not subsidy_request_uuids:
            error_msg = 'You must provide subsidy request UUID(s) to be approved.'
            logger.exception(error_msg)
            raise SubsidyRequestError(error_msg, status.HTTP_400_BAD_REQUEST)

        for subsidy_request_uuid in subsidy_request_uuids:
            try:
                validate_uuid(subsidy_request_uuid)
            except ParseError as exc:
                error_msg = f'Subsidy Request UUID provided ({subsidy_request_uuid}) is not a valid UUID'
                logger.exception(error_msg)
                raise SubsidyRequestError(error_msg, status.HTTP_400_BAD_REQUEST) from exc

    def _validate_subsidy_request(self):
        """
        Raises:
            SubsidyRequestCreationError: if a subsidy request cannot be created
        """

        enterprise_customer_uuid = self.request.data.get('enterprise_customer_uuid')

        try:
            customer_configuration = SubsidyRequestCustomerConfiguration.objects.get(
                enterprise_customer_uuid=enterprise_customer_uuid
            )
        except ObjectDoesNotExist as exc:
            error_msg = f'Customer configuration for enterprise: {enterprise_customer_uuid} does not exist.'
            raise SubsidyRequestCreationError(error_msg, status.HTTP_422_UNPROCESSABLE_ENTITY) from exc

        if not customer_configuration.subsidy_requests_enabled:
            error_msg = f'Subsidy requests for enterprise: {enterprise_customer_uuid} are disabled.'
            raise SubsidyRequestCreationError(error_msg, status.HTTP_422_UNPROCESSABLE_ENTITY)

        if not customer_configuration.subsidy_type:
            error_msg = f'Subsidy request type for enterprise: {enterprise_customer_uuid} has not been set up.'
            raise SubsidyRequestCreationError(error_msg, status.HTTP_422_UNPROCESSABLE_ENTITY)

        if customer_configuration.subsidy_type != self.subsidy_type:
            error_msg = f'Subsidy request type must be {customer_configuration.subsidy_type}'
            raise SubsidyRequestCreationError(error_msg, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def _raise_error_if_any_requests_match_incorrect_states(self, subsidy_requests, incorrect_states, current_action):
        """
        Args:
           subsidy_requests: SubsidyRequest Queryset
           incorrect_states: SubsidyRequestStates states that should throw error
           current_action: SubsidyRequestState that we are trying to apply where this is called
        Raises:
            SubsidyRequestError: if any request has SubsidyRequest state == incorrect_states
        """

        subsidies_in_wrong_state = subsidy_requests.filter(state__in=incorrect_states)
        if subsidies_in_wrong_state:
            uuids = [str(uuid) for uuid in list(subsidies_in_wrong_state.values_list('uuid', flat=True))]
            pretty_uuids = ','.join(uuids)
            pretty_verbs = '/'.join(incorrect_states)
            error_msg = (
                f'{self.subsidy_type} Request(s) with UUID(s) {pretty_uuids} are already {pretty_verbs}. '
                f'Requests could not be {current_action}.'
            )
            raise SubsidyRequestError(error_msg, status.HTTP_422_UNPROCESSABLE_ENTITY)

    @permission_required(
        constants.REQUESTS_ADMIN_LEARNER_ACCESS_PERMISSION,
        fn=get_enterprise_uuid_from_request_data,
    )
    def create(self, request, *args, **kwargs):
        try:
            self._validate_subsidy_request()
        except SubsidyRequestCreationError as exc:
            logger.exception(exc)
            return Response(exc.message, exc.http_status_code)

        request.data['user'] = self.user.id
        response = super().create(request, *args, **kwargs)

        track_event(
            lms_user_id=self.lms_user_id,
            event_name=SegmentEvents.SUBSIDY_REQUEST_CREATED[self.subsidy_type],
            properties=response.data
        )

        return response

    @permission_required(
        constants.REQUESTS_ADMIN_ACCESS_PERMISSION,
        fn=get_enterprise_uuid_from_query_params,
    )
    @action(detail=False, url_path='overview', methods=['get'])
    def overview(self, request):
        """
        Returns an overview of subsidy requests count by state.
        """
        enterprise_customer_uuid = self.request.query_params.get('enterprise_customer_uuid')
        if not enterprise_customer_uuid:
            msg = 'enterprise_customer_uuid query param is required'
            return Response(msg, status=status.HTTP_400_BAD_REQUEST)

        queryset = self.filter_queryset(self.get_queryset()).filter(enterprise_customer_uuid=enterprise_customer_uuid)
        queryset_values = queryset.values('state').annotate(count=Count('state')).order_by('-count')
        requests_overview = list(queryset_values)
        return Response(requests_overview, status=status.HTTP_200_OK)


@extend_schema_view(
    retrieve=extend_schema(
        tags=['License Requests'],
        summary='License request retrieve.',
    ),
    list=extend_schema(
        tags=['License Requests'],
        summary='License request list.',
    ),
    create=extend_schema(
        tags=['License Requests'],
        summary='License request create.',
    ),
    overview=extend_schema(
        tags=['License Requests'],
        summary='License request overview.',
    ),
)
class LicenseRequestViewSet(SubsidyRequestViewSet):
    """
    Viewset for license requests
    """

    queryset = LicenseRequest.objects.order_by('-created')
    serializer_class = serializers.LicenseRequestSerializer

    subsidy_type = SubsidyTypeChoices.LICENSE

    def _validate_subsidy_request(self):
        super()._validate_subsidy_request()

        enterprise_customer_uuid = self.request.data.get('enterprise_customer_uuid')

        has_pending_request = LicenseRequest.objects.filter(
            user__lms_user_id=self.lms_user_id,
            enterprise_customer_uuid=enterprise_customer_uuid,
            state__in=[SubsidyRequestStates.REQUESTED, SubsidyRequestStates.PENDING]
        ).first()

        if has_pending_request:
            error_msg = f'User already has an outstanding license request for enterprise: {enterprise_customer_uuid}.'
            logger.exception(error_msg)
            raise SubsidyRequestCreationError(error_msg, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def _validate_subscription_plan_uuid(self, subscription_plan_uuid):
        """
        Args:
            subsidy_request_uuid: a valid uuid
        Raises:
            SubsidyRequestError: if subscription_plan_uuid not present or invalid
        """
        if not subscription_plan_uuid:
            error_msg = (
                'You must provide a License Subscription UUID so subsidy requests '
                'can be completed (so a License can be assigned to the user).'
            )
            logger.exception(error_msg)
            raise SubsidyRequestError(error_msg, status.HTTP_400_BAD_REQUEST)

        try:
            validate_uuid(subscription_plan_uuid)
        except ParseError as exc:
            error_msg = f'Subscription Plan UUID provided ({subscription_plan_uuid}) is not a valid UUID'
            logger.exception(error_msg)
            raise SubsidyRequestError(error_msg, status.HTTP_400_BAD_REQUEST) from exc

    def _verify_subsidies_remaining(self, subscription_plan_uuid, subsidy_request_uuids):
        """
        Check license manager to make sure there are licenses remaining.

        Args:
            subscription_plan_uuid: UUID for subscription plan
            subsidy_request_uuids: list of one or more UUID strings
        """
        try:
            client = LicenseManagerApiClient()
            status_counts = client.get_subscription_overview(subscription_plan_uuid)
        except (RequestConnectionError, HTTPError, Timeout) as exc:
            error_msg = (
                'We were unable to approve/decline the selected license requests at this time. '
                'Please try again in a few minutes, or reach out to customer support if you '
                'continue to experience issues. '
            )
            raise SubsidyRequestError(error_msg, status.HTTP_500_INTERNAL_SERVER_ERROR) from exc

        remaining_unassigned_codes = 0
        for status_count in status_counts:
            if status_count['status'] == LICENSE_UNASSIGNED_STATUS:
                remaining_unassigned_codes = status_count['count']
                break

        if remaining_unassigned_codes < len(subsidy_request_uuids):
            error_msg = (
                f'Not enough licenses available for subscription {subscription_plan_uuid} '
                'to approve the requests'
            )
            raise SubsidyRequestError(error_msg, status.HTTP_422_UNPROCESSABLE_ENTITY)

    @extend_schema(
        tags=['License Requests'],
        summary='License request approve.',
    )
    @permission_required(
        constants.REQUESTS_ADMIN_ACCESS_PERMISSION,
        fn=get_enterprise_uuid_from_request_data,
    )
    @action(detail=False, url_path='approve', methods=['post'])
    def approve(self, *args, **kwargs):
        """
        Action of approving a License Subsidy Request
        """

        enterprise_customer_uuid = get_enterprise_uuid_from_request_data(self.request)
        license_request_uuids = self.request.data.get('subsidy_request_uuids')
        subscription_plan_uuid = self.request.data.get('subscription_plan_uuid')
        send_notification = self.request.data.get('send_notification', False)

        try:
            self._validate_subsidy_request_uuids(license_request_uuids)
            self._validate_subscription_plan_uuid(subscription_plan_uuid)
            self._verify_subsidies_remaining(subscription_plan_uuid, license_request_uuids)
        except SubsidyRequestError as exc:
            logger.exception(exc)
            return Response(exc.message, exc.http_status_code)

        license_requests = LicenseRequest.objects.filter(
            uuid__in=license_request_uuids,
            enterprise_customer_uuid=enterprise_customer_uuid,
        )

        try:
            self._raise_error_if_any_requests_match_incorrect_states(
                license_requests,
                incorrect_states=[SubsidyRequestStates.DECLINED],
                current_action=SubsidyRequestStates.APPROVED
            )
        except SubsidyRequestError as exc:
            logger.exception(exc)
            return Response(exc.message, exc.http_status_code)

        license_requests_to_approve = license_requests.filter(
            state__in=[SubsidyRequestStates.REQUESTED, SubsidyRequestStates.ERROR]
        )
        with transaction.atomic():
            for request in license_requests_to_approve:
                request.approve(self.user)

        license_requests_to_approve_uuids = [
            str(license_request.uuid) for license_request in license_requests_to_approve
        ]
        license_assignment_tasks = chain(
            assign_licenses_task.s(
                license_requests_to_approve_uuids,
                subscription_plan_uuid
            ),
            update_license_requests_after_assignments_task.s()
        )

        if send_notification:
            for license_request_uuid in license_requests_to_approve_uuids:
                license_assignment_tasks.link(
                    send_notification_email_for_request.si(
                        license_request_uuid,
                        settings.BRAZE_APPROVE_NOTIFICATION_CAMPAIGN,
                        SubsidyTypeChoices.LICENSE,
                    )
                )

        license_assignment_tasks.apply_async()

        response_data = serializers.LicenseRequestSerializer(license_requests_to_approve, many=True).data
        return Response(
            response_data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=['License Requests'],
        summary='License request deny.',
    )
    @permission_required(
        constants.REQUESTS_ADMIN_ACCESS_PERMISSION,
        fn=get_enterprise_uuid_from_request_data,
    )
    @action(detail=False, url_path='decline', methods=['post'])
    def decline(self, *args, **kwargs):
        """
        Action of declining a License Subsidy Request
        """

        enterprise_customer_uuid = get_enterprise_uuid_from_request_data(self.request)
        license_request_uuids = self.request.data.get('subsidy_request_uuids')
        send_notification = self.request.data.get('send_notification', False)
        unlink_users_from_enterprise = self.request.data.get('unlink_users_from_enterprise', False)

        try:
            self._validate_subsidy_request_uuids(license_request_uuids)
        except SubsidyRequestError as exc:
            logger.exception(exc)
            return Response(exc.message, exc.http_status_code)

        license_requests = LicenseRequest.objects.filter(
            uuid__in=license_request_uuids,
            enterprise_customer_uuid=enterprise_customer_uuid,
        )

        try:
            self._raise_error_if_any_requests_match_incorrect_states(
                license_requests,
                incorrect_states=[SubsidyRequestStates.APPROVED, SubsidyRequestStates.PENDING],
                current_action=SubsidyRequestStates.DECLINED
            )
        except SubsidyRequestError as exc:
            logger.exception(exc)
            return Response(exc.message, exc.http_status_code)

        license_requests_to_decline = license_requests.filter(
            state__in=[SubsidyRequestStates.REQUESTED, SubsidyRequestStates.ERROR]
        ).select_related('user', 'reviewer')

        with transaction.atomic():
            for license_request in license_requests_to_decline:
                license_request.decline(self.user)

        serialized_license_requests = serializers.LicenseRequestSerializer(license_requests_to_decline, many=True).data

        for serialized_license_request in serialized_license_requests:
            license_request_uuid = serialized_license_request['uuid']
            lms_user_id = serialized_license_request['lms_user_id']
            enterprise_customer_uuid = serialized_license_request['enterprise_customer_uuid']

            track_event(
                lms_user_id=lms_user_id,
                event_name=SegmentEvents.LICENSE_REQUEST_DECLINED,
                properties={
                    **serialized_license_request,
                    'unlinked_from_enterprise': unlink_users_from_enterprise,
                    'notification_sent': send_notification
                }
            )

            if send_notification:
                send_notification_email_for_request.delay(
                    license_request_uuid,
                    settings.BRAZE_DECLINE_NOTIFICATION_CAMPAIGN,
                    SubsidyTypeChoices.LICENSE,
                    {
                        'unlinked_from_enterprise': unlink_users_from_enterprise
                    }
                )

            if unlink_users_from_enterprise:
                unlink_users_from_enterprise_task.delay(
                    enterprise_customer_uuid,
                    [lms_user_id]
                )

        return Response(
            serialized_license_requests,
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    retrieve=extend_schema(
        tags=['Coupon Code Requests'],
        summary='Coupon Code request retrieve.',
    ),
    list=extend_schema(
        tags=['Coupon Code Requests'],
        summary='Coupon Code request list.',
    ),
    create=extend_schema(
        tags=['Coupon Code Requests'],
        summary='Coupon Code request create.',
    ),
    overview=extend_schema(
        tags=['Coupon Code Requests'],
        summary='Coupon Code request overview.',
    ),
)
class CouponCodeRequestViewSet(SubsidyRequestViewSet):
    """
    Viewset for coupon code requests
    """

    queryset = CouponCodeRequest.objects.order_by('-created')
    serializer_class = serializers.CouponCodeRequestSerializer

    subsidy_type = SubsidyTypeChoices.COUPON

    def _validate_subsidy_request(self):
        super()._validate_subsidy_request()

        enterprise_customer_uuid = self.request.data.get('enterprise_customer_uuid')
        course_id = self.request.data.get('course_id')

        has_pending_request = CouponCodeRequest.objects.filter(
            user__lms_user_id=self.lms_user_id,
            enterprise_customer_uuid=enterprise_customer_uuid,
            state__in=[SubsidyRequestStates.REQUESTED, SubsidyRequestStates.PENDING],
            course_id=course_id
        ).exists()

        if has_pending_request:
            error_msg = f'User already has an outstanding coupon code request for course: {course_id} ' + \
                f'under enterprise: {enterprise_customer_uuid}.'
            logger.exception(error_msg)
            raise SubsidyRequestCreationError(error_msg, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def _validate_redemptions_remaining(self, enterprise_customer_uuid, coupon_id, subsidy_request_uuids):
        """
        Make sure there are coupon redemptions remaining.

        Args:
            enterprise_customer_uuid: a string of enterprise_customer_uuid
            coupon_id: integer id for a coupon
            subsidy_request_uuids: list of one or more UUID strings
        """
        try:
            ecommerce_client = EcommerceApiClient()
            coupon_overview = ecommerce_client.get_coupon_overview(enterprise_customer_uuid, coupon_id)
        except (RequestConnectionError, HTTPError, Timeout) as exc:
            error_msg = (
                'We were unable to approve/decline the selected coupon requests at this time. '
                'Please try again in a few minutes, or reach out to customer support if you '
                'continue to experience issues. '
            )
            raise SubsidyRequestError(error_msg, status.HTTP_500_INTERNAL_SERVER_ERROR) from exc

        if coupon_overview['num_unassigned'] < len(subsidy_request_uuids):
            error_msg = 'Not enough codes available for coupon {coupon_id} to approve the requests'
            raise SubsidyRequestError(error_msg, status.HTTP_422_UNPROCESSABLE_ENTITY)

    @extend_schema(
        tags=['Coupon Code Requests'],
        summary='Coupon Code request approve.',
    )
    @permission_required(
        constants.REQUESTS_ADMIN_ACCESS_PERMISSION,
        fn=get_enterprise_uuid_from_request_data,
    )
    @action(detail=False, url_path='approve', methods=['post'])
    def approve(self, *args, **kwargs):
        """
        Action of approving a Coupon Subsidy Request
        """

        enterprise_customer_uuid = get_enterprise_uuid_from_request_data(self.request)
        coupon_code_request_uuids = self.request.data.get('subsidy_request_uuids')
        coupon_id = self.request.data.get('coupon_id')
        send_notification = self.request.data.get('send_notification', False)

        try:
            self._validate_subsidy_request_uuids(coupon_code_request_uuids)
            self._validate_redemptions_remaining(enterprise_customer_uuid, coupon_id, coupon_code_request_uuids)
        except SubsidyRequestError as exc:
            logger.exception(exc)
            return Response(exc.message, exc.http_status_code)

        coupon_code_requests = CouponCodeRequest.objects.filter(
            uuid__in=coupon_code_request_uuids,
            enterprise_customer_uuid=enterprise_customer_uuid,
        )

        try:
            self._raise_error_if_any_requests_match_incorrect_states(
                coupon_code_requests,
                incorrect_states=[SubsidyRequestStates.DECLINED],
                current_action=SubsidyRequestStates.APPROVED
            )
        except SubsidyRequestError as exc:
            logger.exception(exc)
            return Response(exc.message, exc.http_status_code)

        coupon_code_requests_to_approve = coupon_code_requests.filter(
            state__in=[SubsidyRequestStates.REQUESTED, SubsidyRequestStates.ERROR]
        )
        with transaction.atomic():
            for coupon_code_request in coupon_code_requests_to_approve:
                coupon_code_request.approve(self.user)

        coupon_code_requests_to_approve_uuids = [
            str(coupon_code_request.uuid) for coupon_code_request in coupon_code_requests_to_approve
        ]
        coupon_code_assignment_tasks = chain(
            assign_coupon_codes_task.s(
                coupon_code_requests_to_approve_uuids,
                coupon_id
            ),
            update_coupon_code_requests_after_assignments_task.s()
        )

        if send_notification:
            for coupon_code_request_uuid in coupon_code_requests_to_approve_uuids:
                coupon_code_assignment_tasks.link(
                    send_notification_email_for_request.si(
                        coupon_code_request_uuid,
                        settings.BRAZE_APPROVE_NOTIFICATION_CAMPAIGN,
                        SubsidyTypeChoices.COUPON,
                    )
                )

        coupon_code_assignment_tasks.apply_async()

        response_data = serializers.CouponCodeRequestSerializer(
            coupon_code_requests_to_approve,
            many=True
        ).data

        return Response(
            response_data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=['Coupon Code Requests'],
        summary='Coupon Code request deny.',
    )
    @action(detail=False, url_path='decline', methods=['post'])
    def decline(self, *args, **kwargs):
        """
        Action of declining a Coupon Subsidy Request
        """

        enterprise_customer_uuid = get_enterprise_uuid_from_request_data(self.request)
        coupon_code_request_uuids = self.request.data.get('subsidy_request_uuids')
        send_notification = self.request.data.get('send_notification', False)
        unlink_users_from_enterprise = self.request.data.get('unlink_users_from_enterprise', False)

        try:
            self._validate_subsidy_request_uuids(coupon_code_request_uuids)
        except SubsidyRequestError as exc:
            logger.exception(exc)
            return Response(exc.message, exc.http_status_code)

        coupon_code_requests = CouponCodeRequest.objects.filter(
            uuid__in=coupon_code_request_uuids,
            enterprise_customer_uuid=enterprise_customer_uuid,
        )

        try:
            self._raise_error_if_any_requests_match_incorrect_states(
                coupon_code_requests,
                incorrect_states=[SubsidyRequestStates.APPROVED, SubsidyRequestStates.PENDING],
                current_action=SubsidyRequestStates.DECLINED
            )
        except SubsidyRequestError as exc:
            logger.exception(exc)
            return Response(exc.message, exc.http_status_code)

        coupon_code_requests_to_decline = coupon_code_requests.filter(
            state__in=[SubsidyRequestStates.REQUESTED, SubsidyRequestStates.ERROR]
        ).select_related('user', 'reviewer')

        with transaction.atomic():
            for coupon_code_request in coupon_code_requests_to_decline:
                coupon_code_request.decline(self.user)

        serialized_coupon_code_requests = serializers.CouponCodeRequestSerializer(
            coupon_code_requests_to_decline, many=True
        ).data

        for serialized_coupon_code_request in serialized_coupon_code_requests:
            coupon_code_request_uuid = serialized_coupon_code_request['uuid']
            lms_user_id = serialized_coupon_code_request['lms_user_id']
            enterprise_customer_uuid = serialized_coupon_code_request['enterprise_customer_uuid']

            track_event(
                lms_user_id=lms_user_id,
                event_name=SegmentEvents.COUPON_CODE_REQUEST_DECLINED,
                properties={
                    **serialized_coupon_code_request,
                    'unlinked_from_enterprise': unlink_users_from_enterprise,
                    'notification_sent': send_notification
                }
            )

            if send_notification:
                send_notification_email_for_request.delay(
                    coupon_code_request_uuid,
                    settings.BRAZE_DECLINE_NOTIFICATION_CAMPAIGN,
                    SubsidyTypeChoices.COUPON,
                    {
                        'unlinked_from_enterprise': unlink_users_from_enterprise
                    }
                )

            if unlink_users_from_enterprise:
                unlink_users_from_enterprise_task.delay(
                    enterprise_customer_uuid,
                    [lms_user_id]
                )

        return Response(
            serialized_coupon_code_requests,
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    retrieve=extend_schema(
        tags=['Learner Credit Requests'],
        summary='Retrieve a learner credit request.',
    ),
    list=extend_schema(
        tags=['Learner Credit Requests'],
        summary='List learner credit requests.',
    ),
    create=extend_schema(
        tags=['Learner Credit Requests'],
        summary='Create a learner credit request.',
    ),
    overview=extend_schema(
        tags=['Learner Credit Requests'],
        summary='Learner credit request overview.',
    ),
)
class LearnerCreditRequestViewSet(SubsidyRequestViewSet):
    """
    Viewset for learner credit requests.
    """

    queryset = LearnerCreditRequest.objects.order_by("-created")
    serializer_class = serializers.LearnerCreditRequestSerializer

    subsidy_type = SubsidyTypeChoices.LEARNER_CREDIT

    search_fields = ['user__email', 'course_title']

    def _validate_subsidy_request(self):
        """
        Validate request creation:
        - Ensure policy_uuid is provided and valid.
        - Ensure Browse & Request is active for the policy.
        - No duplicate PENDING/REQUESTED requests for the same course and policy.
        """
        policy_uuid = self.request.data.get("policy_uuid")
        if not policy_uuid:
            raise SubsidyRequestCreationError(
                "policy_uuid is required.", status.HTTP_400_BAD_REQUEST
            )

        try:
            policy = SubsidyAccessPolicy.objects.get(uuid=policy_uuid)
            self.request.validated_policy = policy  # Store validated policy for use in create
        except SubsidyAccessPolicy.DoesNotExist as exc:
            raise SubsidyRequestCreationError(
                f"Invalid policy_uuid: {policy_uuid}.", status.HTTP_400_BAD_REQUEST
            ) from exc

        if not policy.bnr_enabled:
            raise SubsidyRequestCreationError(
                f"Browse & Request is not active for policy UUID: {policy_uuid}.",
                status.HTTP_400_BAD_REQUEST,
            )

        enterprise_customer_uuid = policy.enterprise_customer_uuid
        course_id = self.request.data.get("course_id")

        if LearnerCreditRequest.objects.filter(
            user__lms_user_id=self.lms_user_id,
            enterprise_customer_uuid=enterprise_customer_uuid,
            course_id=course_id,
            state__in=[
                SubsidyRequestStates.REQUESTED,
                SubsidyRequestStates.APPROVED,
                SubsidyRequestStates.ACCEPTED,
                SubsidyRequestStates.ERROR
            ]
        ).exists():
            error_msg = (
                f"You already have an active learner credit request for course {course_id} "
                f"under policy UUID: {policy_uuid}."
            )
            raise SubsidyRequestCreationError(
                error_msg, status.HTTP_422_UNPROCESSABLE_ENTITY
            )

    @permission_required(
        constants.REQUESTS_ADMIN_LEARNER_ACCESS_PERMISSION,
        fn=get_enterprise_uuid_from_request_data,
    )
    def create(self, request, *args, **kwargs):
        """
        Create a learner credit request.
        """
        try:
            self._validate_subsidy_request()
        except SubsidyRequestCreationError as exc:
            return Response({"detail": exc.message}, status=exc.http_status_code)

        policy = self.request.validated_policy
        request.data.update(
            {
                "user": request.user.id,
                "enterprise_customer_uuid": str(policy.enterprise_customer_uuid),
                "learner_credit_request_config": str(
                    policy.learner_credit_request_config.uuid
                ),
            }
        )

        return super().create(request, *args, **kwargs)


@extend_schema_view(
    retrieve=extend_schema(
        tags=['Subsidy Request Configuration'],
        summary='Retrieve customer config.',
    ),
    list=extend_schema(
        tags=['Subsidy Request Configuration'],
        summary='List customer config.',
    ),
)
class SubsidyRequestCustomerConfigurationViewSet(UserDetailsFromJwtMixin, viewsets.ModelViewSet):
    """ Viewset for customer configurations."""

    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = serializers.SubsidyRequestCustomerConfigurationSerializer

    authentication_classes = (JwtAuthentication,)

    filter_backends = (filters.OrderingFilter, DjangoFilterBackend, SubsidyRequestCustomerConfigurationFilterBackend)
    filterset_fields = ('enterprise_customer_uuid', 'subsidy_requests_enabled', 'subsidy_type',)
    pagination_class = PaginationWithPageCount

    queryset = SubsidyRequestCustomerConfiguration.objects.order_by('-created')

    http_method_names = ['get', 'post', 'patch']

    @extend_schema(
        tags=['Subsidy Request Configuration'],
        summary='Create customer config.',
    )
    @permission_required(
        constants.REQUESTS_ADMIN_ACCESS_PERMISSION,
        fn=get_enterprise_uuid_from_request_data,
    )
    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        track_event(
            lms_user_id=self.lms_user_id,
            event_name=SegmentEvents.SUBSIDY_REQUEST_CONFIGURATION_CREATED,
            properties=response.data
        )
        return response

    @extend_schema(
        tags=['Subsidy Request Configuration'],
        summary='Update customer config.',
    )
    @permission_required(
        constants.REQUESTS_ADMIN_ACCESS_PERMISSION,
        fn=lambda request, pk: pk
    )
    def partial_update(self, request, *args, **kwargs):
        enterprise_customer_uuid = kwargs['pk']
        current_config = SubsidyRequestCustomerConfiguration.objects.get(pk=enterprise_customer_uuid)

        if 'subsidy_type' in request.data:

            subsidy_type = request.data['subsidy_type']
            send_notification = request.data.get('send_notification', False)
            current_subsidy_type = current_config.subsidy_type

            if current_subsidy_type and subsidy_type != current_subsidy_type:

                current_subsidy_model = get_subsidy_model(current_subsidy_type)
                # Don't flush anything if current subsidy model not set yet
                if current_subsidy_model is None:
                    return super().partial_update(request, *args, **kwargs)

                # Get identifiers of requests to decline and optionally send notifcations for
                subsidy_request_uuids = list(current_subsidy_model.objects.filter(
                    enterprise_customer_uuid=enterprise_customer_uuid,
                    state__in=[
                        SubsidyRequestStates.REQUESTED,
                        SubsidyRequestStates.PENDING,
                        SubsidyRequestStates.ERROR
                    ],
                ).values_list('uuid', flat=True))
                subsidy_request_uuids = [str(uuid) for uuid in subsidy_request_uuids]

                tasks = chain(
                    decline_enterprise_subsidy_requests_task.si(
                        subsidy_request_uuids,
                        current_subsidy_type,
                    ),
                )

                if send_notification:
                    for subsidy_request_uuid in subsidy_request_uuids:
                        tasks.link(
                            send_notification_email_for_request.si(
                                subsidy_request_uuid,
                                settings.BRAZE_AUTO_DECLINE_NOTIFICATION_CAMPAIGN,
                                current_subsidy_type,
                            )
                        )

                tasks.delay()

        response = super().partial_update(request, *args, **kwargs)

        track_event(
            lms_user_id=self.lms_user_id,
            event_name=SegmentEvents.SUBSIDY_REQUEST_CONFIGURATION_UPDATED,
            properties=response.data
        )
        return response
