"""
REST API views for the subsidy_access_policy app.
"""
import logging
import os
from collections import defaultdict
from contextlib import suppress

import requests
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from edx_enterprise_subsidy_client import EnterpriseSubsidyAPIClient
from edx_rbac.decorators import permission_required
from edx_rbac.mixins import PermissionRequiredMixin
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import authentication
from rest_framework import filters as rest_filters
from rest_framework import permissions
from rest_framework import serializers as rest_serializers
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from enterprise_access.apps.api import filters, serializers, utils
from enterprise_access.apps.api.mixins import UserDetailsFromJwtMixin
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.core.constants import (
    POLICY_READ_PERMISSION,
    POLICY_REDEMPTION_PERMISSION,
    REQUESTS_ADMIN_LEARNER_ACCESS_PERMISSION
)
from enterprise_access.apps.events.signals import ACCESS_POLICY_CREATED, ACCESS_POLICY_UPDATED, SUBSIDY_REDEEMED
from enterprise_access.apps.events.utils import (
    send_access_policy_event_to_event_bus,
    send_subsidy_redemption_event_to_event_bus
)
from enterprise_access.apps.subsidy_access_policy.constants import (
    POLICY_TYPES_WITH_CREDIT_LIMIT,
    REASON_CONTENT_NOT_IN_CATALOG,
    REASON_LEARNER_MAX_ENROLLMENTS_REACHED,
    REASON_LEARNER_MAX_SPEND_REACHED,
    REASON_LEARNER_NOT_IN_ENTERPRISE,
    REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY,
    REASON_POLICY_NOT_ACTIVE,
    REASON_POLICY_SPEND_LIMIT_REACHED,
    MissingSubsidyAccessReasonUserMessages,
    TransactionStateChoices
)
from enterprise_access.apps.subsidy_access_policy.content_metadata_api import get_and_cache_content_metadata
from enterprise_access.apps.subsidy_access_policy.exceptions import ContentPriceNullException, SubsidyAPIHTTPError
from enterprise_access.apps.subsidy_access_policy.models import (
    SubsidyAccessPolicy,
    SubsidyAccessPolicyLockAttemptFailed
)
from enterprise_access.apps.subsidy_access_policy.subsidy_api import get_redemptions_by_content_and_policy_for_learner

from .utils import PaginationWithPageCount

logger = logging.getLogger(__name__)


SUBSIDY_ACCESS_POLICY_CRUD_API_TAG = 'DEPRECATED: Subsidy Access Policy views'
SUBSIDY_ACCESS_POLICY_READ_ONLY_API_TAG = 'subsidy-access-policies read-only'


def policy_permission_retrieve_fn(request, *args, uuid=None):
    """
    Helper to use with @permission_required when retrieving a policy record.
    """
    return utils.get_policy_customer_uuid(uuid)


class SubsidyAccessPolicyReadOnlyViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only viewset for listing or retrieving ``SubsidyAccessPolicy`` records.
    """
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = serializers.SubsidyAccessPolicyResponseSerializer
    authentication_classes = (JwtAuthentication, authentication.SessionAuthentication)
    filter_backends = (filters.NoFilterOnRetrieveBackend,)
    filterset_class = filters.SubsidyAccessPolicyFilter
    pagination_class = PaginationWithPageCount
    lookup_field = 'uuid'

    def get_queryset(self):
        """
        A base queryset to list or retrieve `SubsidyAccessPolicy` records.
        """
        return SubsidyAccessPolicy.objects.all()

    @extend_schema(
        tags=[SUBSIDY_ACCESS_POLICY_READ_ONLY_API_TAG],
        summary='Retrieve subsidy access policy by UUID.',
    )
    @permission_required(POLICY_READ_PERMISSION, fn=policy_permission_retrieve_fn)
    def retrieve(self, request, *args, uuid=None, **kwargs):
        """
        Retrieves a single `SubsidyAccessPolicy` record by uuid.
        """
        return super().retrieve(request, *args, uuid=uuid, **kwargs)

    @extend_schema(
        tags=[SUBSIDY_ACCESS_POLICY_READ_ONLY_API_TAG],
        summary='List subsidy access policies for an enterprise customer.',
    )
    @permission_required(
        POLICY_READ_PERMISSION,
        fn=lambda request: request.query_params.get('enterprise_customer_uuid')
    )
    def list(self, request, *args, **kwargs):
        """
        Lists `SubsidyAccessPolicy` records, filtered by the
        given query parameters.
        """
        return super().list(request, *args, **kwargs)


@extend_schema_view(
    retrieve=extend_schema(
        tags=[SUBSIDY_ACCESS_POLICY_CRUD_API_TAG],
        summary='Retrieve subsidy access policy.',
    ),
    delete=extend_schema(
        tags=[SUBSIDY_ACCESS_POLICY_CRUD_API_TAG],
        summary='Delete subsidy access policy.',
    ),
)
class SubsidyAccessPolicyCRUDViewset(PermissionRequiredMixin, viewsets.ModelViewSet):
    """
     DEPRECATED: Viewset for Subsidy Access Policy CRUD operations.
     """

    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = serializers.SubsidyAccessPolicyCRUDSerializer
    authentication_classes = (JwtAuthentication,)
    filter_backends = (rest_filters.OrderingFilter, DjangoFilterBackend,)
    filterset_fields = ('enterprise_customer_uuid', 'policy_type',)
    pagination_class = PaginationWithPageCount
    http_method_names = ['get', 'post', 'patch', 'delete']
    lookup_field = 'uuid'
    permission_required = REQUESTS_ADMIN_LEARNER_ACCESS_PERMISSION

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

    @extend_schema(
        tags=[SUBSIDY_ACCESS_POLICY_CRUD_API_TAG],
        summary='Create subsidy access policy.',
    )
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

    @extend_schema(
        tags=[SUBSIDY_ACCESS_POLICY_CRUD_API_TAG],
        summary='Update subsidy access policy.',
    )
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

    @extend_schema(
        tags=[SUBSIDY_ACCESS_POLICY_CRUD_API_TAG],
        summary='List subsidy access policy.',
    )
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
    permission_required = POLICY_REDEMPTION_PERMISSION
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
        Base queryset that returns all active policies associated
        with the customer uuid requested by the client.
        """
        return SubsidyAccessPolicy.objects.filter(
            enterprise_customer_uuid=self.enterprise_customer_uuid,
            active=True,
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
        Return all redeemable policies in terms of "credit available".
        """
        policies = []
        all_policies = SubsidyAccessPolicy.objects.filter(
            policy_type__in=POLICY_TYPES_WITH_CREDIT_LIMIT,
            enterprise_customer_uuid=enterprise_customer_uuid
        )
        for policy in all_policies:
            if policy.credit_available(lms_user_id):
                policies.append(policy)

        return policies

    @extend_schema(
        tags=['Subsidy Access Policy Redemption'],
        summary='List credits available.',
        parameters=[serializers.SubsidyAccessPolicyCreditsAvailableRequestSerializer],
        responses=serializers.SubsidyAccessPolicyCreditsAvailableResponseSerializer(many=True),
    )
    @action(detail=False, methods=['get'])
    def credits_available(self, request):
        """
        Return a list of all redeemable policies for given `enterprise_customer_uuid`, `lms_user_id` that have
        redeemable credit available.
        """
        serializer = serializers.SubsidyAccessPolicyCreditsAvailableRequestSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        enterprise_customer_uuid = serializer.data['enterprise_customer_uuid']
        lms_user_id = serializer.data['lms_user_id']

        policies_with_credit_available = self.policies_with_credit_available(enterprise_customer_uuid, lms_user_id)
        response_data = serializers.SubsidyAccessPolicyCreditsAvailableResponseSerializer(
            policies_with_credit_available,
            many=True,
            context={'lms_user_id': lms_user_id}
        ).data

        return Response(
            response_data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=['Subsidy Access Policy Redemption'],
        summary='List redeemable policies.',
        parameters=[serializers.SubsidyAccessPolicyListRequestSerializer],
        responses=serializers.SubsidyAccessPolicyRedeemableResponseSerializer(many=True),
    )
    def list(self, request):
        """
        Return a list of all redeemable policies for given `enterprise_customer_uuid`, `lms_user_id` and `content_key`
        """
        serializer = serializers.SubsidyAccessPolicyListRequestSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        enterprise_customer_uuid = serializer.data['enterprise_customer_uuid']
        lms_user_id = serializer.data['lms_user_id']
        content_key = serializer.data['content_key']

        redeemable_policies, _ = self.evaluate_policies(enterprise_customer_uuid, lms_user_id, content_key)
        response_data = serializers.SubsidyAccessPolicyRedeemableResponseSerializer(redeemable_policies, many=True).data

        return Response(
            response_data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=['Subsidy Access Policy Redemption'],
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
            422: The subisdy access policy is not redeemable in a way that IS NOT retryable.
            429: The subisdy access policy is not redeemable in a way that IS retryable (e.g. policy currently locked).
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
                        detail=self._get_reasons_for_no_redeemable_policies(
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

    def get_existing_redemptions(self, policies, lms_user_id):
        """
        Returns a mapping of content keys to a mapping of policy uuids to lists of transactions
        for the given learner, filtered to only those transactions associated with **subsidies**
        to which any of the given **policies** are associated.
        """
        redemptions_map = get_redemptions_by_content_and_policy_for_learner(policies, lms_user_id)
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

    def _get_user_message_for_reason(self, reason_slug, enterprise_admin_users):
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

        MISSING_SUBSIDY_ACCESS_POLICY_REASONS = {
            REASON_POLICY_NOT_ACTIVE: user_message_organization_no_funds,
            REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY: user_message_organization_no_funds,
            REASON_POLICY_SPEND_LIMIT_REACHED: user_message_organization_no_funds,
            REASON_LEARNER_NOT_IN_ENTERPRISE: MissingSubsidyAccessReasonUserMessages.LEARNER_NOT_IN_ENTERPRISE,
            REASON_LEARNER_MAX_SPEND_REACHED: MissingSubsidyAccessReasonUserMessages.LEARNER_LIMITS_REACHED,
            REASON_LEARNER_MAX_ENROLLMENTS_REACHED: MissingSubsidyAccessReasonUserMessages.LEARNER_LIMITS_REACHED,
            REASON_CONTENT_NOT_IN_CATALOG: MissingSubsidyAccessReasonUserMessages.CONTENT_NOT_IN_CATALOG,
        }

        if reason_slug not in MISSING_SUBSIDY_ACCESS_POLICY_REASONS:
            return None

        return MISSING_SUBSIDY_ACCESS_POLICY_REASONS[reason_slug]

    @extend_schema(
        tags=['Subsidy Access Policy Redemption'],
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

            redemptions_by_policy_uuid = redemptions_by_content_and_policy[content_key]
            # Flatten dict of lists because the response doesn't need to be bucketed by policy_uuid.
            redemptions = [
                redemption
                for redemptions in redemptions_by_policy_uuid.values()
                for redemption in redemptions
            ]

            # Determine if the learner has already redeemed the requested content_key.
            has_successful_redemption = any(
                redemption['state'] == TransactionStateChoices.COMMITTED
                for redemption in redemptions
            )

            # Of all policies for this customer, determine which are redeemable and which are not.
            # But, only do this if there are no existing successful redemptions,
            # so we don't unnecessarily call `can_redeem()` on every policy.
            if not has_successful_redemption:
                redeemable_policies, non_redeemable_policies = self.evaluate_policies(
                    enterprise_customer_uuid, lms_user_id, content_key
                )

            if not redemptions and not redeemable_policies:
                reasons.extend(self._get_reasons_for_no_redeemable_policies(
                    enterprise_customer_uuid,
                    non_redeemable_policies
                ))

            # TODO: Arbitrarily select one redeemable policy for now.
            if redeemable_policies:
                resolved_policy = redeemable_policies[0]

            element_response = {
                "content_key": content_key,
                "list_price": self._get_list_price(enterprise_customer_uuid, content_key),
                "redemptions": redemptions,
                "has_successful_redemption": has_successful_redemption,
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

    def _get_reasons_for_no_redeemable_policies(self, enterprise_customer_uuid, non_redeemable_policies):
        """
        Serialize a reason for non-redeemability, and fetch enterprise admin user that learner can contact,
        for each non-redeemable policy.
        """
        reasons = []
        lms_client = LmsApiClient()
        enterprise_customer_data = lms_client.get_enterprise_customer_data(enterprise_customer_uuid)
        enterprise_admin_users = enterprise_customer_data.get('admin_users')

        for reason, policies in non_redeemable_policies.items():
            reasons.append({
                "reason": reason,
                "user_message": self._get_user_message_for_reason(reason, enterprise_admin_users),
                "metadata": {
                    "enterprise_administrators": enterprise_admin_users,
                },
                "policy_uuids": [policy.uuid for policy in policies],
            })

        return reasons

    def _get_list_price(self, enterprise_customer_uuid, content_key):
        """
        Determine the price for content for display purposes only.
        """
        try:
            content_metadata = get_and_cache_content_metadata(enterprise_customer_uuid, content_key)
            # Note that the "content_price" key is guaranteed to exist, but the value may be None.
            list_price_integer_cents = content_metadata["content_price"]
            # TODO: simplify this function by consolidating this conversion logic into the response serializer:
            if list_price_integer_cents is not None:
                list_price_decimal_dollars = float(list_price_integer_cents) / 100
            else:
                list_price_decimal_dollars = None
        except requests.exceptions.HTTPError as exc:
            logger.warning(f'{exc} when checking content metadata for {enterprise_customer_uuid} and {content_key}')
            raise RedemptionRequestException(
                detail=f'Could not determine price for content_key: {content_key}',
            ) from exc

        return {
            "usd": list_price_decimal_dollars,
            "usd_cents": list_price_integer_cents,
        }
