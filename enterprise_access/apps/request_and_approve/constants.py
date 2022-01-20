""" Constants for the request_and_access app. """

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
