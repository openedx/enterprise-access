""" Constants for the subsidy_request app. """

class SubsidyRequestStates:
    """ Possible states of a subsidy request. """

    PENDING_REVIEW = "pending_review"
    APPROVED_PENDING = "approved_pending"
    APPROVED_FULFILLED= "approved_fulfilled"
    DENIED = "denied"

    CHOICES = (
        (PENDING_REVIEW, "Pending Review"),
        (APPROVED_PENDING, "Approved - Pending"),
        (APPROVED_FULFILLED, "Approved - Fulfilled"),
        (DENIED, "Denied"),
    )


class SubsidyTypeChoices:
    LICENSE = 'License'
    COUPON = 'Coupon'  # aka A la cart

    CHOICES = (
        (LICENSE, 'License Subsidy'),
        (COUPON, 'Coupon Subsidy'),
    )


class PendingRequestReminderFrequency:
    NEVER = 'Never'
    DAILY = 'Daily'
    WEEKLY = 'Weekly'
    FORTNIGHTLY = 'Fortnightly'
    MONTHLY = 'Monthly'

    CHOICES = (
        (NEVER, 'Never Remind'),
        (DAILY, 'Once a Day'),
        (WEEKLY, 'Once a Week'),
        (FORTNIGHTLY, 'Once Every Two Weeks'),
        (MONTHLY, 'Once a Month'),
    )
