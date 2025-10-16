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


# DEPRECATED: This class was used to add 'reminded' to the list of possible
# actions. Its functionality has been consolidated into the self-contained
# `LearnerCreditRequestActionTypes` class.
class LearnerCreditAdditionalActionStates:
    """ Additional states specifically for LearnerCreditRequestActions. """

    REMINDED = 'reminded'
    CHOICES = (
        (REMINDED, "Reminded"),
    )


# DEPRECATED: This variable combined states from multiple classes in a way that
# was confusing and brittle. Use `LearnerCreditRequestActionTypes.CHOICES` instead.
LearnerCreditRequestActionChoices = SubsidyRequestStates.CHOICES + LearnerCreditAdditionalActionStates.CHOICES


# List of states where a learner cannot make a new request for a course
# if they already have a request in one of these states.
LC_NON_RE_REQUESTABLE_STATES = [
    SubsidyRequestStates.REQUESTED,
    SubsidyRequestStates.APPROVED,
    SubsidyRequestStates.ACCEPTED,
    SubsidyRequestStates.ERROR,
]

REUSABLE_REQUEST_STATES = [
    SubsidyRequestStates.CANCELLED,
    SubsidyRequestStates.EXPIRED,
    SubsidyRequestStates.REVERSED,
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


class LearnerCreditRequestActionTypes:
    """
    Defines the set of possible values for the `recent_action` field on the
    `LearnerCreditRequestActions` model. This represents the specific event
    or operation that occurred (e.g., an approval, a reminder).
    """
    REQUESTED = 'requested'
    APPROVED = 'approved'
    DECLINED = 'declined'
    ERROR = 'error'
    ACCEPTED = 'accepted'
    CANCELLED = 'cancelled'
    EXPIRED = 'expired'
    REVERSED = 'reversed'
    REMINDED = 'reminded'

    CHOICES = (
        (REQUESTED, "Requested"),
        (APPROVED, "Approved"),
        (DECLINED, "Declined"),
        (ERROR, "Error"),
        (ACCEPTED, "Accepted"),
        (CANCELLED, "Cancelled"),
        (EXPIRED, "Expired"),
        (REVERSED, "Reversed"),
        (REMINDED, "Reminded"),
    )


class LearnerCreditRequestUserMessages:
    """
    Defines the set of possible values for the `status` field on the
    `LearnerCreditRequestActions` model. This represents the user-facing
    status label that is displayed in the UI as a result of an action.
    """
    REQUESTED = 'requested'
    REMINDED = 'reminded'
    APPROVED = 'approved'
    ACCEPTED = 'accepted'
    DECLINED = 'declined'
    REVERSED = 'reversed'
    CANCELLED = 'cancelled'
    EXPIRED = 'expired'
    ERROR = 'error'

    CHOICES = (
        (REQUESTED, "Requested"),
        (REMINDED, "Waiting For Learner"),
        (APPROVED, "Waiting For Learner"),
        (ACCEPTED, "Redeemed By Learner"),
        (DECLINED, "Declined"),
        (REVERSED, "Refunded"),
        (CANCELLED, "Cancelled"),
        (EXPIRED, "Expired"),
        (ERROR, "Error"),
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
