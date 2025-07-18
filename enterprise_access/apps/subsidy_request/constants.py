""" Constants for the subsidy_request app. """


class SubsidyRequestStates:
    """ Possible states of a subsidy request. """

    # Common states
    REQUESTED = 'requested'
    PENDING = 'pending'
    APPROVED = 'approved'
    DECLINED = 'declined'
    ERROR = 'error'

    COMMON_STATES = (
        (REQUESTED, "Requested"),
        (PENDING, "Pending"),
        (APPROVED, "Approved"),
        (DECLINED, "Declined"),
        (ERROR, "Error"),
    )

    # Learner credit request related states
    ACCEPTED = 'accepted'
    CANCELLED = 'cancelled'
    EXPIRED = 'expired'
    REVERSED = 'reversed'

    LC_REQUEST_STATES = (
        (ACCEPTED, "Accepted"),
        (CANCELLED, "Cancelled"),
        (EXPIRED, "Expired"),
        (REVERSED, "Reversed"),
    )

    CHOICES = COMMON_STATES + LC_REQUEST_STATES


class LearnerCreditAdditionalActionStates:
    """ Additional states specifically for LearnerCreditRequestActions. """

    REMINDED = 'reminded'
    CHOICES = (
        (REMINDED, "Reminded"),
    )


# Combined choices for LearnerCreditRequestAction model
LearnerCreditRequestActionChoices = SubsidyRequestStates.CHOICES + LearnerCreditAdditionalActionStates.CHOICES


# List of states where a learner cannot make a new request for a course
# if they already have a request in one of these states.
LC_NON_RE_REQUESTABLE_STATES = [
    SubsidyRequestStates.REQUESTED,
    SubsidyRequestStates.APPROVED,
    SubsidyRequestStates.ACCEPTED,
    SubsidyRequestStates.ERROR,
]


class SubsidyTypeChoices:
    """ Type of subsidies. """
    LICENSE = 'license'
    COUPON = 'coupon'  # aka A la cart
    LEARNER_CREDIT = 'learner_credit'

    CHOICES = (
        (LICENSE, 'License Subsidy'),
        (COUPON, 'Coupon Subsidy'),
        (LEARNER_CREDIT, 'Learner Credit Subsidy'),
    )


SUBSIDY_TYPE_CHANGE_DECLINATION = (
    'Declined because subsidy type on SubsidyRequestCustomerConfiguration '
    'has changed.'
)

SUBSIDY_REQUEST_BULK_OPERATION_BATCH_SIZE = 100


class LearnerCreditRequestUserMessages:
    """
    User-facing messages for LearnerCreditRequestActions status field.
    Reusing the state keys from SubsidyRequestStates but with different display messages.
    """
    CHOICES = (
        (SubsidyRequestStates.REQUESTED, "Requested"),
        (LearnerCreditAdditionalActionStates.REMINDED, "Waiting For Learner"),
        (SubsidyRequestStates.APPROVED, "Waiting For Learner"),
        (SubsidyRequestStates.ACCEPTED, "Redeemed By Learner"),
        (SubsidyRequestStates.DECLINED, "Declined"),
        (SubsidyRequestStates.REVERSED, "Refunded"),
        (SubsidyRequestStates.CANCELLED, "Cancelled"),
        (SubsidyRequestStates.EXPIRED, "Expired"),
    )


class LearnerCreditRequestActionErrorReasons:
    """
    Error reasons for LearnerCreditRequestActions error_reason field.
    """
    FAILED_APPROVAL = 'failed_approval'
    FAILED_DECLINE = 'failed_decline'
    FAILED_CANCELLATION = 'failed_cancellation'
    FAILED_REDEMPTION = 'failed_redemption'
    FAILED_REVERSAL = 'failed_reversal'
    EMAIL_ERROR = 'email_error'

    CHOICES = (
        (FAILED_APPROVAL, "Failed: Approval"),
        (FAILED_DECLINE, "Failed: Decline"),
        (FAILED_CANCELLATION, "Failed: Cancellation"),
        (FAILED_REDEMPTION, "Failed: Redemption"),
        (FAILED_REVERSAL, "Failed: Reversal"),
        (EMAIL_ERROR, 'Email error'),
    )


# Segment events


class SegmentEvents:
    """
    Events sent to segment.
    """

    LICENSE_REQUEST_CREATED = 'edx.server.enterprise-access.license-request-lifecycle.created'
    LICENSE_REQUEST_APPROVED = 'edx.server.enterprise-access.license-request-lifecycle.approved'
    LICENSE_REQUEST_DECLINED = 'edx.server.enterprise-access.license-request-lifecycle.declined'
    COUPON_CODE_REQUEST_CREATED = 'edx.server.enterprise-access.coupon-code-request-lifecycle.created'
    COUPON_CODE_REQUEST_APPROVED = 'edx.server.enterprise-access.coupon-code-request-lifecycle.approved'
    COUPON_CODE_REQUEST_DECLINED = 'edx.server.enterprise-access.coupon-code-request-lifecycle.declined'
    LEARNER_CREDIT_REQUEST_CREATED = 'edx.server.enterprise-access.learner-credit-request-lifecycle.created'
    SUBSIDY_REQUEST_CONFIGURATION_CREATED = ('edx.server.enterprise-access.'
                                             'subsidy-request-configuration-lifecycle.created')
    SUBSIDY_REQUEST_CONFIGURATION_UPDATED = ('edx.server.enterprise-access.'
                                             'subsidy-request-configuration-lifecycle.updated')

    SUBSIDY_REQUEST_CREATED = {
        SubsidyTypeChoices.LICENSE: LICENSE_REQUEST_CREATED,
        SubsidyTypeChoices.COUPON: COUPON_CODE_REQUEST_CREATED,
        SubsidyTypeChoices.LEARNER_CREDIT: LEARNER_CREDIT_REQUEST_CREATED,
    }
    SUBSIDY_REQUEST_APPROVED = {
        SubsidyTypeChoices.LICENSE: LICENSE_REQUEST_APPROVED,
        SubsidyTypeChoices.COUPON: COUPON_CODE_REQUEST_APPROVED
    }
    SUBSIDY_REQUEST_DECLINED = {
        SubsidyTypeChoices.LICENSE: LICENSE_REQUEST_DECLINED,
        SubsidyTypeChoices.COUPON: COUPON_CODE_REQUEST_DECLINED
    }
