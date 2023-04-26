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


PER_LEARNER_ENROLL_CREDIT = 'PerLearnerEnrollmentCreditAccessPolicy'
PER_LEARNER_SPEND_CREDIT = 'PerLearnerSpendCreditAccessPolicy'

CREDIT_POLICY_TYPE_PRIORITY = 1
SUBSCRIPTION_POLICY_TYPE_PRIORITY = 2


class PolicyTypes:
    """Subsidy Access Policy Types. """

    CHOICES = (
        (PER_LEARNER_ENROLL_CREDIT, PER_LEARNER_ENROLL_CREDIT),
        (PER_LEARNER_SPEND_CREDIT, PER_LEARNER_SPEND_CREDIT),
    )


POLICY_TYPES_WITH_CREDIT_LIMIT = [
    PER_LEARNER_ENROLL_CREDIT,
    PER_LEARNER_SPEND_CREDIT,
]

POLICY_TYPE_CREDIT_LIMIT_FIELDS = [
    'per_learner_enrollment_limit',
    'per_learner_spend_limit',
]

POLICY_TYPE_FIELD_MAPPER = dict(zip(POLICY_TYPES_WITH_CREDIT_LIMIT, POLICY_TYPE_CREDIT_LIMIT_FIELDS))


class TransactionStateChoices:
    """
    Lifecycle states for a ledger transaction (i.e., redemption).

    CREATED
        Indicates that the transaction has only just been created, and is the default state.

    PENDING
        Indicates that an attempt is being made to redeem the content in the target LMS.

    COMMITTED
        Indicates that the content has been redeemed, and a reference to the redemption result (often an enrollment ID)
        is stored in the reference_id field of the transaction.

    FAILED
        Indidcates that the attempt to redeem the content in the target LMS encountered an error.
    """

    CREATED = 'created'
    PENDING = 'pending'
    COMMITTED = 'committed'
    FAILED = 'failed'
