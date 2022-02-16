""" Constants for the subsidy_request app. """

class SubsidyRequestStates:
    """ Possible states of a subsidy request. """

    REQUESTED = 'requested'
    PENDING = 'pending'
    APPROVED = 'approved'
    DECLINED = 'declined'
    ERROR = 'error'

    CHOICES = (
        (REQUESTED, "Requested"),
        (PENDING, "Pending"),
        (APPROVED, "Approved"),
        (DECLINED, "Declined"),
        (ERROR, "Error"),
    )


class SubsidyTypeChoices:
    """ Type of subsidies. """
    LICENSE = 'License'
    COUPON = 'Coupon'  # aka A la cart

    CHOICES = (
        (LICENSE, 'License Subsidy'),
        (COUPON, 'Coupon Subsidy'),
    )

SUBSIDY_TYPE_CHANGE_DECLINATION = (
    'Declined because subsidy type on SubsidyRequestCustomerConfiguration '
    'has changed.'
)

ENTERPRISE_BRAZE_ALIAS_LABEL = 'Enterprise'  # Do Not change this, this is consistent with other uses across edX repos.
