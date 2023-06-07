""" Admin configuration for subsidy_access_policy models. """


from django.contrib import admin
from django.utils.text import Truncator  # for shortening a text

from enterprise_access.apps.subsidy_access_policy import models


class BaseSubsidyAccessPolicyMixin(admin.ModelAdmin):
    """
    Mixin for common admin properties on subsidy access policy models.
    """
    list_display = (
        'uuid',
        'modified',
        'active',
        'short_description',
        'spend_limit',
    )
    list_filter = (
        'active',
        'access_method',
    )
    search_fields = (
        'uuid',
        'enterprise_customer_uuid',
        'catalog_uuid',
        'subsidy_uuid',
    )
    ordering = ['-modified']

    def short_description(self, obj):
        return Truncator(str(obj.description)).chars(255)


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
    Admin configuration for PerLearnerSpendCreditAccessPolicy.
    """
    list_display = BaseSubsidyAccessPolicyMixin.list_display + (
        'per_learner_spend_limit',
    )
