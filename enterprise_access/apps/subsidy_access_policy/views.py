"""
Views for subsidy_access_policy.
"""
from django.http.response import Http404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication

from subsidy_access_policy.models import LearnerCreditAccessPolicy, SubsidyAccessPolicy
from subsidy_access_policy.serializers import PolicyRedeemRequestSerializer
from subsidy_access_policy.exceptions import NoRedeemablePolicyFound


class AccessPolicyRedeemAPIView(APIView):
    """
    API to redeem a policy.
    """
    authentication_classes = [JwtAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, format=None):
        """
        """
        # Get all redeemable polices
            # For each policy
                # Check group membership of learner
                # Check content in catalog
                # Check redeemability of content in subsidy
        # Use policy resolver to pick first redeemable policy
        # Call Subsidy API to redeem subsidy for the redeemable policy

        serializer = PolicyRedeemRequestSerializer(data=request.post)
        serializer.is_valid(raise_exception=True)

        group_uuid = serializer.data['group_uuid']
        learner_id = serializer.data['learner_id']
        content_key = serializer.data['content_key']

        try:
            policy_to_redeem = self.redeemable_policy(group_uuid, learner_id, content_key)
        except NoRedeemablePolicyFound as no_redeemable_policy_found:
            raise Http404 from no_redeemable_policy_found

        response = policy_to_redeem.redeem(learner_id, content_key)
        return Response(response)

    def redeemable_policy(self, group_uuid, learner_id, content_key):
        redeemable_learner_credit_policies = []
        learner_credit_policies = LearnerCreditAccessPolicy.get_policies(group_uuid)
        for learner_credit_policy in learner_credit_policies:
            if learner_credit_policy.can_redeem(learner_id, content_key):
                redeemable_learner_credit_policies.append(learner_credit_policy)

        if not redeemable_learner_credit_policies:
            raise NoRedeemablePolicyFound(
                'No Redeemable Policy found. GroupId: [%s], LearnerId: [%s], ContentKey: [%s]' %
                (group_uuid, learner_id, content_key)
            )

        policy_to_redeem = SubsidyAccessPolicy.resolve_policy(redeemable_learner_credit_policies)

        return policy_to_redeem
