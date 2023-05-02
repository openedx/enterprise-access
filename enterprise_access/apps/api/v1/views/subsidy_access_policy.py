"""
REST API views for the subsidy_access_policy app.
"""
import logging
import os
from collections import defaultdict
from contextlib import suppress

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from edx_enterprise_subsidy_client import EnterpriseSubsidyAPIClient
from edx_rbac.mixins import PermissionRequiredMixin
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import filters, permissions
from rest_framework import serializers as rest_serializers
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from enterprise_access.apps.api import serializers
from enterprise_access.apps.api.mixins import UserDetailsFromJwtMixin
from enterprise_access.apps.events.signals import ACCESS_POLICY_CREATED, ACCESS_POLICY_UPDATED, SUBSIDY_REDEEMED
from enterprise_access.apps.events.utils import (
    send_access_policy_event_to_event_bus,
    send_subsidy_redemption_event_to_event_bus
)
from enterprise_access.apps.subsidy_access_policy.constants import (
    POLICY_TYPES_WITH_CREDIT_LIMIT,
    TransactionStateChoices
)
from enterprise_access.apps.subsidy_access_policy.models import (
    SubsidyAccessPolicy,
    SubsidyAccessPolicyLockAttemptFailed
)

from .utils import PaginationWithPageCount

logger = logging.getLogger(__name__)


@extend_schema_view(
    retrieve=extend_schema(
        tags=['Subsidy Access Policy CRUD'],
        summary='Retrieve subsidy access policy',
        description='Retrieves a single subsidy access policy record, given its UUID.',
    ),
    destroy=extend_schema(
        tags=['Subsidy Access Policy CRUD'],
        summary='Delete subsidy access policy',
        description='De-activates the requested subsidy access policy record.'
    ),
)
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

    @extend_schema(
        tags=['Subsidy Access Policy CRUD'],
        summary='Create subsidy access policy.',
    )
    def create(self, request, *args, **kwargs):
        """
        Creates a new ``SubsidyAccessPolicy`` record.
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
        tags=['Subsidy Access Policy CRUD'],
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
        tags=['Subsidy Access Policy CRUD'],
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
    permission_required = 'requests.has_learner_or_admin_access'
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
        queryset = SubsidyAccessPolicy.objects.order_by('-created')
        enterprise_customer_uuid = self.enterprise_customer_uuid
        return queryset.filter(enterprise_customer_uuid=enterprise_customer_uuid)

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
        all_policies_for_enterprise = SubsidyAccessPolicy.objects.filter(
            enterprise_customer_uuid=enterprise_customer_uuid,
            active=True,
        )
        for policy in all_policies_for_enterprise:
            redeemable, reason = policy.can_redeem(lms_user_id, content_key)
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

        URL Location: POST /api/v1/policy/<policy_uuid>/redeem/

        JSON body parameters:
        {
            "lms_user_id":
            "content_key":
        }

        status codes:
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
                if policy.can_redeem(lms_user_id, content_key):
                    redemption_result = policy.redeem(lms_user_id, content_key, metadata)
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

    def get_redemptions_by_policy_uuid(self, enterprise_customer_uuid, lms_user_id, content_key):
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
                            "lms_user_id": 54321,
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
            if redemptions := policy.redemptions(lms_user_id, content_key):
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

    @extend_schema(
        tags=['Subsidy Access Policy Redemption'],
        summary='Retrieve redemption.',
        parameters=[serializers.SubsidyAccessPolicyRedemptionRequestSerializer],
    )
    @action(detail=False, methods=['get'])
    def redemption(self, request, *args, **kwargs):
        """
        Return redemption records for given `enterprise_customer_uuid`, `lms_user_id` and `content_key`

        URL Location: GET /api/v1/policy/redemption/?enterprise_customer_uuid=<>&lms_user_id=<>&content_key=<>
        """
        serializer = serializers.SubsidyAccessPolicyRedemptionRequestSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        enterprise_customer_uuid = serializer.data['enterprise_customer_uuid']
        lms_user_id = serializer.data['lms_user_id']
        content_key = serializer.data['content_key']

        return Response(
            self.get_redemptions_by_policy_uuid(enterprise_customer_uuid, lms_user_id, content_key),
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=['Subsidy Access Policy Redemption'],
        summary='Can redeem.',
        parameters=[serializers.SubsidyAccessPolicyCanRedeemRequestSerializer],
    )
    @action(
        detail=False,
        methods=['get'],
        url_name='can-redeem',
        url_path='enterprise-customer/(?P<enterprise_customer_uuid>[^/.]+)/can-redeem',
    )
    def can_redeem(self, request, enterprise_customer_uuid):
        """
        Within a specified enterprise customer, retrieves a single, redeemable access policy (or null)
        for each ``content_key`` in a provided list of content keys.

        Returns ``rest_framework.response.Response``:

                400: If there are missing or otherwise invalid input parameters.  Response body is JSON with a single
                     `Error` key.

                403: If the requester has insufficient permissions, Response body is JSON with a single `Error` key.

                201: If a redeemable access policy was found, an existing redemption was found, or neither.  Response
                     body is a JSON list of dict containing redemption evaluations for each given content_key.  See
                     below for a sample response to 3 passed content_keys: one which has existing redemptions, one
                     without, and a third that is not redeemable.

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
        serializer = serializers.SubsidyAccessPolicyCanRedeemRequestSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        content_keys = serializer.data['content_key']
        lms_user_id = self.lms_user_id

        response = []
        for content_key in content_keys:
            serialized_policy = None
            reasons = []

            redemptions_by_policy_uuid = self.get_redemptions_by_policy_uuid(
                enterprise_customer_uuid,
                lms_user_id,
                content_key
            )
            # Flatten dict of lists because the response doesn't need to be bucketed by policy_uuid.
            redemptions = [
                redemption
                for redemptions in redemptions_by_policy_uuid.values()
                for redemption in redemptions
            ]
            redeemable_policies, non_redeemable_policies = self.evaluate_policies(
                enterprise_customer_uuid, lms_user_id, content_key
            )
            if not redemptions and not redeemable_policies:
                for reason, policies in non_redeemable_policies.items():
                    reasons.append({
                        "reason": reason,
                        "policy_uuids": [policy.uuid for policy in policies],
                    })
            if redeemable_policies:
                resolved_policy = SubsidyAccessPolicy.resolve_policy(redeemable_policies)
                serialized_policy = serializers.SubsidyAccessPolicyRedeemableResponseSerializer(resolved_policy).data

            has_successful_redemption = any(
                redemption['state'] == TransactionStateChoices.COMMITTED
                for redemption in redemptions
            )
            can_redeem_for_content_response = {
                "content_key": content_key,
                "redemptions": redemptions,
                "has_successful_redemption": has_successful_redemption,
                "redeemable_subsidy_access_policy": serialized_policy,
                "can_redeem": bool(serialized_policy),
                "reasons": reasons,
            }
            response.append(can_redeem_for_content_response)

        return Response(response, status=status.HTTP_200_OK)
