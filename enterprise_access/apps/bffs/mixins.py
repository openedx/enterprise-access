"""
Mixins for accessing `HandlerContext` data for bffs app
"""


class BFFContextDataMixin:
    """
    Mixin to validate that the `self.context` attribute is defined.
    """

    def __init__(self, *args, **kwargs):
        """
        Initialize the BFFDataMixin, ensuring that the
        `self.context` attribute is defined.
        """
        super().__init__(*args, **kwargs)
        if not hasattr(self, 'context'):
            raise AttributeError("The `self.context` attribute must be defined.")


class EnterpriseCustomerUserSubsidiesDataMixin(BFFContextDataMixin):
    """
    Mixin to access enterprise customer user subsidies data from the context.
    """

    @property
    def enterprise_customer_user_subsidies(self):
        """
        Get enterprise customer user subsidies from the context.
        """
        return self.context.data.get('enterprise_customer_user_subsidies', {})


class LearnerSubscriptionsDataMixin(EnterpriseCustomerUserSubsidiesDataMixin):
    """
    Mixin to access learner subscriptions data from the context.
    """

    @property
    def subscriptions(self):
        """
        Get subscriptions from the context.
        """
        return self.enterprise_customer_user_subsidies.get('subscriptions', {})

    @property
    def customer_agreement(self):
        """
        Get customer agreement from the context.
        """
        return self.subscriptions.get('customer_agreement', {})

    @property
    def subscription_licenses(self):
        """
        Get subscription licenses from the context.
        """
        return self.subscriptions.get('subscription_licenses', [])

    @property
    def subscription_licenses_by_status(self):
        """
        Get subscription licenses by status from the context.
        """
        return self.subscriptions.get('subscription_licenses_by_status', {})

    @property
    def subscription_license(self):
        """
        Get subscription license from the context.
        """
        return self.subscriptions.get('subscription_license', None)

    @property
    def subscription_plan(self):
        """
        Get subscription plan from the context.
        """
        return self.subscriptions.get('subscription_plan', {})

    @property
    def show_subscription_expiration_notifications(self):
        """
        Get whether subscription expiration notifications should be shown from the context.
        """
        return self.subscriptions.get('show_expiration_notifications', False)


class LearnerSubsidiesDataMixin(LearnerSubscriptionsDataMixin):
    """
    Mixin to access learner subsidies data from the context (e.g., subscriptions)
    """


class BaseLearnerDataMixin(LearnerSubsidiesDataMixin, BFFContextDataMixin):
    """
    Mixin to access shared common properties for learner-focused routes.
    """

    @property
    def default_enterprise_enrollment_intentions(self):
        """
        Get default enterprise enrollment intentions from the context.
        """
        return self.context.data.get('default_enterprise_enrollment_intentions', {})


class LearnerDashboardDataMixin(BaseLearnerDataMixin):
    """
    Mixin to access learner dashboard data from the context.
    """

    @property
    def enterprise_course_enrollments(self):
        """
        Get enterprise course enrollments from the context.
        """
        return self.context.data.get('enterprise_course_enrollments', [])
