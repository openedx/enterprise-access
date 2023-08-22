""" Constants for the subsidy_access_policy app. """
import re


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
CREDIT_POLICY_TYPE_PRIORITY = 1
SUBSCRIPTION_POLICY_TYPE_PRIORITY = 2


class PolicyTypes:
    """
    Subsidy Access Policy Types.

    This must be manually maintained to be in sync with all sub-classes of the SubsidyAccessPolicy model.
    """

    PER_LEARNER_ENROLLMENT_CREDIT = 'PerLearnerEnrollmentCreditAccessPolicy'
    PER_LEARNER_SPEND_CREDIT = 'PerLearnerSpendCreditAccessPolicy'

    CHOICES = (
        (PER_LEARNER_ENROLLMENT_CREDIT, PER_LEARNER_ENROLLMENT_CREDIT),
        (PER_LEARNER_SPEND_CREDIT, PER_LEARNER_SPEND_CREDIT),
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
    ORGANIZATION_EXPIRED_FUNDS = "You can't enroll right now because your funds expired."
    ORGANIZATION_EXPIRED_FUNDS_NO_ADMINS = "You can't enroll right now because your funds expired. " \
                                           "Contact your administrator for help."
    LEARNER_LIMITS_REACHED = "You can't enroll right now because of limits set by your organization."
    CONTENT_NOT_IN_CATALOG = \
        "You can't enroll right now because this course is no longer available in your organization's catalog."
    LEARNER_NOT_IN_ENTERPRISE = \
        "You can't enroll right now because your account is no longer associated with the organization."


REASON_POLICY_EXPIRED = "policy_expired"
REASON_SUBSIDY_EXPIRED = "subsidy_expired"
REASON_CONTENT_NOT_IN_CATALOG = "content_not_in_catalog"
REASON_LEARNER_NOT_IN_ENTERPRISE = "learner_not_in_enterprise"
REASON_NOT_ENOUGH_VALUE_IN_SUBSIDY = "not_enough_value_in_subsidy"
REASON_LEARNER_MAX_SPEND_REACHED = "learner_max_spend_reached"
REASON_POLICY_SPEND_LIMIT_REACHED = "policy_spend_limit_reached"
REASON_LEARNER_MAX_ENROLLMENTS_REACHED = "learner_max_enrollments_reached"


class SubsidyRedemptionErrorCodes:
    """
    Collection of error ``code`` values that the subsidy API's
    redeem endpoint might return in an error response payload.
    """
    DEFAULT_ERROR = 'subsidy_redemption_error'
    FULFILLMENT_ERROR = 'fulfillment_error'


class SubsidyRedemptionErrorReasons:
    """
    Somewhat more generic collection of reasons that redemption may have
    failed in ways that are *not* related to fulfillment.
    """
    DEFAULT_REASON = 'default_subsidy_redemption_error'

    USER_MESSAGES_BY_REASON = {
        DEFAULT_REASON: "Something went wrong during subsidy redemption",
    }


class SubsidyFulfillmentErrorReasons:
    """
    Codifies standard reasons that fulfillment may have failed,
    along with a mapping of those reasons to user-friendly display messages.
    """
    DEFAULT_REASON = 'default_fulfillment_error'
    DUPLICATE_FULFILLMENT = 'duplicate_fulfillment'

    USER_MESSAGES_BY_REASON = {
        DEFAULT_REASON: "Something went wrong during fulfillment",
        DUPLICATE_FULFILLMENT: "A legacy fulfillment already exists for this content.",
    }

    CAUSES_REGEXP_BY_REASON = {
        DUPLICATE_FULFILLMENT: re.compile(".*duplicate order.*"),
    }

    @classmethod
    def get_cause_from_error_message(cls, message_string):
        """
        Helper to find the cause of a given error message string
        by matching against the regexs mapped above.
        """
        if not message_string:
            return None

        for cause_of_message, regex in cls.CAUSES_REGEXP_BY_REASON.items():
            if regex.match(message_string):
                return cause_of_message

        return None
