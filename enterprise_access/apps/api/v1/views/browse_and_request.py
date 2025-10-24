"""
Rest API views for the browse and request app.
"""
import logging

from celery import chain
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import DatabaseError, IntegrityError, transaction
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
    LearnerCreditRequestFilterSet,
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
    add_bulk_approve_operation_result,
    get_enterprise_uuid_from_query_params,
    get_enterprise_uuid_from_request_data,
    validate_uuid
)
from enterprise_access.apps.api_client.ecommerce_client import EcommerceApiClient
from enterprise_access.apps.api_client.license_manager_client import LicenseManagerApiClient
from enterprise_access.apps.content_assignments import api as assignments_api
from enterprise_access.apps.core import constants
from enterprise_access.apps.subsidy_access_policy.api import approve_learner_credit_request_via_policy
from enterprise_access.apps.subsidy_access_policy.exceptions import SubisidyAccessPolicyRequestApprovalError
from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy
from enterprise_access.apps.subsidy_request.constants import (
    REUSABLE_REQUEST_STATES,
    LearnerCreditAdditionalActionStates,
    LearnerCreditRequestActionErrorReasons,
    SegmentEvents,
    SubsidyRequestStates,
    SubsidyTypeChoices
)
from enterprise_access.apps.subsidy_request.models import (
    CouponCodeRequest,
    LearnerCreditRequest,
    LearnerCreditRequestActions,
    LicenseRequest,
    SubsidyRequestCustomerConfiguration
)
from enterprise_access.apps.subsidy_request.tasks import (
    send_learner_credit_bnr_admins_email_with_new_requests_task,
    send_learner_credit_bnr_cancel_notification_task,
    send_learner_credit_bnr_decline_notification_task,
    send_learner_credit_bnr_request_approve_task,
    send_reminder_email_for_pending_learner_credit_request
)
from enterprise_access.apps.subsidy_request.utils import (
    get_action_choice,
    get_error_reason_choice,
    get_user_message_choice
)
from enterprise_access.apps.track.segment import track_event
from enterprise_access.utils import format_traceback, get_subsidy_model

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
    approve=extend_schema(
        tags=['Learner Credit Requests'],
        summary='Approve a learner credit request.',
        request=serializers.LearnerCreditRequestApproveRequestSerializer,
    ),
    bulk_approve=extend_schema(
        tags=['Learner Credit Requests'],
        summary='Bulk approve learner credit requests.',
        description=(
            'Bulk approve learner credit requests. Supports two modes:\n'
            '1. Specific UUID approval: provide subsidy_request_uuids\n'
            '2. Approve all: set approve_all=True (optionally with query filters)\n\n'
            'Response contains categorized results with uuid, state, and detail for each request.'
        ),
        request=serializers.LearnerCreditRequestBulkApproveRequestSerializer,
    ),
    overview=extend_schema(
        tags=['Learner Credit Requests'],
        summary='Learner credit request overview.',
    ),
    decline=extend_schema(
        tags=['Learner Credit Requests'],
        summary='Decline a learner credit request.',
        request=serializers.LearnerCreditRequestDeclineSerializer,
    ),
    cancel=extend_schema(
        tags=['Learner Credit Requests'],
        summary='Learner credit request cancel endpoint.',
    )
)
class LearnerCreditRequestViewSet(SubsidyRequestViewSet):
    """
    Viewset for learner credit requests.
    """

    queryset = LearnerCreditRequest.objects.order_by("-created")
    serializer_class = serializers.LearnerCreditRequestSerializer
    filterset_class = LearnerCreditRequestFilterSet

    # Add ordering fields including simple action-based sorting
    ordering_fields = [
        'created',
        'reviewed_at',
        'course_price',
        'state',
        'user__email',
        'course_title',

        # Simple action-based sorting fields
        'latest_action_time',
        'latest_action_type',
        'latest_action_status',
        'learner_request_state',

        # State-based sorting field
        'state_sort_order',
    ]

    subsidy_type = SubsidyTypeChoices.LEARNER_CREDIT

    search_fields = ['user__email', 'course_title']

    def get_queryset(self):
        """
        Apply simple action-based annotations for sorting by latest action status.
        """
        queryset = super().get_queryset()

        # Apply annotations for list views with sorting capabilities
        if self.action in ('list', 'overview'):
            queryset = LearnerCreditRequest.annotate_dynamic_fields_onto_queryset(
                queryset
            ).prefetch_related(
                'actions',
            ).select_related(
                'user',
                'reviewer'
            )

        return queryset

    def _reuse_existing_request(self, request, course_price):
        """
        Reuse an existing learner credit request by resetting its state and attributes.
        """
        logger.info(
            "Reusing existing learner credit request: %s for user: %s course: %s",
            request.uuid,
            request.user.lms_user_id,
            request.course_id
        )
        request.state = SubsidyRequestStates.REQUESTED
        request.assignment = None
        request.course_price = course_price  # price may change by the time learner re-requests
        request.reviewer = None
        request.reviewed_at = None
        request.decline_reason = None
        request.save()  # this will handle updating course_title and partner info
        return request

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
        course_id = self.request.data.get("course_id")
        course_price = self.request.data.get("course_price")

        # Check if learner is re-requesting the same course under the same policy.
        existing_request = LearnerCreditRequest.objects.filter(
            user__lms_user_id=request.user.lms_user_id,
            learner_credit_request_config=policy.learner_credit_request_config,
            course_id=course_id,
        ).first()

        # If an existing request is found in CANCELLED, EXPIRED or REVERSED state we
        # reuse it instead of creating a new one.
        if existing_request and existing_request.state in REUSABLE_REQUEST_STATES:
            try:
                self._reuse_existing_request(existing_request, course_price)
                LearnerCreditRequestActions.create_action(
                    learner_credit_request=existing_request,
                    recent_action=get_action_choice(SubsidyRequestStates.REQUESTED),
                    status=get_user_message_choice(SubsidyRequestStates.REQUESTED),
                )
                # Trigger admin email notification with the latest request
                send_learner_credit_bnr_admins_email_with_new_requests_task.delay(
                    str(policy.uuid),
                    str(policy.learner_credit_request_config.uuid),
                    str(existing_request.enterprise_customer_uuid)
                )
                response_data = serializers.LearnerCreditRequestSerializer(existing_request).data
                return Response(response_data, status=status.HTTP_200_OK)
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception(
                    "Error reusing existing learner credit request: %s Reason: %s",
                    existing_request.uuid,
                    exc
                )
                return Response(
                    {"detail": "Failed to submit a request. Please try again."},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY
                )

        # Create a new learner credit request
        request.data.update(
            {
                "user": request.user.lms_user_id,
                "enterprise_customer_uuid": str(policy.enterprise_customer_uuid),
                "learner_credit_request_config": str(
                    policy.learner_credit_request_config.uuid
                ),
            }
        )

        response = super().create(request, *args, **kwargs)

        # --- Record the creation action ---
        if response.status_code in (status.HTTP_201_CREATED, status.HTTP_200_OK):
            lcr_uuid = response.data.get("uuid")
            if lcr_uuid:
                try:
                    lcr = LearnerCreditRequest.objects.get(uuid=lcr_uuid)
                    LearnerCreditRequestActions.create_action(
                        learner_credit_request=lcr,
                        recent_action=get_action_choice(SubsidyRequestStates.REQUESTED),
                        status=get_user_message_choice(SubsidyRequestStates.REQUESTED),
                    )

                    # Trigger admin email notification with the latest request
                    send_learner_credit_bnr_admins_email_with_new_requests_task.delay(
                        str(policy.uuid),
                        str(policy.learner_credit_request_config.uuid),
                        str(lcr.enterprise_customer_uuid)
                    )
                except LearnerCreditRequest.DoesNotExist:
                    logger.warning(f"LearnerCreditRequest {lcr_uuid} not found for action creation.")

        return response

    @permission_required(
        constants.REQUESTS_ADMIN_ACCESS_PERMISSION,
        fn=get_enterprise_uuid_from_request_data,
    )
    @action(detail=False, url_path='approve', methods=['post'])
    def approve(self, request, *args, **kwargs):
        """
        Approve a learner credit request.
        """
        # Validate the request data
        serializer = serializers.LearnerCreditRequestApproveRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        learner_credit_request_uuid = serializer.data['learner_credit_request_uuid']
        policy_uuid = serializer.data['policy_uuid']

        lc_request = LearnerCreditRequest.objects.select_related('user').get(
            uuid=learner_credit_request_uuid,
        )

        learner_email = lc_request.user.email
        content_key = lc_request.course_id
        content_price_cents = lc_request.course_price

        # Log "approve" as recent action in the Request Action model.
        lc_request_action = LearnerCreditRequestActions.create_action(
            learner_credit_request=lc_request,
            recent_action=get_action_choice(SubsidyRequestStates.APPROVED),
            status=get_user_message_choice(SubsidyRequestStates.APPROVED),
        )

        try:
            with transaction.atomic():
                # Validate the policy, once validated, approve the request by creating a content assignment.
                learner_credit_request_assignment = approve_learner_credit_request_via_policy(
                    policy_uuid,
                    content_key,
                    content_price_cents,
                    learner_email,
                    lc_request.user.lms_user_id
                )
                # link allocated assignment to the request
                lc_request.assignment = learner_credit_request_assignment
                lc_request.save()
                lc_request.approve(request.user)
                send_learner_credit_bnr_request_approve_task.delay(learner_credit_request_assignment.uuid)
            response_data = serializers.LearnerCreditRequestSerializer(lc_request).data
            return Response(
                response_data,
                status=status.HTTP_200_OK,
            )
        except SubisidyAccessPolicyRequestApprovalError as exc:
            error_msg = (
                f"[LC REQUEST APPROVAL] Failed to approve learner credit request "
                f"with UUID {learner_credit_request_uuid}. Reason: {exc.message}."
            )
            logger.exception(error_msg)

            # Update approve action with error reason.
            lc_request_action.status = get_user_message_choice(SubsidyRequestStates.REQUESTED)
            lc_request_action.error_reason = get_error_reason_choice(
                LearnerCreditRequestActionErrorReasons.FAILED_APPROVAL
            )
            lc_request_action.traceback = format_traceback(exc)
            lc_request_action.save()
            return Response({"detail": error_msg}, exc.status_code)

    @permission_required(
        constants.REQUESTS_ADMIN_ACCESS_PERMISSION,
        fn=get_enterprise_uuid_from_request_data,
    )
    @action(detail=False, url_path="bulk-approve", methods=["post"])
    def bulk_approve(self, request, *args, **kwargs):
        """
        Bulk approve learner credit requests.

        Supports two modes:
        1. Specific UUID approval: provide subsidy_request_uuids
        2. Approve all: set approve_all=True (optionally with query filters)

        Processes each request independently and returns a summary with
        approved and failed items. Partial success is allowed.
        """
        serializer = (
            serializers.LearnerCreditRequestBulkApproveRequestSerializer(
                data=request.data
            )
        )
        serializer.is_valid(raise_exception=True)
        policy_uuid = serializer.validated_data["policy_uuid"]
        approve_all = serializer.validated_data.get("approve_all", False)

        if approve_all:
            base_queryset = LearnerCreditRequest.objects.filter(
                state=SubsidyRequestStates.REQUESTED,
                learner_credit_request_config__learner_credit_config__uuid=policy_uuid,
            ).select_related("user")

            requests_to_process = self.filter_queryset(base_queryset)

            requests_by_uuid = {
                str(req.uuid): req for req in requests_to_process
            }
        else:
            subsidy_request_uuids = serializer.validated_data["subsidy_request_uuids"]
            requests_by_uuid = {
                str(req.uuid): req
                for req in LearnerCreditRequest.objects.select_related(
                    "user"
                ).filter(uuid__in=subsidy_request_uuids)
            }

        results = {"approved": [], "failed": [], "not_found": [], "skipped": []}

        approved_requests = []
        successful_request_data = []

        for uuid_val, lc_request in requests_by_uuid.items():
            if (not approve_all and lc_request.state != SubsidyRequestStates.REQUESTED):
                add_bulk_approve_operation_result(
                    results, "skipped", uuid_val, lc_request.state,
                    f"Request already in {lc_request.state} state"
                )
                continue

            learner_email = lc_request.user.email
            content_key = lc_request.course_id
            content_price_cents = lc_request.course_price

            lc_request_action = LearnerCreditRequestActions.create_action(
                learner_credit_request=lc_request,
                recent_action=get_action_choice(
                    SubsidyRequestStates.APPROVED
                ),
                status=get_user_message_choice(SubsidyRequestStates.APPROVED),
            )

            try:
                with transaction.atomic():
                    assignment = approve_learner_credit_request_via_policy(
                        policy_uuid,
                        content_key,
                        content_price_cents,
                        learner_email,
                        lc_request.user.lms_user_id,
                    )

                    # Prepare for bulk processing instead of individual saves
                    lc_request.assignment = assignment

                    approved_requests.append(lc_request)
                    successful_request_data.append({
                        'uuid': uuid_val,
                        'state': SubsidyRequestStates.APPROVED,
                        'message': "Successfully approved",
                        'assignment_uuid': assignment.uuid
                    })

            except SubisidyAccessPolicyRequestApprovalError as exc:
                error_msg = (
                    f"[LC REQUEST BULK APPROVAL] Failed to approve learner credit request "
                    f"with UUID {uuid_val}. Reason: {exc.message}."
                )
                logger.exception(error_msg)
                # Update action with error
                lc_request_action.status = get_user_message_choice(
                    SubsidyRequestStates.REQUESTED
                )
                lc_request_action.error_reason = get_error_reason_choice(
                    LearnerCreditRequestActionErrorReasons.FAILED_APPROVAL
                )
                lc_request_action.traceback = format_traceback(exc)
                lc_request_action.save()
                add_bulk_approve_operation_result(results, "failed", uuid_val, lc_request.state, exc.message)

        if approved_requests:
            try:
                with transaction.atomic():
                    LearnerCreditRequest.bulk_approve_requests(approved_requests, request.user)

                    # Send notifications and record results
                    for request_data in successful_request_data:
                        send_learner_credit_bnr_request_approve_task.delay(request_data['assignment_uuid'])
                        add_bulk_approve_operation_result(
                            results,
                            "approved",
                            request_data['uuid'],
                            request_data['state'],
                            request_data['message'],
                        )

            except (ValidationError, IntegrityError, DatabaseError) as exc:
                error_msg = f"[LC REQUEST BULK APPROVAL] Bulk update failed: {exc}"
                logger.exception(error_msg)
                for request_data in successful_request_data:
                    add_bulk_approve_operation_result(
                        results, "failed", request_data['uuid'],
                        SubsidyRequestStates.REQUESTED, str(exc)
                    )

        return Response(results, status=status.HTTP_200_OK)

    @permission_required(
        constants.REQUESTS_ADMIN_ACCESS_PERMISSION,
        fn=get_enterprise_uuid_from_request_data,
    )
    @action(
        detail=False,
        url_path='cancel',
        methods=['post'],
        serializer_class=serializers.LearnerCreditRequestCancelSerializer
    )
    def cancel(self, request, *args, **kwargs):
        """
        Cancel a learner credit request.
        """
        serializer = serializers.LearnerCreditRequestCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        learner_credit_request = serializer.get_learner_credit_request()

        error_msg = None
        lc_action = LearnerCreditRequestActions.create_action(
            learner_credit_request=learner_credit_request,
            recent_action=get_action_choice(SubsidyRequestStates.CANCELLED),
            status=get_user_message_choice(SubsidyRequestStates.CANCELLED),
        )

        try:
            with transaction.atomic():
                response = assignments_api.cancel_assignments([learner_credit_request.assignment], False)
                if response.get('non_cancelable'):
                    error_msg = (
                        f"Failed to cancel associated assignment with uuid: {learner_credit_request.assignment.uuid}"
                        f" for request: {learner_credit_request.uuid}."
                    )
                    lc_action.error_reason = get_error_reason_choice(
                        LearnerCreditRequestActionErrorReasons.FAILED_CANCELLATION
                    )
                    lc_action.status = get_user_message_choice(SubsidyRequestStates.APPROVED)
                    lc_action.traceback = error_msg
                    lc_action.save()
                    return Response(error_msg, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

                learner_credit_request.cancel(self.user)
                lc_action.save()
            send_learner_credit_bnr_cancel_notification_task.delay(
                str(learner_credit_request.assignment.uuid)
            )
            logger.info(
                f"Sent cancel notification email for learner credit request {learner_credit_request.uuid}"
            )

            serialized_request = serializers.LearnerCreditRequestSerializer(learner_credit_request).data
            return Response(serialized_request, status=status.HTTP_200_OK)
        except (ValidationError, IntegrityError, DatabaseError) as exc:
            error_msg = format_traceback(exc)
            logger.exception(error_msg)
            lc_action.error_reason = get_error_reason_choice(
                LearnerCreditRequestActionErrorReasons.FAILED_CANCELLATION
            )
            lc_action.status = get_user_message_choice(SubsidyRequestStates.APPROVED)
            lc_action.traceback = error_msg
            lc_action.save()
            return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    @permission_required(
        constants.REQUESTS_ADMIN_ACCESS_PERMISSION,
        fn=get_enterprise_uuid_from_request_data,
    )
    @action(detail=False, url_path="remind", methods=["post"])
    def remind(self, request, *args, **kwargs):
        """
        Remind a Learner that their LearnerCreditRequest is Approved and waiting for their action.
        """
        serializer = serializers.LearnerCreditRequestRemindSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        learner_credit_request = serializer.get_learner_credit_request()
        assignment = learner_credit_request.assignment

        action_instance = LearnerCreditRequestActions.create_action(
            learner_credit_request=learner_credit_request,
            recent_action=get_action_choice(LearnerCreditAdditionalActionStates.REMINDED),
            status=get_user_message_choice(LearnerCreditAdditionalActionStates.REMINDED),
        )

        try:
            send_reminder_email_for_pending_learner_credit_request.delay(assignment.uuid)
            return Response(status=status.HTTP_200_OK)
        except Exception as exc:  # pylint: disable=broad-except
            # Optionally log an errored action here if the task couldn't be queued
            action_instance.status = get_user_message_choice(LearnerCreditRequestActionErrorReasons.EMAIL_ERROR)
            action_instance.error_reason = str(exc)
            action_instance.save()
            return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    @permission_required(
        constants.REQUESTS_ADMIN_ACCESS_PERMISSION,
        fn=get_enterprise_uuid_from_request_data,
    )
    @action(detail=False, url_path="decline", methods=["post"])
    def decline(self, *args, **kwargs):
        """
        Action of declining a Learner Credit Subsidy Request
        """
        # Validate input using serializer
        serializer = serializers.LearnerCreditRequestDeclineSerializer(data=self.request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Extract validated data and the already-fetched object
        validated_data = serializer.validated_data
        learner_credit_request = serializer.get_learner_credit_request()  # No DB query!
        learner_credit_request_uuid = validated_data["subsidy_request_uuid"]
        send_notification = validated_data["send_notification"]
        disassociate_from_org = validated_data["disassociate_from_org"]

        enterprise_customer_uuid = get_enterprise_uuid_from_request_data(self.request)

        # Create the action instance before attempting the decline operation
        action_instance = LearnerCreditRequestActions.create_action(
            learner_credit_request=learner_credit_request,
            recent_action=get_action_choice(SubsidyRequestStates.DECLINED),
            status=get_user_message_choice(SubsidyRequestStates.DECLINED),
        )

        try:
            with transaction.atomic():
                learner_credit_request.decline(self.user)
        except (ValidationError, IntegrityError, DatabaseError) as exc:
            action_instance.status = get_user_message_choice(SubsidyRequestStates.REQUESTED)
            action_instance.error_reason = get_error_reason_choice(
                LearnerCreditRequestActionErrorReasons.FAILED_DECLINE
            )
            action_instance.traceback = str(exc)
            action_instance.save()

            logger.exception(f"Error declining learner credit request {learner_credit_request_uuid}: {exc}")
            return Response(
                "An error occurred while declining the request. Please try again.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Handle post-decline operations
        serialized_request = serializers.LearnerCreditRequestSerializer(learner_credit_request).data
        lms_user_id = serialized_request["lms_user_id"]

        if send_notification:
            send_learner_credit_bnr_decline_notification_task.delay(
                learner_credit_request_uuid
            )
            logger.info(
                f"Sent decline notification email for learner credit request {learner_credit_request_uuid}"
            )
        if disassociate_from_org:
            try:
                unlink_users_from_enterprise_task.delay(enterprise_customer_uuid, [lms_user_id])
            except (ConnectionError, TimeoutError, OSError) as exc:
                action_instance.status = get_user_message_choice(SubsidyRequestStates.REQUESTED)
                action_instance.error_reason = get_error_reason_choice(
                    LearnerCreditRequestActionErrorReasons.FAILED_DECLINE
                )
                action_instance.traceback = str(exc)
                action_instance.save()

                logger.exception(
                    f"Error unlinking user from enterprise for request {learner_credit_request_uuid}: {exc}"
                )

        return Response(serialized_request, status=status.HTTP_200_OK)


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
