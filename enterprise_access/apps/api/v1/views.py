"""
Views for Enterprise Access API v1.
"""
from celery import chain
import logging

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils.functional import cached_property
from django_filters.rest_framework import DjangoFilterBackend
from edx_rbac import utils
from edx_rbac.decorators import permission_required
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from requests.exceptions import ConnectionError as RequestConnectionError
from requests.exceptions import HTTPError, Timeout
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ParseError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from enterprise_access.apps.api import serializers
from enterprise_access.apps.api.constants import LICENSE_UNASSIGNED_STATUS
from enterprise_access.apps.api.exceptions import SubsidyRequestCreationError, SubsidyRequestError
from enterprise_access.apps.api.filters import (
    SubsidyRequestCustomerConfigurationFilterBackend,
    SubsidyRequestFilterBackend
)
from enterprise_access.apps.api.tasks import decline_enterprise_subsidy_requests_task
from enterprise_access.apps.api.utils import get_enterprise_uuid_from_request_data, validate_uuid
from enterprise_access.apps.api_client.ecommerce_client import EcommerceApiClient
from enterprise_access.apps.api_client.license_manager_client import LicenseManagerApiClient
from enterprise_access.apps.core import constants
from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates, SubsidyTypeChoices
from enterprise_access.apps.subsidy_request.models import (
    CouponCodeRequest,
    LicenseRequest,
    SubsidyRequestCustomerConfiguration
)

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

class SubsidyRequestViewSet(viewsets.ModelViewSet):
    """
    Base Viewset for subsidy requests.
    """

    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = serializers.SubsidyRequestSerializer
    list_lookup_field = 'enterprise_customer_uuid'

    authentication_classes = (JwtAuthentication,)

    filter_backends = (filters.OrderingFilter, DjangoFilterBackend, SubsidyRequestFilterBackend,)
    filterset_fields = ('uuid', 'state', 'course_id', 'enterprise_customer_uuid')
    pagination_class = PaginationWithPageCount

    http_method_names = ['get', 'post']

    subsidy_type = None

    @cached_property
    def decoded_jwt(self):
        return utils.get_decoded_jwt(self.request)

    @property
    def lms_user_id(self):
        return self.decoded_jwt.get('user_id')

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

        # Set the lms user id for the request
        request.data['lms_user_id'] = self.lms_user_id
        return super().create(request, *args, **kwargs)


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
            lms_user_id=self.lms_user_id,
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
            subscription_overview = client.get_subscription_overview(subscription_plan_uuid)
        except (RequestConnectionError, HTTPError, Timeout) as exc:
            error_msg = (
                'We were unable to approve/decline the selected license requests at this time. '
                'Please try again in a few minutes, or reach out to customer support if you '
                'continue to experience issues. '
            )
            raise SubsidyRequestError(error_msg, status.HTTP_500_INTERNAL_SERVER_ERROR) from exc
        status_counts = subscription_overview.get('results')

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
        subsidy_request_uuids = self.request.data.get('subsidy_request_uuids')
        subscription_plan_uuid = self.request.data.get('subscription_plan_uuid')
        reviewer_lms_user_id = self.lms_user_id

        try:
            self._validate_subsidy_request_uuids(subsidy_request_uuids)
            self._validate_subscription_plan_uuid(subscription_plan_uuid)
            self._verify_subsidies_remaining(subscription_plan_uuid, subsidy_request_uuids)
        except SubsidyRequestError as exc:
            logger.exception(exc)
            return Response(exc.message, exc.http_status_code)

        subsidy_requests = LicenseRequest.objects.filter(
            uuid__in=subsidy_request_uuids,
            enterprise_customer_uuid=enterprise_customer_uuid,
        )

        try:
            self._raise_error_if_any_requests_match_incorrect_states(
                subsidy_requests,
                incorrect_states=[SubsidyRequestStates.DECLINED],
                current_action=SubsidyRequestStates.APPROVED
            )
        except SubsidyRequestError as exc:
            logger.exception(exc)
            return Response(exc.message, exc.http_status_code)

        subsidies_to_approve = subsidy_requests.filter(
            state__in=[SubsidyRequestStates.REQUESTED, SubsidyRequestStates.ERROR]
        )
        with transaction.atomic():
            for subsidy_request in subsidies_to_approve:
                subsidy_request.approve(reviewer_lms_user_id)

        # All requests successfully approved, you may now spin off tasks
        # my_celery_task(subsidies_to_approve)
        print('SPIN OFF CELERY TASK TO DO THE ASSIGNMENT')

        serialized_subsidy_requests = serializers.LicenseRequestSerializer(subsidies_to_approve, many=True)

        return Response(
            serialized_subsidy_requests.data,
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
        subsidy_request_uuids = self.request.data.get('subsidy_request_uuids')
        reviewer_lms_user_id = self.lms_user_id

        try:
            self._validate_subsidy_request_uuids(subsidy_request_uuids)
        except SubsidyRequestError as exc:
            logger.exception(exc)
            return Response(exc.message, exc.http_status_code)

        subsidy_requests = LicenseRequest.objects.filter(
            uuid__in=subsidy_request_uuids,
            enterprise_customer_uuid=enterprise_customer_uuid,
        )

        try:
            self._raise_error_if_any_requests_match_incorrect_states(
                subsidy_requests,
                incorrect_states=[SubsidyRequestStates.APPROVED, SubsidyRequestStates.PENDING],
                current_action=SubsidyRequestStates.DECLINED
            )
        except SubsidyRequestError as exc:
            logger.exception(exc)
            return Response(exc.message, exc.http_status_code)

        subsidies_to_decline = subsidy_requests.filter(
            state__in=[SubsidyRequestStates.REQUESTED, SubsidyRequestStates.ERROR]
        )
        with transaction.atomic():
            for subsidy_request in subsidies_to_decline:
                subsidy_request.decline(reviewer_lms_user_id)

        serialized_subsidy_requests = serializers.LicenseRequestSerializer(subsidies_to_decline, many=True)

        return Response(
            serialized_subsidy_requests.data,
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
            lms_user_id=self.lms_user_id,
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
        subsidy_request_uuids = self.request.data.get('subsidy_request_uuids')
        coupon_id = self.request.data.get('coupon_id')
        reviewer_lms_user_id = self.lms_user_id

        try:
            self._validate_subsidy_request_uuids(subsidy_request_uuids)
            self._validate_redemptions_remaining(enterprise_customer_uuid, coupon_id, subsidy_request_uuids)
        except SubsidyRequestError as exc:
            logger.exception(exc)
            return Response(exc.message, exc.http_status_code)

        subsidy_requests = CouponCodeRequest.objects.filter(
            uuid__in=subsidy_request_uuids,
            enterprise_customer_uuid=enterprise_customer_uuid,
        )

        try:
            self._raise_error_if_any_requests_match_incorrect_states(
                subsidy_requests,
                incorrect_states=[SubsidyRequestStates.DECLINED],
                current_action=SubsidyRequestStates.APPROVED
            )
        except SubsidyRequestError as exc:
            logger.exception(exc)
            return Response(exc.message, exc.http_status_code)

        subsidies_to_approve = subsidy_requests.filter(
            state__in=[SubsidyRequestStates.REQUESTED, SubsidyRequestStates.ERROR]
        )
        with transaction.atomic():
            for subsidy_request in subsidies_to_approve:
                subsidy_request.approve(reviewer_lms_user_id)

        # All requests successfully approved, you may now spin off tasks
        # my_celery_task(subsidies_to_approve)
        print('SPIN OFF CELERY TASK TO DO THE ASSIGNMENT')

        serialized_subsidy_requests = serializers.CouponCodeRequestSerializer(subsidies_to_approve, many=True)

        return Response(
            serialized_subsidy_requests.data,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, url_path='decline', methods=['post'])
    def decline(self, *args, **kwargs):
        """
        Action of declining a Coupon Subsidy Request
        """

        enterprise_customer_uuid = get_enterprise_uuid_from_request_data(self.request)
        subsidy_request_uuids = self.request.data.get('subsidy_request_uuids')
        reviewer_lms_user_id = self.lms_user_id

        try:
            self._validate_subsidy_request_uuids(subsidy_request_uuids)
        except SubsidyRequestError as exc:
            logger.exception(exc)
            return Response(exc.message, exc.http_status_code)

        subsidy_requests = CouponCodeRequest.objects.filter(
            uuid__in=subsidy_request_uuids,
            enterprise_customer_uuid=enterprise_customer_uuid,
        )

        try:
            self._raise_error_if_any_requests_match_incorrect_states(
                subsidy_requests,
                incorrect_states=[SubsidyRequestStates.APPROVED, SubsidyRequestStates.PENDING],
                current_action=SubsidyRequestStates.DECLINED
            )
        except SubsidyRequestError as exc:
            logger.exception(exc)
            return Response(exc.message, exc.http_status_code)

        subsidies_to_decline = subsidy_requests.filter(
            state__in=[SubsidyRequestStates.REQUESTED, SubsidyRequestStates.ERROR]
        )
        with transaction.atomic():
            for subsidy_request in subsidies_to_decline:
                subsidy_request.decline(reviewer_lms_user_id)

        serialized_subsidy_requests = serializers.CouponCodeRequestSerializer(subsidies_to_decline, many=True)

        return Response(
            serialized_subsidy_requests.data,
            status=status.HTTP_200_OK,
        )


class SubsidyRequestCustomerConfigurationViewSet(viewsets.ModelViewSet):
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
        return super().create(request, *args, **kwargs)

    @permission_required(
        constants.REQUESTS_ADMIN_ACCESS_PERMISSION,
        fn=lambda request, pk: pk
    )
    def partial_update(self, request, *args, **kwargs):
        pk = kwargs['pk']
        current_config = SubsidyRequestCustomerConfiguration.objects.get(pk=pk)

        if 'subsidy_type' in request.data:

            subsidy_type = request.data['subsidy_type']
            send_notification = request.data['send_notification']

            if current_config.subsidy_type and subsidy_type != current_config.subsidy_type:
                decline_enterprise_subsidy_requests_task.delay(pk, current_config.subsidy_type, send_notification)

        return super().partial_update(request, *args, **kwargs)
