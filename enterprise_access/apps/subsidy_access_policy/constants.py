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


PER_LEARNER_ENROL_CREDIT = 'PerLearnerEnrollmentCreditAccessPolicy'
PER_LEARNER_SPEND_CREDIT = 'PerLearnerSpendCreditAccessPolicy'

CREDIT_POLICY_TYPE_PRIORITY = 1
SUBSCRIPTION_POLICY_TYPE_PRIORITY = 2


class PolicyTypes:
    """Subsidy Access Policy Types. """

    CHOICES = (
        (PER_LEARNER_ENROL_CREDIT, PER_LEARNER_ENROL_CREDIT),
        (PER_LEARNER_SPEND_CREDIT, PER_LEARNER_SPEND_CREDIT),
    )


POLICY_TYPES_WITH_CREDIT_LIMIT = [
    PER_LEARNER_ENROL_CREDIT,
    PER_LEARNER_SPEND_CREDIT,
]

POLICY_TYPE_CREDIT_LIMIT_FIELDS = [
    'per_learner_enrollment_limit',
    'per_learner_spend_limit',
]

POLICY_TYPE_FIELD_MAPPER = dict(zip(POLICY_TYPES_WITH_CREDIT_LIMIT, POLICY_TYPE_CREDIT_LIMIT_FIELDS))
