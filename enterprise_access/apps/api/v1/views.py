"""
Views for Enterprise Access API v1.
"""

import logging

from django.core.exceptions import ObjectDoesNotExist
from django.utils.functional import cached_property
from django_filters.rest_framework import DjangoFilterBackend
from edx_rbac import utils
from edx_rbac.decorators import permission_required
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import filters, permissions, status, viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from enterprise_access.apps.api import serializers
from enterprise_access.apps.api.exceptions import SubsidyRequestCreationError
from enterprise_access.apps.api.filters import (
    SubsidyRequestCustomerConfigurationFilterBackend,
    SubsidyRequestFilterBackend
)
from enterprise_access.apps.api.tasks import delete_enterprise_subsidy_requests_task
from enterprise_access.apps.api.utils import get_enterprise_uuid_from_request_data
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
    """ Base Viewset for subsidy requests. """

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

    def _validate_subsidy_request(self):
        """
        Raises a SubsidyRequestCreationError if a subsidy request cannot be created.
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
    """ Viewset for license requests. """

    queryset = LicenseRequest.objects.order_by('-created')
    serializer_class = serializers.LicenseRequestSerializer

    subsidy_type = SubsidyTypeChoices.LICENSE

    def _validate_subsidy_request(self):
        super()._validate_subsidy_request()

        enterprise_customer_uuid = self.request.data.get('enterprise_customer_uuid')

        has_pending_request = LicenseRequest.objects.filter(
            lms_user_id=self.lms_user_id,
            enterprise_customer_uuid=enterprise_customer_uuid,
            state__in=[SubsidyRequestStates.PENDING_REVIEW, SubsidyRequestStates.APPROVED_PENDING]
        ).first()

        if has_pending_request:
            error_msg = f'User already has an outstanding license request for enterprise: {enterprise_customer_uuid}.'
            logger.exception(error_msg)
            raise SubsidyRequestCreationError(error_msg, status.HTTP_422_UNPROCESSABLE_ENTITY)

class CouponCodeRequestViewSet(SubsidyRequestViewSet):
    """ Viewset for coupon code requests. """

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
            state__in=[SubsidyRequestStates.PENDING_REVIEW, SubsidyRequestStates.APPROVED_PENDING],
            course_id=course_id
        ).exists()

        if has_pending_request:
            error_msg = f'User already has an outstanding coupon code request for course: {course_id} ' + \
                f'under enterprise: {enterprise_customer_uuid}.'
            logger.exception(error_msg)
            raise SubsidyRequestCreationError(error_msg, status.HTTP_422_UNPROCESSABLE_ENTITY)


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
            if current_config.subsidy_type and subsidy_type != current_config.subsidy_type:
                # Remove all subsidy requests of the previous type
                delete_enterprise_subsidy_requests_task.delay(pk, current_config.subsidy_type)

        return super().partial_update(request, *args, **kwargs)
