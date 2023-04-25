"""
Views for Enterprise Access API v1.
"""
import logging
import os
from collections import defaultdict
from contextlib import suppress

from celery import chain
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.db.models import Count
from django.http.request import QueryDict
from django_filters.rest_framework import DjangoFilterBackend
from edx_enterprise_subsidy_client import EnterpriseSubsidyAPIClient
from edx_rbac.decorators import permission_required
from edx_rbac.mixins import PermissionRequiredMixin
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from requests.exceptions import ConnectionError as RequestConnectionError
from requests.exceptions import HTTPError, Timeout
from rest_framework import filters, permissions
from rest_framework import serializers as rest_serializers
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ParseError
from rest_framework.generics import get_object_or_404
from rest_framework.pagination import PageNumberPagination
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
from enterprise_access.apps.events.signals import ACCESS_POLICY_CREATED, ACCESS_POLICY_UPDATED, SUBSIDY_REDEEMED
from enterprise_access.apps.events.utils import (
    send_access_policy_event_to_event_bus,
    send_subsidy_redemption_event_to_event_bus
)
from enterprise_access.apps.subsidy_access_policy.constants import POLICY_TYPES_WITH_CREDIT_LIMIT
from enterprise_access.apps.subsidy_access_policy.models import (
    SubsidyAccessPolicy,
    SubsidyAccessPolicyLockAttemptFailed
)
from enterprise_access.apps.subsidy_request.constants import SegmentEvents, SubsidyRequestStates, SubsidyTypeChoices
from enterprise_access.apps.subsidy_request.models import (
    CouponCodeRequest,
    LicenseRequest,
    SubsidyRequestCustomerConfiguration
)
from enterprise_access.apps.track.segment import track_event
from enterprise_access.utils import get_subsidy_model

logger = logging.getLogger(__name__)


class PaginationWithPageCount(PageNumberPagination):
    """
    A PageNumber paginator that adds the total number of pages to the paginated response.
    """

    page_size_query_param = 'page_size'
    max_page_size = 500

    def get_paginated_response(self, data):
        """ Adds a ``num_pages`` field into the paginated response. """
        response = super().get_paginated_response(data)
        response.data['num_pages'] = self.page.paginator.num_pages
        return response


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


class SubsidyAccessPolicyCRUDViewset(PermissionRequiredMixin, viewsets.ModelViewSet):
    """
     Viewset for Subsidy Access Policy CRUD operations.
     """

    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = serializers.SubsidyAccessPolicyCRUDSerializer
    authentication_classes = (JwtAuthentication,)
    filter_backends = (filters.OrderingFilter, DjangoFilterBackend,)
    filterset_fields = ('enterprise_customer_uuid', 'policy_type',)
    pagination_class = PaginationWithPageCount
    http_method_names = ['get', 'post', 'patch', 'delete']
    lookup_field = 'uuid'
    permission_required = 'requests.has_admin_access'

    @property
    def requested_enterprise_customer_uuid(self):
        """
        The enterprise_customer_uuid from request params or post body
        """
        enterprise_customer_uuid = None
        if self.action in ('retrieve', 'partial_update', 'destroy'):
            policy_uuid = self.kwargs.get('uuid')
            if policy_uuid:
                try:
                    policy = SubsidyAccessPolicy.objects.get(uuid=policy_uuid)
                    enterprise_customer_uuid = str(policy.enterprise_customer_uuid)
                except ObjectDoesNotExist:
                    enterprise_customer_uuid = None
        elif self.action == 'create':
            enterprise_customer_uuid = self.request.data.get('enterprise_customer_uuid')
        else:
            enterprise_customer_uuid = self.request.query_params.get('enterprise_customer_uuid', None)
        return enterprise_customer_uuid

    def get_permission_object(self):
        """
        Returns the enterprise_customer_uuid to verify that requesting user possess the enterprise admin role.
        """
        return self.requested_enterprise_customer_uuid

    def create(self, request, *args, **kwargs):
        """
        create action for SubsidyAccessPolicyCRUDViewset. Handles creation of policy after validation
        """
        policy_data = request.data
        serializer = self.get_serializer(data=policy_data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        send_access_policy_event_to_event_bus(
            ACCESS_POLICY_CREATED.event_type,
            serializer.data
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        """
        Used for http patch method. Updates the policy data passed in request after validation.
        """
        policy_data_from_request = request.data
        policy_uuid_from_url = self.kwargs.get('uuid')
        if policy_data_from_request.get('uuid') != policy_uuid_from_url:
            raise rest_serializers.ValidationError('"policy_uuid" cannot be changed')
        policy = get_object_or_404(SubsidyAccessPolicy, pk=policy_uuid_from_url)
        serializer = self.get_serializer(policy, data=policy_data_from_request, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        send_access_policy_event_to_event_bus(
            ACCESS_POLICY_UPDATED.event_type,
            serializer.data
        )
        return Response(serializer.data, status=status.HTTP_202_ACCEPTED)

    def list(self, request, *args, **kwargs):
        """
        List action for SubsidyAccessPolicyCRUDViewset. Show all policies linked with the group uuid passed in request.
        """
        enterprise_customer_uuid = self.requested_enterprise_customer_uuid
        if not enterprise_customer_uuid:
            raise rest_serializers.ValidationError('"enterprise_customer_uuid" query param is required')
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        """
        Queryset for the SubsidyAccessPolicyCRUDViewset. The admins should only be able to perform CRUD operations
        on the enterprise's policies they belong to.
        """
        queryset = SubsidyAccessPolicy.objects.order_by('-created')
        enterprise_customer_uuid = self.requested_enterprise_customer_uuid
        return queryset.filter(enterprise_customer_uuid=enterprise_customer_uuid)


class RedemptionRequestException(APIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = 'Could not redeem'


class SubsidyAccessPolicyLockedException(APIException):
    """
    Throw this exception when an attempt to acquire a policy lock failed because it was already locked by another agent.

    Note: status.HTTP_423_LOCKED is NOT acceptable as a status code for delivery to web browsers.  According to Mozilla:

      > The ability to lock a resource is specific to some WebDAV servers. Browsers accessing web pages will never
      > encounter this status code; in the erroneous cases it happens, they will handle it as a generic 400 status code.

    See: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/423

    HTTP 429 Too Many Requests is the next best thing, and implies retryability.
    """
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = 'Enrollment currently locked for this subsidy access policy.'


class SubsidyAccessPolicyRedeemViewset(UserDetailsFromJwtMixin, PermissionRequiredMixin, viewsets.GenericViewSet):
    """
    Viewset for Subsidy Access Policy APIs.
    """
    authentication_classes = [JwtAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    lookup_url_kwarg = 'policy_uuid'
    permission_required = 'requests.has_learner_or_admin_access'

    @property
    def enterprise_customer_uuid(self):
        """Returns the enterprise customer uuid from query params or request data based on action type. """
        enterprise_uuid = ''

        if self.action in ('list', 'redemption', 'credits_available'):
            enterprise_uuid = self.request.query_params.get('enterprise_customer_uuid')

        if self.action == 'redeem':
            policy_uuid = self.kwargs.get('policy_uuid')
            with suppress(ValidationError):  # Ignore if `policy_uuid` is not a valid uuid
                policy = SubsidyAccessPolicy.objects.filter(uuid=policy_uuid).first()
                if policy:
                    enterprise_uuid = policy.enterprise_customer_uuid

        if self.action == 'can_redeem':
            enterprise_uuid = self.kwargs.get('enterprise_customer_uuid')

        return enterprise_uuid

    def get_permission_object(self):
        """
        Returns the enterprise uuid to verify that requesting user possess the enterprise learner or admin role.
        """
        return self.enterprise_customer_uuid

    def get_queryset(self):
        queryset = SubsidyAccessPolicy.objects.order_by('-created')
        enterprise_customer_uuid = self.enterprise_customer_uuid
        return queryset.filter(enterprise_customer_uuid=enterprise_customer_uuid)

    def evaluate_policies(self, enterprise_customer_uuid, learner_id, content_key):
        """
        Evaluate all policies for the given enterprise customer to check if it can be redeemed against the given learner
        and content.

        Note: Calling this will cause multiple backend API calls to the enterprise-subsidy can_redeem endpoint, one for
        each access policy evaluated.

        Returns:
            tuple of (list of SubsidyAccessPolicy, dict mapping str -> list of SubsidyAccessPolicy): The first tuple
            element is a list of redeemable policies, and the second tuple element is a mapping of reason strings to
            non-redeemable policies.  The reason strings are non-specific, short explanations for why each bucket of
            policies has been deemed non-redeemable.
        """
        redeemable_policies = []
        non_redeemable_policies = defaultdict(list)
        all_policies_for_enterprise = SubsidyAccessPolicy.objects.filter(
            enterprise_customer_uuid=enterprise_customer_uuid
        )
        for policy in all_policies_for_enterprise:
            redeemable, reason = policy.can_redeem(learner_id, content_key)
            if redeemable:
                redeemable_policies.append(policy)
            else:
                # Aggregate the reasons for policies not being redeemable.  This really only works if the reason string
                # is short and generic because the bucketing logic simply treats entire string as the bucket key.
                non_redeemable_policies[reason].append(policy)

        return (redeemable_policies, non_redeemable_policies)

    def policies_with_credit_available(self, enterprise_customer_uuid, learner_id):
        """
        Return all redeemable policies in terms of "credit available".
        """
        policies = []
        all_policies = SubsidyAccessPolicy.objects.filter(
            policy_type__in=POLICY_TYPES_WITH_CREDIT_LIMIT,
            enterprise_customer_uuid=enterprise_customer_uuid
        )
        for policy in all_policies:
            if policy.credit_available(learner_id):
                policies.append(policy)

        return policies

    @action(detail=False, methods=['get'])
    def credits_available(self, request):
        """
        Return a list of all redeemable policies for given `enterprise_customer_uuid`, `lms_user_id` that have
        redeemable credit available.
        """
        serializer = serializers.SubsidyAccessPolicyCreditAvailableListSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        enterprise_customer_uuid = serializer.data['enterprise_customer_uuid']
        learner_id = serializer.data['lms_user_id']

        policies_with_credit_available = self.policies_with_credit_available(enterprise_customer_uuid, learner_id)
        response_data = serializers.SubsidyAccessPolicyCreditAvailableSerializer(
            policies_with_credit_available,
            many=True,
            context={'learner_id': learner_id}
        ).data

        return Response(
            response_data,
            status=status.HTTP_200_OK,
        )

    def list(self, request):
        """
        Return a list of all redeemable policies for given `enterprise_customer_uuid`, `learner_id` and `content_key`
        """
        serializer = serializers.SubsidyAccessPolicyRedeemListSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        enterprise_customer_uuid = serializer.data['enterprise_customer_uuid']
        learner_id = serializer.data['learner_id']
        content_key = serializer.data['content_key']

        redeemable_policies, _ = self.evaluate_policies(enterprise_customer_uuid, learner_id, content_key)
        response_data = serializers.SubsidyAccessPolicyRedeemableSerializer(redeemable_policies, many=True).data

        return Response(
            response_data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=['post'])
    def redeem(self, request, *args, **kwargs):
        """
        Redeem a policy for given `learner_id` and `content_key`

        URL Location: POST /api/v1/policy/<policy_uuid>/redeem/?learner_id=<>&content_key=<>

        status codes:
            400: There are missing or otherwise invalid input parameters.
            403: The requester has insufficient redeem permissions.
            422: The subisdy access policy is not redeemable in a way that IS NOT retryable.
            429: The subisdy access policy is not redeemable in a way that IS retryable (e.g. policy currently locked).
            200: The policy was successfully redeemed.  Response body is JSON with a serialized Transaction
                 containing the following keys (sample values):
                 {
                     "uuid": "the-transaction-uuid",
                     "state": "COMMITTED",
                     "idempotency_key": "the-idempotency-key",
                     "lms_user_id": 54321,
                     "content_key": "demox_1234+2T2023",
                     "quantity": 19900,
                     "unit": "USD_CENTS",
                     "reference_id": 1234,
                     "reference_type": "enterprise_fufillment_source_uuid",
                     "subsidy_access_policy_uuid": "a-policy-uuid",
                     "metadata": {...},
                     "created": "created-datetime",
                     "modified": "modified-datetime",
                     "reversals": []
                 }
        """
        policy = get_object_or_404(SubsidyAccessPolicy, pk=kwargs.get('policy_uuid'))

        serializer = serializers.SubsidyAccessPolicyRedeemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        learner_id = serializer.data['learner_id']
        content_key = serializer.data['content_key']
        try:
            # For now, we should lock the whole policy (i.e. pass nothing to policy.lock()).  In some cases this is more
            # aggressive than necessary, but we can optimize for performance at a later phase of this project.  At that
            # point, we should also consider migrating this logic into the policy model so that different policy types
            # that have different locking needs can supply different lock kwargs.
            with policy.lock():
                if policy.can_redeem(learner_id, content_key):
                    redemption_result = policy.redeem(learner_id, content_key)
                    send_subsidy_redemption_event_to_event_bus(
                        SUBSIDY_REDEEMED.event_type,
                        serializer.data
                    )
                    return Response(redemption_result, status=status.HTTP_200_OK)
                else:
                    raise RedemptionRequestException()
        except SubsidyAccessPolicyLockAttemptFailed as exc:
            logger.exception(exc)
            raise SubsidyAccessPolicyLockedException() from exc

    def get_redemptions_by_policy_uuid(self, enterprise_customer_uuid, learner_id, content_key):
        """
        Get existing redemptions for the given enterprise, learner, and content, bucketed by policy.

        Note: Calling this will cause multiple backend API calls to the enterprise-subsidy can_redeem endpoint, one for
        each access policy evaluated.

        Returns:
            dict of list of policy data: mapping of policy UUID to a list of deserialized ledger transactions. e.g.:
                {
                    "316b0f76-a69c-464b-93d7-7c0142f003aa": [
                        {
                            "uuid": "26cdce7f-b13d-46fe-a395-06d8a50932e9",
                            "state": "committed",
                            "idempotency_key": "the-idempotency-key",
                            "learner_id": 54321,
                            "content_key": "course-v1:demox+1234+2T2023",
                            "quantity": -19900,
                            "unit": "USD_CENTS",
                            "reference_id": "6ff2c1c9-d5fc-48a8-81da-e6a675263f67",
                            "reference_type": "enterprise_fufillment_source_uuid",
                            "subsidy_access_policy_uuid": "ac4cca18-4857-402d-963a-790c2f6fcc53",
                            "metadata": {...},
                            "created": <created-datetime>,
                            "modified": <modified-datetime>,
                            "reversals": [],
                            "policy_redemption_status_url": <API URL to check redemption status>,
                            "courseware_url": "https://courses.edx.org/courses/course-v1:demox+1234+2T2023/courseware/",
                        },
                    ]
                }
        """
        redemptions_by_policy_uuid = {}
        policies = SubsidyAccessPolicy.objects.filter(enterprise_customer_uuid=enterprise_customer_uuid)

        for policy in policies:
            if redemptions := policy.redemptions(learner_id, content_key):
                for redemption in redemptions:
                    redemption["policy_redemption_status_url"] = os.path.join(
                        EnterpriseSubsidyAPIClient.TRANSACTIONS_ENDPOINT,
                        f"{redemption['uuid']}/",
                    )
                    # TODO: this is currently hard-coded to only support OCM courses.
                    redemption["courseware_url"] = os.path.join(
                        settings.LMS_URL,
                        f"courses/{redemption['content_key']}/courseware/",
                    )
                redemptions_by_policy_uuid[str(policy.uuid)] = redemptions

        return redemptions_by_policy_uuid

    @action(detail=False, methods=['get'])
    def redemption(self, request, *args, **kwargs):
        """
        Return redemption records for given `enterprise_customer_uuid`, `learner_id` and `content_key`

        URL Location: GET /api/v1/policy/redemption/?enterprise_customer_uuid=<>&learner_id=<>&content_key=<>
        """
        serializer = serializers.SubsidyAccessPolicyRedeemListSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        enterprise_customer_uuid = serializer.data['enterprise_customer_uuid']
        learner_id = serializer.data['learner_id']
        content_key = serializer.data['content_key']

        return Response(
            self.get_redemptions_by_policy_uuid(enterprise_customer_uuid, learner_id, content_key),
            status=status.HTTP_200_OK,
        )

    @action(
        detail=False,
        methods=['get'],
        url_name='can-redeem',
        # TODO: more precise UUID pattern?
        url_path='enterprise-customer/(?P<enterprise_customer_uuid>[^/.]+)/can-redeem',
    )
    def can_redeem(self, request, enterprise_customer_uuid=None):
        """
        Retrieve single, redeemable access policy for a set of content keys.

        URL Location: GET /api/v1/policy/enterprise-customer/<enterprise_customer_uuid>/can-redeem/
                          ?content_key=<>&content_key=<>&...&content_key=<>

        Request Args:
            enterprise_customer_uuid (URL, required): The enterprise customer to answer this question about.
            content_key (query parameter, multiple, required): Possibly multiple content_keys to run this query against.

        Returns:
            rest_framework.response.Response:
                400: If there are missing or otherwise invalid input parameters.  Response body is JSON with a single
                     `Error` key.
                403: If the requester has insufficient permissions, Response body is JSON with a single `Error` key.
                201: If a redeemable access policy was found, an existing redemption was found, or neither.  Response
                     body is a JSON list of dict containing redemption evaluations for each given content_key.  See
                     below for a sample response to 3 passed content_keys: one which has existing redemptions, one
                     without, and a third that is not redeemable.:
                     [
                         {
                             "content_key": "course-v1:demox+1234+2T2023_1",
                             "redemptions": [
                                 {
                                     "uuid": "26cdce7f-b13d-46fe-a395-06d8a50932e9",
                                     "state": "committed",
                                     "policy_redemption_status_url": <API URL to check the redemtion status>,
                                     "courseware_url": <URL to the courseware page>,
                                     <remainder of serialized Transaction>
                                 },
                             ],
                             "subsidy_access_policy": {
                                 "uuid": "56744a36-93ac-4e6c-b998-a2a1899f2ae4",
                                 "policy_redemption_url": <API URL to redeem the policy>,
                                 <remainder of serialized SubsidyAccessPolicy>
                             },
                             "reasons": []
                         },
                         {
                             "content_key": "course-v1:demox+1234+2T2023_2",
                             "redemptions": [],
                             "subsidy_access_policy": {
                                 "uuid": "56744a36-93ac-4e6c-b998-a2a1899f2ae4",
                                 "policy_redemption_url": <API URL to redeem the policy>,
                                 <remainder of serialized SubsidyAccessPolicy>
                             },
                             "reasons": []
                         },
                         {
                             "content_key": "course-v1:demox+1234+2T2023_3",
                             "redemptions": [],
                             "subsidy_access_policy": null,
                             "reasons": [
                                 {
                                     "reason": "Not enough funds available for the course.",
                                     "policy_uuids": ["56744a36-93ac-4e6c-b998-a2a1899f2ae4"],
                                 }
                             ]
                         }
                         ...
                     ]
        """
        all_request_params = QueryDict(mutable=True)
        all_request_params.update(request.query_params)
        all_request_params.update({"enterprise_customer_uuid": enterprise_customer_uuid})
        serializer = serializers.SubsidyAccessPolicyCanRedeemRequestSerializer(data=all_request_params)
        serializer.is_valid(raise_exception=True)

        enterprise_customer_uuid = serializer.data['enterprise_customer_uuid']
        content_keys = serializer.data['content_key']
        learner_id = self.lms_user_id

        response = []
        for content_key in content_keys:
            serialized_policy = None
            reasons = []

            redemptions_by_policy_uuid = self.get_redemptions_by_policy_uuid(
                enterprise_customer_uuid,
                learner_id,
                content_key
            )
            # Flatten dict of lists because the response doesn't need to be bucketed by policy_uuid.
            redemptions = [
                redemption
                for redemptions in redemptions_by_policy_uuid.values()
                for redemption in redemptions
            ]
            redeemable_policies, non_redeemable_policies = self.evaluate_policies(
                enterprise_customer_uuid, learner_id, content_key
            )
            if not redemptions and not redeemable_policies:
                for reason, policies in non_redeemable_policies.items():
                    reasons.append({
                        "reason": reason,
                        "policy_uuids": [policy.uuid for policy in policies],
                    })
            if redeemable_policies:
                resolved_policy = SubsidyAccessPolicy.resolve_policy(redeemable_policies)
                serialized_policy = serializers.SubsidyAccessPolicyRedeemableSerializer(resolved_policy).data

            has_successful_redemption = any(redemption['state'] == 'committed' for redemption in redemptions)
            can_redeem_for_content_response = {
                "content_key": content_key,
                "redemptions": redemptions,
                "has_redeemed": has_successful_redemption,
                "redeemable_subsidy_access_policy": serialized_policy,
                "can_redeem": bool(serialized_policy),
                "reasons": reasons,
            }
            response.append(can_redeem_for_content_response)

        return Response(response, status=status.HTTP_200_OK)
