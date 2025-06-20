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


# Configure the priority of each policy type here.  When given multiple redeemable policies to select for redemption,
# the policy resolution engine will select policies with the lowest priority number.
ASSIGNED_CREDIT_POLICY_TYPE_PRIORITY = 0
CREDIT_POLICY_TYPE_PRIORITY = 1
SUBSCRIPTION_POLICY_TYPE_PRIORITY = 2


class PolicyTypes:
    """
    Subsidy Access Policy Types.

    This must be manually maintained to be in sync with all sub-classes of the SubsidyAccessPolicy model.
    """

    PER_LEARNER_ENROLLMENT_CREDIT = 'PerLearnerEnrollmentCreditAccessPolicy'
    PER_LEARNER_SPEND_CREDIT = 'PerLearnerSpendCreditAccessPolicy'
    ASSIGNED_LEARNER_CREDIT = 'AssignedLearnerCreditAccessPolicy'

    CHOICES = (
        (PER_LEARNER_ENROLLMENT_CREDIT, PER_LEARNER_ENROLLMENT_CREDIT),
        (PER_LEARNER_SPEND_CREDIT, PER_LEARNER_SPEND_CREDIT),
        (ASSIGNED_LEARNER_CREDIT, ASSIGNED_LEARNER_CREDIT),
    )


CENTS_PER_DOLLAR = 100.0


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
        Indicates that the attempt to redeem the content in the target LMS encountered an error.
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
    ORGANIZATION_EXPIRED_FUNDS = "You can't enroll right now because your funds expired."
    ORGANIZATION_EXPIRED_FUNDS_NO_ADMINS = "You can't enroll right now because your funds expired. " \
                                           "Contact your administrator for help."
    LEARNER_LIMITS_REACHED = "You can't enroll right now because of limits set by your organization."
    CONTENT_NOT_IN_CATALOG = \
        "You can't enroll right now because this course is no longer available in your organization's catalog."
    BEYOND_ENROLLMENT_DEADLINE = \
        "You can't enroll right now because the enrollment deadline for this course has passed."
    LEARNER_NOT_IN_ENTERPRISE = \
        "You can't enroll right now because your account is no longer associated with the organization."
    LEARNER_NOT_ASSIGNED_CONTENT = \
        "You can't enroll right now because this course is not assigned to you."
    LEARNER_ASSIGNMENT_CANCELED = \
        "You can't enroll right now right now because your administrator canceled your course assignment."


REASON_POLICY_EXPIRED = "policy_expired"
REASON_SUBSIDY_EXPIRED = "subsidy_expired"
REASON_CONTENT_NOT_IN_CATALOG = "content_not_in_catalog"
REASON_BEYOND_ENROLLMENT_DEADLINE = "beyond_enrollment_deadline"
REASON_LEARNER_NOT_IN_ENTERPRISE = "learner_not_in_enterprise"
REASON_LEARNER_NOT_IN_ENTERPRISE_GROUP = "learner_not_in_enterprise_group"
REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY = "not_enough_value_in_subsidy"
REASON_LEARNER_MAX_SPEND_REACHED = "learner_max_spend_reached"
REASON_POLICY_SPEND_LIMIT_REACHED = "policy_spend_limit_reached"
REASON_LEARNER_MAX_ENROLLMENTS_REACHED = "learner_max_enrollments_reached"
REASON_LEARNER_NOT_ASSIGNED_CONTENT = "reason_learner_not_assigned_content"
REASON_LEARNER_ASSIGNMENT_CANCELLED = "reason_learner_assignment_cancelled"
REASON_LEARNER_ASSIGNMENT_FAILED = "reason_learner_assignment_failed"
REASON_LEARNER_ASSIGNMENT_EXPIRED = "reason_learner_assignment_expired"
REASON_LEARNER_ASSIGNMENT_REVERSED = "reason_learner_assignment_reversed"

# Redeem metadata keyword that
# forces enrollment to take place, regardless of course state.
FORCE_ENROLLMENT_KEYWORD = 'allow_late_enrollment'

SORT_BY_ENROLLMENT_COUNT = 'enrollment_count'

GROUP_MEMBERS_WITH_AGGREGATES_DEFAULT_PAGE_SIZE = 10

# Exceeding the spend_limit validation error
VALIDATION_ERROR_SPEND_LIMIT_EXCEEDS_STARTING_BALANCE = "You cannot make this change, as the sum of all budget \
spend_limits for a given subsidy would exceed the sum of all deposits into that subsidy.  If you are trying to \
re-balance policies, FIRST reduce the spend_limit of one, THEN increase the spend_limit of another."
