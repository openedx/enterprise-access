
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
