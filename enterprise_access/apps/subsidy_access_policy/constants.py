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


class MissingSubsidyAccessReasonUserMessages:
    """
    User-friendly display messages explaining why the learner does not have subsidized access.
    """
    ORGANIZATION_NO_FUNDS = "You can't enroll right now because your organization doesn't have enough funds."
    ORGANIZATION_NO_FUNDS_NO_ADMINS = \
        "You can't enroll right now because your organization doesn't have enough funds. " \
        "Contact your administrator to request more."
    LEARNER_LIMITS_REACHED = "You can't enroll right now because of limits set by your organization."


REASON_POLICY_NOT_ACTIVE = "policy_not_active"
REASON_CONTENT_NOT_IN_CATALOG = "content_not_in_catalog"
REASON_LEARNER_NOT_IN_ENTERPRISE = "learner_not_in_enterprise"
REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY = "not_enough_value_in_subsidy"
REASON_LEARNER_MAX_SPEND_REACHED = "learner_max_spend_reached"
REASON_LEARNER_MAX_ENROLLMENTS_REACHED = "learner_max_enrollments_reached"
