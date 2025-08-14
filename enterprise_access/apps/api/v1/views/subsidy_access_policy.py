"""
REST API views for the subsidy_access_policy app.
"""
import logging
import math
import os
from collections import defaultdict
from contextlib import suppress
from urllib import parse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.functional import cached_property
from drf_spectacular.utils import extend_schema
from edx_enterprise_subsidy_client import EnterpriseSubsidyAPIClient
from edx_rbac.decorators import permission_required
from edx_rbac.mixins import PermissionRequiredMixin
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from requests.exceptions import HTTPError
from rest_framework import authentication, mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework_csv.renderers import CSVRenderer

from enterprise_access.apps.api import filters, serializers, utils
from enterprise_access.apps.api.mixins import UserDetailsFromJwtMixin
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.content_assignments.api import AllocationException
from enterprise_access.apps.content_metadata.api import get_and_cache_content_metadata
from enterprise_access.apps.core.constants import (
    SUBSIDY_ACCESS_POLICY_ALLOCATION_PERMISSION,
    SUBSIDY_ACCESS_POLICY_READ_PERMISSION,
    SUBSIDY_ACCESS_POLICY_REDEMPTION_PERMISSION,
    SUBSIDY_ACCESS_POLICY_WRITE_PERMISSION
)
from enterprise_access.apps.events.signals import SUBSIDY_REDEEMED
from enterprise_access.apps.events.utils import send_subsidy_redemption_event_to_event_bus
from enterprise_access.apps.subsidy_access_policy.constants import (
    GROUP_MEMBERS_WITH_AGGREGATES_DEFAULT_PAGE_SIZE,
    REASON_BEYOND_ENROLLMENT_DEADLINE,
    REASON_CONTENT_NOT_IN_CATALOG,
    REASON_LEARNER_ASSIGNMENT_CANCELLED,
    REASON_LEARNER_ASSIGNMENT_FAILED,
    REASON_LEARNER_MAX_ENROLLMENTS_REACHED,
    REASON_LEARNER_MAX_SPEND_REACHED,
    REASON_LEARNER_NOT_ASSIGNED_CONTENT,
    REASON_LEARNER_NOT_IN_ENTERPRISE,
    REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY,
    REASON_POLICY_EXPIRED,
    REASON_POLICY_SPEND_LIMIT_REACHED,
    REASON_SUBSIDY_EXPIRED,
    SORT_BY_ENROLLMENT_COUNT,
    MissingSubsidyAccessReasonUserMessages,
    TransactionStateChoices
)
from enterprise_access.apps.subsidy_access_policy.content_metadata_api import make_list_price_dict
from enterprise_access.apps.subsidy_access_policy.exceptions import (
    ContentPriceNullException,
    MissingAssignment,
    SubsidyAPIHTTPError
)
from enterprise_access.apps.subsidy_access_policy.models import (
    PolicyGroupAssociation,
    SubsidyAccessPolicy,
    SubsidyAccessPolicyLockAttemptFailed
)
from enterprise_access.apps.subsidy_access_policy.subsidy_api import (
    get_and_cache_subsidy_learners_aggregate_data,
    get_redemptions_by_content_and_policy_for_learner
)
from enterprise_access.apps.subsidy_access_policy.utils import sort_subsidy_access_policies_for_redemption
from enterprise_access.apps.subsidy_request.constants import LC_NON_RE_REQUESTABLE_STATES
from enterprise_access.apps.subsidy_request.models import LearnerCreditRequest

from .utils import PaginationWithPageCount

logger = logging.getLogger(__name__)

SUBSIDY_ACCESS_POLICY_CRUD_API_TAG = 'Subsidy Access Policies CRUD'
SUBSIDY_ACCESS_POLICY_REDEMPTION_API_TAG = 'Subsidy Access Policy Redemption'
SUBSIDY_ACCESS_POLICY_ALLOCATION_API_TAG = 'Subsidy Access Policy Allocation'
GROUP_MEMBER_DATA_WITH_AGGREGATES_API_TAG = 'Group Member Data With Aggregates'
DELETE_POLICY_GROUP_ASSOCIATION_API_TAG = 'Delete Policy Group Association'


def group_members_with_aggregates_next_page(current_url):
    """Helper method to create the next page url"""
    parsed_url = parse.urlparse(current_url)
    parsed_query = parse.parse_qs(parsed_url.query)
    parsed_query_page = parsed_query['page']
    parsed_query_page[0] = str(int(parsed_query_page[0]) + 1)
    parsed_url._replace(query=parsed_query)
    return parse.urlunparse(parsed_url)


def group_members_with_aggregates_previous_page(current_url):
    """Helper method to create the previous page url"""
    parsed_url = parse.urlparse(current_url)
    parsed_query = parse.parse_qs(parsed_url.query)
    parsed_query_page = parsed_query['page']
    parsed_query_page[0] = str(int(parsed_query_page[0]) - 1)
    parsed_url._replace(query=parsed_query)
    return parse.urlunparse(parsed_url)


def _update_pagination_params_for_group_aggregates(
    member_response,
    traverse_pagination,
    sort_by_enrollment_count,
    page_index_start,
    request,
    num_member_results,
):
    """
    Helper method to construct the api response object with next and previous values
    """
    current_url = request.build_absolute_uri()
    if not traverse_pagination:
        # If sorting by enrollment count and a provided page, we have to be more clever about when to construct next
        # and previous values
        has_next_page = (page_index_start + GROUP_MEMBERS_WITH_AGGREGATES_DEFAULT_PAGE_SIZE) < num_member_results
        sort_by_enrollment_count_next = (
            has_next_page and sort_by_enrollment_count
        )
        if member_response.get('next') or sort_by_enrollment_count_next:
            member_response['next'] = group_members_with_aggregates_next_page(current_url)
        if member_response.get('previous') or (page_index_start > 0 and sort_by_enrollment_count):
            member_response['previous'] = group_members_with_aggregates_previous_page(current_url)
    else:
        member_response['next'] = None
        member_response['previous'] = None

    if sort_by_enrollment_count:
        member_response['num_pages'] = math.ceil(num_member_results / GROUP_MEMBERS_WITH_AGGREGATES_DEFAULT_PAGE_SIZE)


def zip_group_members_data_with_enrollment_count(member_results, subsidy_learner_aggregate_dict):
    """Helper method to zip group member results with aggregate data from the subsidy service"""
    for key, result in enumerate(member_results):
        enrollment_count = 0
        if lms_user_id := result.get('lms_user_id'):
            enrollment_count = subsidy_learner_aggregate_dict.get(lms_user_id, 0)
        result['enrollment_count'] = enrollment_count
        member_results[key] = result
    return member_results


def policy_permission_detail_fn(request, *args, uuid=None, **kwargs):
    """
    Helper to use with @permission_required on detail-type endpoints (retrieve, update, partial_update, destroy).

    Args:
        uuid (str): UUID representing a SubsidyAccessPolicy object.
    """
    return utils.get_policy_customer_uuid(uuid)


def _get_reasons_for_no_redeemable_policies(enterprise_customer_uuid, non_redeemable_policies_by_reason):
    """
    Serialize a reason for non-redeemability, and fetch enterprise admin user that learner can contact,
    for each non-redeemable policy.

    Params:
      enterprise_customer_uuid: The customer UUID related to the non-redeemable policies.  Used
        for fetching customer admin users via the LMS API client.
      non_redeemable_policies_by_reason: Mapping of unredeemable/unallocatable policy reasons
        to lists of policy records for which that reason holds.

    Returns:
      A list of dictionaries, one per reason, that contains the reason constant, a user-facing
      message, and a list of policy UUIDs for which that reason holds.
    """
    reasons = []
    display_reason = None
    lms_client = LmsApiClient()
    enterprise_customer_data = lms_client.get_enterprise_customer_data(enterprise_customer_uuid)
    admin_contact = _get_admin_contact_email(enterprise_customer_data)

    for reason, policies in non_redeemable_policies_by_reason.items():
        user_message = _get_user_message_for_reason(reason, admin_contact)
        reasons.append({
            "reason": reason,
            "user_message": user_message,
            "metadata": {
                "enterprise_administrators": admin_contact,
            },
            "policy_uuids": [policy.uuid for policy in policies],
        })

    # Validate a reason exist before picking the most relevant reason already assumed to
    # be the first item in the list of non-redeemable policies sorted by the most salient policy
    if len(reasons) > 0:
        display_reason = reasons[0]

    return reasons, display_reason


def _get_admin_contact_email(enterprise_customer_data):
    """
    Return the point of contact email for an enterprise customer.
    """
    if admin_contact_email := enterprise_customer_data.get('contact_email'):
        return [{
            "email": admin_contact_email,
            "lms_user_id": None,
        }]
    return enterprise_customer_data.get('admin_users', [])


def _get_user_message_for_reason(reason_slug, enterprise_admin_users):
    """
    Return the user-facing message for a given reason slug.
    """
    if not reason_slug:
        return None

    has_enterprise_admin_users = len(enterprise_admin_users) > 0

    user_message_organization_no_funds = (
        MissingSubsidyAccessReasonUserMessages.ORGANIZATION_NO_FUNDS
        if has_enterprise_admin_users
        else MissingSubsidyAccessReasonUserMessages.ORGANIZATION_NO_FUNDS_NO_ADMINS
    )

    user_message_organization_fund_expired = (
        MissingSubsidyAccessReasonUserMessages.ORGANIZATION_EXPIRED_FUNDS
        if has_enterprise_admin_users
        else MissingSubsidyAccessReasonUserMessages.ORGANIZATION_EXPIRED_FUNDS_NO_ADMINS
    )

    MISSING_SUBSIDY_ACCESS_POLICY_REASONS = {
        REASON_POLICY_EXPIRED: user_message_organization_no_funds,
        REASON_SUBSIDY_EXPIRED: user_message_organization_fund_expired,
        REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY: user_message_organization_no_funds,
        REASON_POLICY_SPEND_LIMIT_REACHED: user_message_organization_no_funds,
        REASON_LEARNER_NOT_IN_ENTERPRISE: MissingSubsidyAccessReasonUserMessages.LEARNER_NOT_IN_ENTERPRISE,
        REASON_LEARNER_MAX_SPEND_REACHED: MissingSubsidyAccessReasonUserMessages.LEARNER_LIMITS_REACHED,
        REASON_LEARNER_MAX_ENROLLMENTS_REACHED: MissingSubsidyAccessReasonUserMessages.LEARNER_LIMITS_REACHED,
        REASON_CONTENT_NOT_IN_CATALOG: MissingSubsidyAccessReasonUserMessages.CONTENT_NOT_IN_CATALOG,
        REASON_BEYOND_ENROLLMENT_DEADLINE: MissingSubsidyAccessReasonUserMessages.BEYOND_ENROLLMENT_DEADLINE,
        REASON_LEARNER_NOT_ASSIGNED_CONTENT: MissingSubsidyAccessReasonUserMessages.LEARNER_NOT_ASSIGNED_CONTENT,
        REASON_LEARNER_ASSIGNMENT_CANCELLED: MissingSubsidyAccessReasonUserMessages.LEARNER_ASSIGNMENT_CANCELED,
        REASON_LEARNER_ASSIGNMENT_FAILED: MissingSubsidyAccessReasonUserMessages.LEARNER_NOT_ASSIGNED_CONTENT,
    }

    if reason_slug not in MISSING_SUBSIDY_ACCESS_POLICY_REASONS:
        return None

    return MISSING_SUBSIDY_ACCESS_POLICY_REASONS[reason_slug]


class SubsidyAccessPolicyPagination(PaginationWithPageCount):
    """
    Set a smaller page size for SubsidyAccessPolicy list views.
    """
    max_page_size = 10


class SubsidyAccessPolicyViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Viewset supporting all CRUD operations on ``SubsidyAccessPolicy`` records.
    """
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = serializers.SubsidyAccessPolicyResponseSerializer
    authentication_classes = (JwtAuthentication, authentication.SessionAuthentication)
    filter_backends = (filters.NoFilterOnDetailBackend,)
    filterset_class = filters.SubsidyAccessPolicyFilter
    pagination_class = SubsidyAccessPolicyPagination
    lookup_field = 'uuid'

    def __init__(self, *args, **kwargs):
        self.extra_context = {}
        super().__init__(*args, **kwargs)

    def set_policy_created(self, created):
        """
        Helper function, used from within a related serializer for creation,
        to help understand in the context of this viewset whether
        a policy was created, or if a policy with the requested parameters
        already existed when creation was attempted.
        """
        self.extra_context['created'] = created

    def get_queryset(self):
        """
        A base queryset to list or retrieve `SubsidyAccessPolicy` records.
        """
        return SubsidyAccessPolicy.objects.all()

    def get_serializer_class(self):
        """
        Overrides the default behavior to return different
        serializers depending on the request action.
        """
        if self.action == 'create':
            return serializers.SubsidyAccessPolicyCRUDSerializer
        if self.action in ('update', 'partial_update'):
            return serializers.SubsidyAccessPolicyUpdateRequestSerializer
        return self.serializer_class

    @extend_schema(
        tags=[SUBSIDY_ACCESS_POLICY_CRUD_API_TAG],
        summary='Retrieve subsidy access policy by UUID.',
    )
    @permission_required(SUBSIDY_ACCESS_POLICY_READ_PERMISSION, fn=policy_permission_detail_fn)
    def retrieve(self, request, *args, uuid=None, **kwargs):
        """
        Retrieves a single `SubsidyAccessPolicy` record by uuid.
        """
        return super().retrieve(request, *args, uuid=uuid, **kwargs)

    @extend_schema(
        tags=[SUBSIDY_ACCESS_POLICY_CRUD_API_TAG],
        summary='List subsidy access policies for an enterprise customer.',
    )
    @permission_required(
        SUBSIDY_ACCESS_POLICY_READ_PERMISSION,
        fn=lambda request: request.query_params.get('enterprise_customer_uuid')
    )
    def list(self, request, *args, **kwargs):
        """
        Lists `SubsidyAccessPolicy` records, filtered by the
        given query parameters.
        """
        return super().list(request, *args, **kwargs)

    @extend_schema(
        tags=[SUBSIDY_ACCESS_POLICY_CRUD_API_TAG],
        summary='Create a new subsidy access policy.',
        request=serializers.SubsidyAccessPolicyCRUDSerializer,
        responses={
            status.HTTP_200_OK: serializers.SubsidyAccessPolicyResponseSerializer,
            status.HTTP_201_CREATED: serializers.SubsidyAccessPolicyResponseSerializer,
        },
    )
    @permission_required(
        SUBSIDY_ACCESS_POLICY_WRITE_PERMISSION,
        fn=lambda request: request.data.get('enterprise_customer_uuid')
    )
    def create(self, request, *args, **kwargs):
        """
        Creates a single `SubsidyAccessPolicy` record, or returns
        an existing one if an **active** record with the requested (enterprise_customer_uuid,
        subsidy_uuid, catalog_uuid, access_method) values already exists.
        """
        response = super().create(request, *args, **kwargs)
        if not self.extra_context.get('created'):
            response.status_code = status.HTTP_200_OK
        return response

    @extend_schema(
        tags=[SUBSIDY_ACCESS_POLICY_CRUD_API_TAG],
        summary='Partially update (with a PUT) a subsidy access policy by UUID.',
        request=serializers.SubsidyAccessPolicyUpdateRequestSerializer,
        responses={
            status.HTTP_200_OK: serializers.SubsidyAccessPolicyResponseSerializer,
            status.HTTP_404_NOT_FOUND: None,
        },
    )
    @permission_required(SUBSIDY_ACCESS_POLICY_WRITE_PERMISSION, fn=policy_permission_detail_fn)
    def update(self, request, *args, uuid=None, **kwargs):
        """
        Updates a single `SubsidyAccessPolicy` record by uuid.  All fields for the update are optional
        (which is different from a standard PUT request).
        """
        kwargs['partial'] = True
        return super().update(request, *args, uuid=uuid, **kwargs)

    @extend_schema(
        tags=[SUBSIDY_ACCESS_POLICY_CRUD_API_TAG],
        summary='Partially update (with a PATCH) a subsidy access policy by UUID.',
        request=serializers.SubsidyAccessPolicyUpdateRequestSerializer,
        responses={
            status.HTTP_200_OK: serializers.SubsidyAccessPolicyResponseSerializer,
            status.HTTP_404_NOT_FOUND: None,
        },
    )
    @permission_required(SUBSIDY_ACCESS_POLICY_WRITE_PERMISSION, fn=policy_permission_detail_fn)
    def partial_update(self, request, *args, uuid=None, **kwargs):
        """
        Updates a single `SubsidyAccessPolicy` record by uuid.  All fields for the update are optional.
        """
        return super().partial_update(request, *args, uuid=uuid, **kwargs)

    @extend_schema(
        tags=[SUBSIDY_ACCESS_POLICY_CRUD_API_TAG],
        summary='Soft-delete subsidy access policy by UUID.',
        request=serializers.SubsidyAccessPolicyDeleteRequestSerializer,
        responses={
            status.HTTP_200_OK: serializers.SubsidyAccessPolicyResponseSerializer,
            status.HTTP_404_NOT_FOUND: None,
        },
    )
    @permission_required(SUBSIDY_ACCESS_POLICY_WRITE_PERMISSION, fn=policy_permission_detail_fn)
    def destroy(self, request, *args, uuid=None, **kwargs):
        """
        Soft-delete a single `SubsidyAccessPolicy` record by uuid.
        """
        # Collect the "reason" query parameter from request body.
        request_serializer = serializers.SubsidyAccessPolicyDeleteRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        delete_reason = request_serializer.data.get('reason', None)

        try:
            policy_to_soft_delete = self.get_queryset().get(uuid=uuid)
        except SubsidyAccessPolicy.DoesNotExist:
            return Response(None, status=status.HTTP_404_NOT_FOUND)

        # Custom delete() method should flip the active flag if it isn't already active=False.
        policy_to_soft_delete.delete(reason=delete_reason)

        response_serializer = serializers.SubsidyAccessPolicyResponseSerializer(policy_to_soft_delete)
        return Response(response_serializer.data, status=status.HTTP_200_OK)


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


class AllocationRequestException(APIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = 'Could not allocate'


class SubsidyAccessPolicyRedeemViewset(UserDetailsFromJwtMixin, PermissionRequiredMixin, viewsets.GenericViewSet):
    """
    Viewset for Subsidy Access Policy APIs.
    """
    authentication_classes = [JwtAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    lookup_url_kwarg = 'policy_uuid'
    permission_required = SUBSIDY_ACCESS_POLICY_REDEMPTION_PERMISSION
    http_method_names = ['get', 'post']

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

        if self.action == 'can_request':
            enterprise_uuid = self.kwargs.get('enterprise_customer_uuid')

        return enterprise_uuid

    def get_permission_object(self):
        """
        Returns the enterprise uuid to verify that requesting user possess the enterprise learner or admin role.
        """
        return self.enterprise_customer_uuid

    def get_queryset(self):
        """
        Base queryset that returns all active & redeemable policies associated
        with the customer uuid requested by the client.
        """
        return SubsidyAccessPolicy.policies_with_redemption_enabled().filter(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
        ).order_by('-created')

    def evaluate_policies(self, enterprise_customer_uuid, lms_user_id, content_key, skip_customer_user_check=False):
        """
        Evaluate all policies for the given enterprise customer to check if it can be redeemed against the given learner
        and content.

        Note: Calling this will cause multiple backend API calls to the enterprise-subsidy can_redeem endpoint, one for
        each access policy evaluated.

        Returns:
            tuple of (list of SubsidyAccessPolicy, dict mapping str -> list of SubsidyAccessPolicy):
            The first tuple element is a list of redeemable policies, and the second tuple element is a mapping of
            reason strings to non-redeemable policies.  The reason strings are non-specific, short explanations for
            why each bucket of policies has been deemed non-redeemable. Both elements in the tuple are intentionally
            sorted based on prioritization of both redemption and non-redeemable policy reasons.

            THe sort logic is as such:
                - priority (of type)
                - expiration, sooner to expire first
                - balance, lower balance first
        """
        redeemable_policies = []
        non_redeemable_policies = defaultdict(list)
        # Sort policies by:
        # - priority (of type)
        # - expiration, sooner to expire first
        # - balance, lower balance first
        all_sorted_policies_for_enterprise = sort_subsidy_access_policies_for_redemption(
            queryset=self.get_queryset()
        )
        for policy in all_sorted_policies_for_enterprise:
            try:
                redeemable, reason, _ = policy.can_redeem(
                    lms_user_id, content_key, skip_customer_user_check=skip_customer_user_check
                )
                logger.info(
                    f'[can_redeem] {policy} inputs: (lms_user_id={lms_user_id}, content_key={content_key}) results: '
                    f'redeemable={redeemable}, reason={reason}.'
                )
            except ContentPriceNullException as exc:
                logger.warning(f'{exc} when checking can_redeem() for {enterprise_customer_uuid}')
                raise RedemptionRequestException(
                    detail=f'Could not determine price for content_key: {content_key}',
                ) from exc
            if redeemable:
                redeemable_policies.append(policy)
            else:
                # Aggregate the reasons for policies not being redeemable.  This really only works if the reason string
                # is short and generic because the bucketing logic simply treats entire string as the bucket key.
                non_redeemable_policies[reason].append(policy)

        return redeemable_policies, non_redeemable_policies

    def policies_with_credit_available(self, enterprise_customer_uuid, lms_user_id):
        """
        Return policies with credit available, associated with the given customer, and redeemable by the given learner.
        """
        policies = []
        all_policies_for_enterprise = self.get_queryset().filter(
            enterprise_customer_uuid=enterprise_customer_uuid
        )
        for policy in all_policies_for_enterprise:
            if policy.credit_available(lms_user_id):
                policies.append(policy)

        return policies

    @extend_schema(
        tags=[SUBSIDY_ACCESS_POLICY_REDEMPTION_API_TAG],
        summary='List credits available.',
        parameters=[serializers.SubsidyAccessPolicyCreditsAvailableRequestSerializer],
        responses=serializers.SubsidyAccessPolicyCreditsAvailableResponseSerializer(many=True),
    )
    @action(detail=False, methods=['get'])
    def credits_available(self, request):
        """
        Return a list of all redeemable policies for given `enterprise_customer_uuid`, `lms_user_id` that have
        redeemable credit available.

        Note that, for each redeemable policy that is *assignable*, the policy record
        in the response payload will also contain a list of `learner_content_assignments`
        associated with the requested `lms_user_id`.
        """
        serializer = serializers.SubsidyAccessPolicyCreditsAvailableRequestSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        enterprise_customer_uuid = serializer.data['enterprise_customer_uuid']
        lms_user_id = serializer.data['lms_user_id']

        policies_with_credit_available = self.policies_with_credit_available(enterprise_customer_uuid, lms_user_id)

        response_data = serializers.SubsidyAccessPolicyCreditsAvailableResponseSerializer(
            policies_with_credit_available,
            many=True,
            context={
                'lms_user_id': lms_user_id,
            },
        ).data

        return Response(
            response_data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=[SUBSIDY_ACCESS_POLICY_REDEMPTION_API_TAG],
        summary='Redeem with a policy.',
        request=serializers.SubsidyAccessPolicyRedeemRequestSerializer,
    )
    @action(detail=True, methods=['post'])
    def redeem(self, request, *args, **kwargs):
        """
        Redeem a policy for given `lms_user_id` and `content_key`

        status codes::

            400: There are missing or otherwise invalid input parameters.
            403: The requester has insufficient redeem permissions.
            422: The subsidy access policy is not redeemable in a way that IS NOT retryable.
            429: The subsidy access policy is not redeemable in a way that IS retryable (e.g. policy currently locked).
            200: The policy was successfully redeemed.  Response body is JSON with a serialized Transaction
                 containing the following keys (sample values):
                 {
                     "uuid": "the-transaction-uuid",
                     "state": "committed",
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

        serializer = serializers.SubsidyAccessPolicyRedeemRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        lms_user_id = serializer.data['lms_user_id']
        content_key = serializer.data['content_key']
        metadata = serializer.data.get('metadata')
        try:
            # For now, we should lock the whole policy (i.e. pass nothing to policy.lock()).  In some cases this is more
            # aggressive than necessary, but we can optimize for performance at a later phase of this project.  At that
            # point, we should also consider migrating this logic into the policy model so that different policy types
            # that have different locking needs can supply different lock kwargs.
            with policy.lock():
                can_redeem, reason, existing_transactions = policy.can_redeem(lms_user_id, content_key)
                if can_redeem:
                    redemption_result = policy.redeem(lms_user_id, content_key, existing_transactions, metadata)
                    send_subsidy_redemption_event_to_event_bus(
                        SUBSIDY_REDEEMED.event_type,
                        serializer.data
                    )
                    return Response(redemption_result, status=status.HTTP_200_OK)
                else:
                    non_redeemable_policies_reasons_list, _ = _get_reasons_for_no_redeemable_policies(
                        policy.enterprise_customer_uuid,
                        {reason: [policy]}
                    )
                    raise RedemptionRequestException(detail=non_redeemable_policies_reasons_list)
        except SubsidyAccessPolicyLockAttemptFailed as exc:
            logger.exception(exc)
            raise SubsidyAccessPolicyLockedException() from exc
        except SubsidyAPIHTTPError as exc:
            logger.exception(f'{exc} when creating transaction in subsidy API')
            error_payload = exc.error_payload()
            error_payload['detail'] = f"Subsidy Transaction API error: {error_payload['detail']}"
            raise RedemptionRequestException(
                detail=error_payload,
            ) from exc
        except MissingAssignment as exc:
            logger.exception(f'{exc} when redeeming assigned learner credit.')
            raise RedemptionRequestException(
                detail=f'Assignments race-condition: {exc}',
            ) from exc

    def get_existing_redemptions(self, policies, lms_user_id):
        """
        Returns a mapping of content keys to a mapping of policy uuids to lists of transactions
        for the given learner, filtered to only those transactions associated with **subsidies**
        to which any of the given **policies** are associated.
        """
        try:
            redemptions_map = get_redemptions_by_content_and_policy_for_learner(policies, lms_user_id)
        except SubsidyAPIHTTPError as exc:
            logger.exception(f'{exc} when fetching redemptions from subsidy API')
            error_payload = exc.error_payload()
            error_payload['detail'] = f"Subsidy Transaction API error: {error_payload['detail']}"
            raise RedemptionRequestException(
                detail=error_payload,
            ) from exc

        for content_key, transactions_by_policy in redemptions_map.items():
            for _, redemptions in transactions_by_policy.items():
                for redemption in redemptions:
                    redemption["policy_redemption_status_url"] = os.path.join(
                        EnterpriseSubsidyAPIClient.TRANSACTIONS_ENDPOINT,
                        f"{redemption['uuid']}/",
                    )
                    # TODO: this is currently hard-coded to only support OCM courses.
                    redemption["courseware_url"] = os.path.join(
                        settings.LMS_URL,
                        f"courses/{content_key}/courseware/",
                    )

        return redemptions_map

    def _get_list_price_for_catalog_course_metadata(self, course_metadata, content_key):
        """
        Get the list_price dict for course metadata fetched from catalog.

        Returns:
            dict conforming to a "list price" dict.

        Raises:
            ContentPriceNullException if the metadata is too malformed to find a price.
        """
        if (decimal_dollars := course_metadata['normalized_metadata_by_run'].get(
                content_key, {}).get('content_price')) is None:
            decimal_dollars = course_metadata['normalized_metadata'].get('content_price')
        if (decimal_dollars is None):
            raise ContentPriceNullException(
                f'Failed to obtain content price from enterprise-catalog for content_key {content_key}.'
            )
        return make_list_price_dict(decimal_dollars=decimal_dollars)

    @extend_schema(
        tags=[SUBSIDY_ACCESS_POLICY_REDEMPTION_API_TAG],
        summary='Can redeem.',
        parameters=[serializers.SubsidyAccessPolicyCanRedeemRequestSerializer],
        responses={
            status.HTTP_200_OK: serializers.SubsidyAccessPolicyCanRedeemElementResponseSerializer(many=True),
            # TODO: refine these other possible responses:
            # status.HTTP_403_FORBIDDEN: PermissionDenied,
            # status.HTTP_404_NOT_FOUND: NotFound,
        },
    )
    @action(
        detail=False,
        methods=['get'],
        url_name='can-redeem',
        url_path='enterprise-customer/(?P<enterprise_customer_uuid>[^/.]+)/can-redeem',
        pagination_class=None,
    )
    def can_redeem(self, request, enterprise_customer_uuid):
        """
        Within a specified enterprise customer, retrieves a single, redeemable access policy (or null)
        for each ``content_key`` in a provided list of content keys.

        Returns ``rest_framework.response.Response``:

                400: If there are missing or otherwise invalid input parameters.  Response body is JSON with a single
                     `Error` key.

                403: If the requester has insufficient permissions, Response body is JSON with a single `Error` key.

                200: If a redeemable access policy was found, an existing redemption was found, or neither.  Response
                     body is a JSON list of dict containing redemption evaluations for each given content_key.  See
                     redoc for a sample response.
        """
        serializer = serializers.SubsidyAccessPolicyCanRedeemRequestSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        content_keys = serializer.data['content_key']
        lms_user_id_override = serializer.data.get('lms_user_id') if request.user.is_staff else None
        lms_user_id = lms_user_id_override or self.lms_user_id or request.user.lms_user_id
        if not lms_user_id:
            logger.warning(
                f'No lms_user_id found when checking if we can redeem {content_keys} '
                f'in customer {enterprise_customer_uuid}'
            )
            raise NotFound(detail='Could not determine a value for lms_user_id')

        policies_for_customer = self.get_queryset()
        if not policies_for_customer:
            raise NotFound(detail='No active policies for this customer')

        redemptions_by_content_and_policy = self.get_existing_redemptions(
            policies_for_customer,
            lms_user_id
        )

        element_responses = []
        for content_key in content_keys:
            reasons = []
            display_reason = None
            redeemable_policies = []
            non_redeemable_policies = []
            resolved_policy = None
            list_price_dict = None

            redemptions_by_policy = redemptions_by_content_and_policy[content_key]
            policy_by_redemption_uuid = {
                redemption['uuid']: policy
                for policy, redemptions in redemptions_by_policy.items()
                for redemption in redemptions
            }
            # Flatten dict of lists because the response doesn't need to be bucketed by policy_uuid.
            redemptions = [
                redemption
                for redemptions in redemptions_by_policy.values()
                for redemption in redemptions
            ]

            # Determine if the learner has already redeemed the requested content_key.  Just because a transaction has
            # state='committed' doesn't mean it counts as a successful redemption; it must also NOT have a committed
            # reversal.
            successful_redemptions = [
                redemption for redemption in redemptions
                if redemption['state'] == TransactionStateChoices.COMMITTED and (
                    not redemption['reversal'] or
                    redemption['reversal'].get('state') != TransactionStateChoices.COMMITTED
                )
            ]

            # Of all policies for this customer, determine which are redeemable and which are not.
            # But, only do this if there are no existing successful redemptions,
            # so we don't unnecessarily call `can_redeem()` on every policy.
            if not successful_redemptions:
                redeemable_policies, non_redeemable_policies = self.evaluate_policies(
                    enterprise_customer_uuid,
                    lms_user_id, content_key,
                    # don't skip the customer user check if we're using an override lms_user_id
                    skip_customer_user_check=not bool(lms_user_id_override),
                )

            if not successful_redemptions and not redeemable_policies:
                non_redeemable_policies_reason_list, display_reason = _get_reasons_for_no_redeemable_policies(
                    enterprise_customer_uuid,
                    non_redeemable_policies
                )
                reasons.extend(non_redeemable_policies_reason_list)

            if redeemable_policies:
                resolved_policy = redeemable_policies[0]

            try:
                if resolved_policy:
                    list_price_dict = resolved_policy.get_list_price(lms_user_id, content_key)
                elif successful_redemptions:
                    # Get the policy used for redemption and use that to compute the price. If the redemption was the
                    # result of assignment, the historical assignment price might differ from the canonical price. We
                    # prefer to display the redeemed price to avoid confusion.
                    successfully_redeemed_policy = policy_by_redemption_uuid[successful_redemptions[0]['uuid']]
                    list_price_dict = successfully_redeemed_policy.get_list_price(lms_user_id, content_key)
                else:
                    # In the case where the learner cannot redeem and has never redeemed this content, bypass the
                    # subsidy metadata endpoint and go straight to the source (enterprise-catalog) to find normalized
                    # price data. In this case, the list price returned rarely actually drives the display price in the
                    # learner portal frontend, but we still need to maintain a non-null list price for a few reasons:
                    # * Enterprise customers that leverage the API directly always expect a non-null price.
                    # * On rare occasion, this price actually does drive the price display in learner-portal.  We think
                    #   this can happen when courses are searchable and there is an assignment-based policy, but nothing
                    #   has been assigned.
                    # * Long-term, we will use can_redeem for all subsidy types, at which point we will also rely on
                    #   this list_price for price display 100% of the time.
                    try:
                        course_metadata = get_and_cache_content_metadata(content_key, coerce_to_parent_course=True)
                    except HTTPError:
                        # We might normally re-raise the exception here (and have in the past), but this would cause
                        # can-redeem requests to fail for courses which contain restricted runs where the customer does
                        # not have restricted access.  The desired behavior is for the request to NOT fail, and the
                        # frontend is already coded to discard the restricted runs.
                        logger.warning(
                            (
                                'Failed to obtain content metadata from enterprise-catalog with enterprise customer '
                                '%s. This can happen if the run is restricted.'
                            ),
                            enterprise_customer_uuid
                        )
                    else:
                        list_price_dict = self._get_list_price_for_catalog_course_metadata(course_metadata, content_key)
            except ContentPriceNullException as exc:
                raise RedemptionRequestException(
                    detail=(
                        f'Could not determine list price for content_key {content_key}'
                        f' with enterprise customer {enterprise_customer_uuid}')
                ) from exc

            element_response = {
                "content_key": content_key,
                "list_price": list_price_dict,
                "redemptions": redemptions,
                "has_successful_redemption": bool(successful_redemptions),
                "redeemable_subsidy_access_policy": resolved_policy,
                "can_redeem": bool(resolved_policy),
                "reasons": reasons,
                "display_reason": display_reason,
            }
            element_responses.append(element_response)

        response_serializer = serializers.SubsidyAccessPolicyCanRedeemElementResponseSerializer(
            element_responses,
            # many=True, when combined with pagination_class=None, will cause the serialized output representation to be
            # a top-level array. This deviates from the industry norm of nesting results lists into the value of a
            # top-level "results" key. The current implementation is already integrated with a frontend used in
            # production, so the easiest thing to do is to NOT change it to match the industry norm more closely.
            many=True,
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=[SUBSIDY_ACCESS_POLICY_REDEMPTION_API_TAG],
        summary='Can request.',
        parameters=[serializers.SubsidyAccessPolicyCanRequestRequestSerializer],
        responses={
            status.HTTP_200_OK: serializers.SubsidyAccessPolicyCanRequestElementResponseSerializer(many=True),
            # TODO: refine these other possible responses:
            # status.HTTP_403_FORBIDDEN: PermissionDenied,
            # status.HTTP_404_NOT_FOUND: NotFound,
        },
    )
    @action(
        detail=False,
        methods=['get'],
        url_name='can-request',
        url_path='enterprise-customer/(?P<enterprise_customer_uuid>[^/.]+)/can-request',
        pagination_class=None,
    )
    def can_request(self, request, enterprise_customer_uuid):
        """
        Check if a learner can request access to content. The flow is:
        1. Find BnR enabled policies first
        2. Check if content key exists in those policies
        3. Check for existing pending request by this learner
        """
        serializer = serializers.SubsidyAccessPolicyCanRequestRequestSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        content_key = serializer.data['content_key']
        lms_user_id_override = serializer.data.get('lms_user_id') if request.user.is_staff else None
        lms_user_id = lms_user_id_override or self.lms_user_id or request.user.lms_user_id
        if not lms_user_id:
            raise NotFound(detail='Could not determine a value for lms_user_id')

        # Get all active policies for this customer
        policies_for_customer = self.get_queryset()
        if not policies_for_customer:
            return Response({
                'can_request': False,
                'reason': 'No active policies for this customer'
            }, status=status.HTTP_200_OK)

        # 1. Find policies with BnR enabled
        bnr_enabled_policies = policies_for_customer.filter(
            learner_credit_request_config__isnull=False,
            learner_credit_request_config__active=True
        )
        if not bnr_enabled_policies:
            return Response({
                'can_request': False,
                'reason': 'No policies with BnR enabled found'
            }, status=status.HTTP_200_OK)

        # 2. Check if content exists in catalogs for BnR enabled policies
        # Filter policies to those that contain the content key in their catalog
        valid_policies = bnr_enabled_policies.filter(
            pk__in=[
                policy.pk for policy in bnr_enabled_policies
                if policy.catalog_contains_content_key(content_key)
            ]
        )

        if not valid_policies.exists():
            return Response({
                'can_request': False,
                'reason': REASON_CONTENT_NOT_IN_CATALOG
            }, status=status.HTTP_200_OK)

        # 3. Check for existing pending request by this learner
        existing_request = LearnerCreditRequest.objects.filter(
            user__lms_user_id=lms_user_id,
            enterprise_customer_uuid=enterprise_customer_uuid,
            course_id=content_key,
            state__in=LC_NON_RE_REQUESTABLE_STATES
        ).first()

        if existing_request:
            return Response({
                'can_request': False,
                'reason': f"You already have an active request for this course in state: {existing_request.state}",
                'existing_request': str(existing_request.uuid)
            }, status=status.HTTP_200_OK)

        # Sort policies to find the best one for redemption
        requestable_policy = sort_subsidy_access_policies_for_redemption(valid_policies)[0]
        response_data = {
            'content_key': content_key,
            'can_request': True,
            'requestable_subsidy_access_policy': requestable_policy
        }
        response_serializer = serializers.SubsidyAccessPolicyCanRequestElementResponseSerializer(response_data)
        return Response(response_serializer.data, status=status.HTTP_200_OK)


class SubsidyAccessPolicyAllocateViewset(UserDetailsFromJwtMixin, PermissionRequiredMixin, viewsets.GenericViewSet):
    """
    Viewset for Subsidy Access Policy Allocation actions.
    """
    authentication_classes = [JwtAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    lookup_url_kwarg = 'policy_uuid'
    permission_required = SUBSIDY_ACCESS_POLICY_ALLOCATION_PERMISSION
    http_method_names = ['post']

    @cached_property
    def enterprise_customer_uuid(self):
        """Returns the enterprise customer uuid from query params or request data based on action type. """
        enterprise_uuid = ''

        if self.action == 'allocate':
            policy_uuid = self.kwargs.get('policy_uuid')
            with suppress(ValidationError):  # Ignore if `policy_uuid` is not a valid uuid
                policy = SubsidyAccessPolicy.objects.filter(uuid=policy_uuid).first()
                if policy:
                    enterprise_uuid = policy.enterprise_customer_uuid

        return enterprise_uuid

    def get_permission_object(self):
        """
        Returns the enterprise uuid to verify that requesting user possess the enterprise learner or admin role.
        """
        return str(self.enterprise_customer_uuid)

    def get_queryset(self):
        """
        Default base queryset for this viewset.
        """
        return SubsidyAccessPolicy.objects.none()

    @extend_schema(
        tags=[SUBSIDY_ACCESS_POLICY_ALLOCATION_API_TAG],
        summary='Allocate assignments',
        parameters=[serializers.SubsidyAccessPolicyAllocateRequestSerializer],
        responses={
            status.HTTP_202_ACCEPTED: serializers.SubsidyAccessPolicyAllocationResponseSerializer,
        },
    )
    @action(
        detail=True,
        methods=['post'],
    )
    def allocate(self, request, *args, **kwargs):
        """
        Idempotently creates or updates allocated ``LearnerContentAssignment``
        records for a requested list of user email addresses, in the requested
        ``content_key`` and at the requested price of ``content_price_cents``.
        These assignments are related to the ``AssignmentConfiguration`` of the
        requested ``AssignedLearnerCreditAccessPolicy`` record.
        """
        policy = get_object_or_404(SubsidyAccessPolicy, pk=kwargs.get('policy_uuid'))

        serializer = serializers.SubsidyAccessPolicyAllocateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        learner_emails = serializer.data['learner_emails']
        content_key = serializer.data['content_key']
        content_price_cents = serializer.data['content_price_cents']

        try:
            with policy.lock():
                can_allocate, reason = policy.can_allocate(
                    len(learner_emails),
                    content_key,
                    content_price_cents,
                )
                if can_allocate:
                    allocation_result = policy.allocate(
                        learner_emails,
                        content_key,
                        content_price_cents,
                    )
                    response_serializer = serializers.SubsidyAccessPolicyAllocationResponseSerializer(
                        allocation_result,
                    )
                    return Response(response_serializer.data, status=status.HTTP_202_ACCEPTED)
                else:
                    non_allocatable_reason_list, _ = _get_reasons_for_no_redeemable_policies(
                        policy.enterprise_customer_uuid,
                        {reason: [policy]}
                    )
                    raise AllocationRequestException(detail=non_allocatable_reason_list)

        except SubsidyAccessPolicyLockAttemptFailed as exc:
            logger.exception(exc)
            raise SubsidyAccessPolicyLockedException() from exc
        except (AllocationException, ValidationError) as exc:
            logger.exception(exc)
            error_message = str(exc)
            error_detail = [
                {
                    "reason": exc.__class__.__name__,
                    "user_message": getattr(exc, 'user_message', error_message),
                    "error_message": error_message,
                    "policy_uuids": [policy.uuid],
                }
            ]
            raise AllocationRequestException(detail=error_detail) from exc


class GroupMembersWithAggregatesCsvRenderer(CSVRenderer):
    """
    Custom Renderer class to ensure csv column ordering and labelling.
    """
    header = [
        'member_details.user_email',
        'member_details.user_name',
        'recent_action',
        'enrollment_count',
        'activated_at',
        'status'
    ]
    labels = {
        'member_details.user_email': 'email',
        'member_details.user_name': 'name',
        'recent_action': 'Recent Action',
        'enrollment_count': 'Enrollment Number',
        'activated_at': 'Activation Date',
    }


class SubsidyAccessPolicyGroupViewset(UserDetailsFromJwtMixin, PermissionRequiredMixin, viewsets.GenericViewSet):
    """
    Viewset for Subsidy Access Policy Group Associations.
    """
    permission_classes = (permissions.IsAuthenticated,)
    authentication_classes = (JwtAuthentication, authentication.SessionAuthentication)
    filter_backends = (filters.NoFilterOnDetailBackend,)
    lookup_field = 'uuid'

    def get_permission_required(self):
        """
        Return specific permission name based on the view being requested
        """
        if self.action == 'delete_policy_group_association':
            return [SUBSIDY_ACCESS_POLICY_WRITE_PERMISSION]
        return [SUBSIDY_ACCESS_POLICY_READ_PERMISSION]

    @cached_property
    def enterprise_customer_uuid(self):
        """
        Returns the enterprise customer uuid from request data based.
        """
        enterprise_uuid = ''
        if self.action == 'delete_policy_group_association':
            enterprise_uuid = self.kwargs.get('enterprise_uuid')
        else:
            policy_uuid = self.kwargs.get('uuid')
            with suppress(ValidationError):  # Ignore if `policy_uuid` is not a valid uuid
                policy = SubsidyAccessPolicy.objects.filter(uuid=policy_uuid).first()
                if policy:
                    enterprise_uuid = policy.enterprise_customer_uuid
        return enterprise_uuid

    def get_permission_object(self):
        """
        Returns the enterprise uuid to verify that requesting user possess the enterprise learner or admin role.
        """
        return str(self.enterprise_customer_uuid)

    def get_queryset(self):
        """
        Required by Django Generic Viewsets, since this data is fetched remotely and constructed there is no interal
        notion of a queryset
        """

    @extend_schema(
        tags=[GROUP_MEMBER_DATA_WITH_AGGREGATES_API_TAG],
        summary='List group member data with aggregates.',
        parameters=[serializers.SubsidyAccessPolicyAllocateRequestSerializer],
        responses={
            status.HTTP_200_OK: serializers.GroupMemberWithAggregatesResponseSerializer,
            status.HTTP_404_NOT_FOUND: None,
        },
    )
    @action(detail=False, methods=['get'])
    def get_group_member_data_with_aggregates(self, request, uuid):
        """
        Retrieves Enterprise Group Members data zipped with subsidy aggregate enrollment data from a group record
        linked to a subsidy access policy.

        Params:
            group_uuid: (Optional) The Enterprise Group uuid from which to select members. Leave blank to fetch the
                first group found in the PolicyGroupAssociation table associated with the supplied SubsidyAccessPolicy.
            user_query: (Optional) Query sub-string to search/filter group members by email.
            sort_by: (Optional) Choice- sort results by either: 'member_details', 'status', or 'recent_action'.
            show_removed: (Optional) Whether or not to return deleted membership records.
            is_reversed: (Optional) Reverse the order in which records are returned.
            format_csv: (Optional) Whether or not to return data in a csv format, defaults to `False`
            page: (Optional) Which page of Enterprise Group Membership records to request. Leave blank to fetch all
                group membership records
            learners: (Optional) Array of learner emails. If specified, the endpoint will only return membership
                records associated with one of the provided emails.
        """
        request_serializer = serializers.GroupMemberWithAggregatesRequestSerializer(data=request.query_params)
        request_serializer.is_valid(raise_exception=True)
        group_uuid = request_serializer.validated_data.get('group_uuid')
        page = request_serializer.validated_data.get('page')
        traverse_pagination = request_serializer.validated_data.get('traverse_pagination')
        sort_by = request_serializer.validated_data.get('sort_by')
        is_reversed = request_serializer.validated_data.get('is_reversed')
        learners = request_serializer.validated_data.get('learners')

        try:
            policy = SubsidyAccessPolicy.objects.get(uuid=uuid)
        except SubsidyAccessPolicy.DoesNotExist:
            return Response("Policy not found", status.HTTP_404_NOT_FOUND)

        # IMPLICITLY ASSUME there is exactly one group associated with the policy
        # as of 04/2024
        if not group_uuid:
            policy_group_association = PolicyGroupAssociation.objects.filter(subsidy_access_policy=policy.uuid).first()
            if not policy_group_association:
                return Response("Policy group not found associated with subsidy", status.HTTP_404_NOT_FOUND)
            group_uuid = policy_group_association.enterprise_group_uuid

        # Request learner aggregate data from the subsidy service for this particular subsidy/policy
        subsidy_learner_aggregate_dict = get_and_cache_subsidy_learners_aggregate_data(
            policy.subsidy_uuid,
            policy.uuid
        )

        # If `sort_by_enrollment_count` is true, then we need to fetch all the group members records and do the sorting
        # ourselves since platform is unaware of enrollment data numbers.
        page_requested_by_client = None
        if sort_by_enrollment_count := (sort_by == SORT_BY_ENROLLMENT_COUNT):
            sort_by = None
        else:
            page_requested_by_client = page

        # Request the group member data from platform
        member_response = LmsApiClient().fetch_group_members(
            group_uuid=group_uuid,
            sort_by=sort_by,
            user_query=request_serializer.validated_data.get('user_query'),
            show_removed=request_serializer.validated_data.get('show_removed'),
            is_reversed=is_reversed,
            traverse_pagination=(traverse_pagination or sort_by_enrollment_count),
            page=page_requested_by_client,
            learners=learners,
        )
        member_results = member_response.get('results')

        # Sift through the group members data, zipping the aggregate data from the subsidy service into
        # each member record, assume enrollment count is 0 if subsidy enrollment aggregate data does not exist.
        member_results = zip_group_members_data_with_enrollment_count(member_results, subsidy_learner_aggregate_dict)

        # If the request sorts by enrollment count, sort member data and grab values to properly construct the
        # `next` and `previous` pages
        page_index_start = 0
        num_member_results = 0
        if sort_by_enrollment_count:
            member_results.sort(key=lambda result: result.get('enrollment_count'), reverse=(not is_reversed))
            if page:
                # Needed to construct `next` and `previous` values for the response
                num_member_results = len(member_results)

                # Cut down the returned "all data" to the page and size requested
                page_index_start = (page - 1) * GROUP_MEMBERS_WITH_AGGREGATES_DEFAULT_PAGE_SIZE
                member_results = member_results[
                    page_index_start: page_index_start + GROUP_MEMBERS_WITH_AGGREGATES_DEFAULT_PAGE_SIZE
                ]

        member_response['results'] = member_results

        # return in a csv format if indicated by query params
        if request_serializer.validated_data.get('format_csv', False):
            request.accepted_renderer = GroupMembersWithAggregatesCsvRenderer()
            request.accepted_media_type = GroupMembersWithAggregatesCsvRenderer().media_type
            return Response(list(member_results), status=status.HTTP_200_OK, content_type='text/csv')

        # Since we are essentially forwarding all request params to platform, we only need to replace the `next` and
        # `previous` url values from the response returned by platform to construct a valid response object for the
        # requester.
        _update_pagination_params_for_group_aggregates(
            member_response,
            traverse_pagination,
            sort_by_enrollment_count,
            page_index_start,
            request,
            num_member_results,
        )
        return Response(data=member_response, status=200)

    @extend_schema(
        tags=[DELETE_POLICY_GROUP_ASSOCIATION_API_TAG],
        summary='Delete a PolicyGroupAssociation record.',
        responses={
            status.HTTP_204_NO_CONTENT: None,
            status.HTTP_404_NOT_FOUND: None,
        },
    )
    @action(detail=False, methods=['delete'])
    def delete_policy_group_association(
        self, request, enterprise_uuid, group_uuid, *args, **kwargs  # pylint: disable=unused-argument
    ):
        """
        Delete all `PolicyGroupAssociation` records associated with the group uuid.
        Params:
            enterprise_uuid: (required) The enterprise customer associated with the EnterpriseGroup
            group_uuid: (required) The uuid associated with the EnterpriseGroup in edx-enterprise
        """
        try:
            policy_group_associations = PolicyGroupAssociation.objects.filter(enterprise_group_uuid=group_uuid)
            policy_group_associations.delete()
        except PolicyGroupAssociation.DoesNotExist:
            return Response(None, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)
