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
    LICENSE = 'license'
    COUPON = 'coupon'  # aka A la cart

    CHOICES = (
        (LICENSE, 'License Subsidy'),
        (COUPON, 'Coupon Subsidy'),
    )


SUBSIDY_TYPE_CHANGE_DECLINATION = (
    'Declined because subsidy type on SubsidyRequestCustomerConfiguration '
    'has changed.'
)

SUBSIDY_REQUEST_BULK_OPERATION_BATCH_SIZE = 100

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
    SUBSIDY_REQUEST_CONFIGURATION_CREATED = ('edx.server.enterprise-access.'
                                             'subsidy-request-configuration-lifecycle.created')
    SUBSIDY_REQUEST_CONFIGURATION_UPDATED = ('edx.server.enterprise-access.'
                                             'subsidy-request-configuration-lifecycle.updated')

    SUBSIDY_REQUEST_CREATED = {
        SubsidyTypeChoices.LICENSE: LICENSE_REQUEST_CREATED,
        SubsidyTypeChoices.COUPON: COUPON_CODE_REQUEST_CREATED
    }
    SUBSIDY_REQUEST_APPROVED = {
        SubsidyTypeChoices.LICENSE: LICENSE_REQUEST_APPROVED,
        SubsidyTypeChoices.COUPON: COUPON_CODE_REQUEST_APPROVED
    }
    SUBSIDY_REQUEST_DECLINED = {
        SubsidyTypeChoices.LICENSE: LICENSE_REQUEST_DECLINED,
        SubsidyTypeChoices.COUPON: COUPON_CODE_REQUEST_DECLINED
    }
