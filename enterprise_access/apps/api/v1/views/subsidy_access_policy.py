"""
REST API views for the subsidy_access_policy app.
"""
import logging
import os
import time
from collections import defaultdict
from contextlib import suppress

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.functional import cached_property
from drf_spectacular.utils import extend_schema
from edx_enterprise_subsidy_client import EnterpriseSubsidyAPIClient
from edx_rbac.decorators import permission_required
from edx_rbac.mixins import PermissionRequiredMixin
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import authentication, mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from enterprise_access.apps.api import filters, serializers, utils
from enterprise_access.apps.api.mixins import UserDetailsFromJwtMixin
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.content_assignments.api import AllocationException
from enterprise_access.apps.core.constants import (
    SUBSIDY_ACCESS_POLICY_ALLOCATION_PERMISSION,
    SUBSIDY_ACCESS_POLICY_READ_PERMISSION,
    SUBSIDY_ACCESS_POLICY_REDEMPTION_PERMISSION,
    SUBSIDY_ACCESS_POLICY_WRITE_PERMISSION
)
from enterprise_access.apps.events.signals import SUBSIDY_REDEEMED
from enterprise_access.apps.events.utils import send_subsidy_redemption_event_to_event_bus
from enterprise_access.apps.subsidy_access_policy.constants import (
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
    MissingSubsidyAccessReasonUserMessages,
    TransactionStateChoices
)
from enterprise_access.apps.subsidy_access_policy.exceptions import (
    ContentPriceNullException,
    MissingAssignment,
    SubsidyAPIHTTPError
)
from enterprise_access.apps.subsidy_access_policy.models import (
    SubsidyAccessPolicy,
    SubsidyAccessPolicyLockAttemptFailed
)
from enterprise_access.apps.subsidy_access_policy.subsidy_api import get_redemptions_by_content_and_policy_for_learner

from .utils import PaginationWithPageCount

logger = logging.getLogger(__name__)

SUBSIDY_ACCESS_POLICY_CRUD_API_TAG = 'Subsidy Access Policies CRUD'
SUBSIDY_ACCESS_POLICY_REDEMPTION_API_TAG = 'Subsidy Access Policy Redemption'
SUBSIDY_ACCESS_POLICY_ALLOCATION_API_TAG = 'Subsidy Access Policy Allocation'


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
    lms_client = LmsApiClient()
    enterprise_customer_data = lms_client.get_enterprise_customer_data(enterprise_customer_uuid)
    enterprise_admin_users = enterprise_customer_data.get('admin_users')

    for reason, policies in non_redeemable_policies_by_reason.items():
        reasons.append({
            "reason": reason,
            "user_message": _get_user_message_for_reason(reason, enterprise_admin_users),
            "metadata": {
                "enterprise_administrators": enterprise_admin_users,
            },
            "policy_uuids": [policy.uuid for policy in policies],
        })

    return reasons


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

    def evaluate_policies(self, enterprise_customer_uuid, lms_user_id, content_key):
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
        all_policies_for_enterprise = self.get_queryset()
        for policy in all_policies_for_enterprise:
            try:
                redeemable, reason, _ = policy.can_redeem(lms_user_id, content_key, skip_customer_user_check=True)
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

        return (redeemable_policies, non_redeemable_policies)

    def policies_with_credit_available(self, enterprise_customer_uuid, lms_user_id):
        """
        Return policies with credit availble, associated with the given customer, and redeemable by the given learner.
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
                    raise RedemptionRequestException(
                        detail=_get_reasons_for_no_redeemable_policies(
                            policy.enterprise_customer_uuid,
                            {reason: [policy]}
                        )
                    )
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
        lms_user_id = self.lms_user_id or request.user.lms_user_id
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
            redeemable_policies = []
            non_redeemable_policies = []
            resolved_policy = None
            list_price = None

            redemptions_by_policy_uuid = redemptions_by_content_and_policy[content_key]
            # Flatten dict of lists because the response doesn't need to be bucketed by policy_uuid.
            redemptions = [
                redemption
                for redemptions in redemptions_by_policy_uuid.values()
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
                    enterprise_customer_uuid, lms_user_id, content_key
                )

            if not redemptions and not redeemable_policies:
                reasons.extend(_get_reasons_for_no_redeemable_policies(
                    enterprise_customer_uuid,
                    non_redeemable_policies
                ))

            if redeemable_policies:
                resolved_policy = SubsidyAccessPolicy.resolve_policy(redeemable_policies)

            try:
                if resolved_policy:
                    list_price = resolved_policy.get_list_price(lms_user_id, content_key)
                elif successful_redemptions:
                    # Get the policy record used at time of successful redemption.
                    # [2023-12-05] TODO: consider cleaning this up.
                    # This is kind of silly, b/c we only need this policy to compute the
                    # list price, and it's really only *necessary* to fetch that price
                    # from within the context of a *policy record* for cases where that successful
                    # policy was assignment-based (because the list price for assignments might
                    # slightly different from the current list price in the canonical content metadata).
                    successfully_redeemed_policy = self.get_queryset().filter(
                        uuid=successful_redemptions[0]['subsidy_access_policy_uuid'],
                    ).first()
                    list_price = successfully_redeemed_policy.get_list_price(lms_user_id, content_key)
            except ContentPriceNullException as exc:
                raise RedemptionRequestException(
                    detail=f'Could not determine list price for content_key: {content_key}',
                ) from exc

            element_response = {
                "content_key": content_key,
                "list_price": list_price,
                "redemptions": redemptions,
                "has_successful_redemption": bool(successful_redemptions),
                "redeemable_subsidy_access_policy": resolved_policy,
                "can_redeem": bool(resolved_policy),
                "reasons": reasons,
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
            # TODO: remove the timing calls after slowness is identified
            start_time = time.process_time()
            with policy.lock():
                can_allocate, reason = policy.can_allocate(
                    len(learner_emails),
                    content_key,
                    content_price_cents,
                )
                can_allocate_time = time.process_time() - start_time
                logger.info('allocate timing: can_allocate() %s', can_allocate_time)
                if can_allocate:
                    allocation_result = policy.allocate(
                        learner_emails,
                        content_key,
                        content_price_cents,
                    )
                    do_allocate_time = time.process_time() - can_allocate_time
                    logger.info('allocate timing: do allocate() %s', do_allocate_time)
                    response_serializer = serializers.SubsidyAccessPolicyAllocationResponseSerializer(
                        allocation_result,
                    )
                    serialization_time = time.process_time() - do_allocate_time
                    logger.info('allocate timing: serialization %s', serialization_time)
                    return Response(response_serializer.data, status=status.HTTP_202_ACCEPTED)
                else:
                    non_allocatable_reason_list = _get_reasons_for_no_redeemable_policies(
                        policy.enterprise_customer_uuid,
                        {reason: [policy]}
                    )
                    raise AllocationRequestException(detail=non_allocatable_reason_list)

            # we may not have hit the `if` block, so just get a time on the
            # entire policy lock context.  We can infer the difference between
            # that value and `serialization_time` if the latter is available in logs.
            lock_release_time = time.process_time() - start_time
            logger.info('allocate timing: policy lock release %s', lock_release_time)
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
