""" Admin configuration for subsidy_access_policy models. """


from django.contrib import admin

from enterprise_access.apps.subsidy_access_policy import models


class BaseSubsidyAccessPolicyMixin(admin.ModelAdmin):
    """
    Mixin for common admin properties on subsidy access policy models.
    """
    list_display = (
        'uuid',
        'active',
        'enterprise_customer_uuid',
        'catalog_uuid',
        'subsidy_uuid',
        'access_method',
        'spend_limit',
    )
    list_filter = (
        'active',
        'access_method',
        'subsidy_uuid',
    )
    search_fields = (
        'uuid',
        'enterprise_customer_uuid',
        'catalog_uuid',
        'subsidy_uuid',
    )


@admin.register(models.PerLearnerEnrollmentCreditAccessPolicy)
class PerLearnerEnrollmentCreditAccessPolicy(BaseSubsidyAccessPolicyMixin):
    """
    Admin configuration for PerLearnerEnrollmentCreditAccessPolicy.
    """
    list_display = BaseSubsidyAccessPolicyMixin.list_display + (
        'per_learner_enrollment_limit',
    )


@admin.register(models.PerLearnerSpendCreditAccessPolicy)
class PerLearnerSpendCreditAccessPolicy(BaseSubsidyAccessPolicyMixin):
    """
    Admin configuration for PerLearnerEnrollmentCreditAccessPolicy.
    """
    list_display = BaseSubsidyAccessPolicyMixin.list_display + (
        'per_learner_spend_limit',
    )
