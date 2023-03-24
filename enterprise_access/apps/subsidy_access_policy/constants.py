""" Constants for the subsidy_access_policy app. """


class AccessMethods:
    """
    Possible states of a subsidy request.
    """

    DIRECT = 'direct'
    REQUEST = 'request'
    ASSIGNED = 'assigned'

    CHOICES = (
        (DIRECT, "Direct"),
        (REQUEST, "Request"),
        (ASSIGNED, "Assigned"),
    )

# Segment events


class SegmentEvents:
    """
    Events sent to segment.
    """

    SUBSIDY_ACCESS_POLICY_CREATED = 'edx.server.enterprise-access.enrollment-lifecycle.subsidy-access-policy.created'
    SUBSIDY_ACCESS_POLICY_REDEEMED = 'edx.server.enterprise-access.enrollment-lifecycle.subsidy-access-policy.redeemed'


SUBSCRIPTION_ACCESS = 'SubscriptionAccessPolicy'
PER_LEARNER_ENROL_CREDIT = 'PerLearnerEnrollmentCreditAccessPolicy'
PER_LEARNER_SPEND_CREDIT = 'PerLearnerSpendCreditAccessPolicy'
CAP_ENROLL_LEARNER_CREDIT = 'CappedEnrollmentLearnerCreditAccessPolicy'

CREDIT_POLICY_TYPE_PRIORITY = 1
SUBSCRIPTION_POLICY_TYPE_PRIORITY = 2


class PolicyTypes:
    """Subsidy Access Policy Types. """

    CHOICES = (
        (SUBSCRIPTION_ACCESS, SUBSCRIPTION_ACCESS),
        (PER_LEARNER_ENROL_CREDIT, PER_LEARNER_ENROL_CREDIT),
        (PER_LEARNER_SPEND_CREDIT, PER_LEARNER_SPEND_CREDIT),
        (CAP_ENROLL_LEARNER_CREDIT, CAP_ENROLL_LEARNER_CREDIT),
    )


POLICY_TYPES_WITH_CREDIT_LIMIT = [
    PER_LEARNER_ENROL_CREDIT,
    PER_LEARNER_SPEND_CREDIT,
    CAP_ENROLL_LEARNER_CREDIT
]

NON_CREDIT_LIMIT_POLICY_TYPES = [SUBSCRIPTION_ACCESS]

POLICY_TYPE_CREDIT_LIMIT_FIELDS = [
    'per_learner_enrollment_limit',
    'per_learner_spend_limit',
    'spend_limit',
]

POLICY_TYPE_FIELD_MAPPER = dict(zip(POLICY_TYPES_WITH_CREDIT_LIMIT, POLICY_TYPE_CREDIT_LIMIT_FIELDS))
