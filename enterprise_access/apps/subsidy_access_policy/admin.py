""" Admin configuration for subsidy_access_policy models. """

from django.conf import settings
from django.contrib import admin
from django.utils.text import Truncator  # for shortening a text
from djangoql.admin import DjangoQLSearchMixin

from enterprise_access.apps.subsidy_access_policy import constants, models

EVERY_SPEND_LIMIT_FIELD = [
    'spend_limit',
    'policy_spend_limit_dollars',
    'per_learner_spend_limit',
    'per_learner_spend_limit_dollars',
    'per_learner_enrollment_limit',
]


def super_admin_enabled():
    return getattr(settings, 'DJANGO_ADMIN_POLICY_SUPER_ADMIN', False)


def cents_to_usd_string(cents):
    """
    Helper to convert cents as an int to dollars as a
    nicely formatted string.
    """
    if cents is None:
        return None
    return "${:,.2f}".format(float(cents) / constants.CENTS_PER_DOLLAR)


class BaseSubsidyAccessPolicyMixin(admin.ModelAdmin):
    """
    Mixin for common admin properties on subsidy access policy models.
    """
    list_display = (
        'uuid',
        'modified',
        'active',
        'display_name_or_short_description',
        'policy_spend_limit_dollars',
    )
    list_filter = (
        'active',
        'access_method',
    )
    ordering = ['-modified']

    readonly_fields = (
        'created',
        'modified',
        'policy_spend_limit_dollars',
    )

    def _short_description(self, obj):
        return Truncator(str(obj.description)).chars(255)

    def display_name_or_short_description(self, obj):
        if obj.display_name:
            return obj.display_name
        return self._short_description(obj)

    @admin.display(description='Policy-wide spend limit (dollars)')
    def policy_spend_limit_dollars(self, obj):
        """Returns this policy's spend_limit as a US Dollar string."""
        if obj.spend_limit is None:
            return None
        return cents_to_usd_string(obj.spend_limit)


@admin.register(models.PerLearnerEnrollmentCreditAccessPolicy)
class PerLearnerEnrollmentCreditAccessPolicy(DjangoQLSearchMixin, BaseSubsidyAccessPolicyMixin):
    """
    Admin configuration for PerLearnerEnrollmentCreditAccessPolicy.
    """
    list_display = BaseSubsidyAccessPolicyMixin.list_display + (
        'per_learner_enrollment_limit',
    )
    search_fields = (
        'uuid',
        'display_name',
        'enterprise_customer_uuid',
        'catalog_uuid',
        'subsidy_uuid',
    )

    fieldsets = [
        (
            'Base configuration',
            {
                'fields': [
                    'enterprise_customer_uuid',
                    'display_name',
                    'description',
                    'active',
                    'catalog_uuid',
                    'subsidy_uuid',
                    'created',
                    'modified',
                ]
            }
        ),
        (
            'Spend limits',
            {
                'fields': [
                    'spend_limit',
                    'policy_spend_limit_dollars',
                    'per_learner_enrollment_limit',
                ] if not super_admin_enabled() else EVERY_SPEND_LIMIT_FIELD
            }
        ),
    ]


@admin.register(models.PerLearnerSpendCreditAccessPolicy)
class PerLearnerSpendCreditAccessPolicy(DjangoQLSearchMixin, BaseSubsidyAccessPolicyMixin):
    """
    Admin configuration for PerLearnerSpendCreditAccessPolicy.
    """
    list_display = BaseSubsidyAccessPolicyMixin.list_display + (
        'per_learner_spend_limit_dollars',
    )
    readonly_fields = BaseSubsidyAccessPolicyMixin.readonly_fields + (
        'per_learner_spend_limit_dollars',
    )
    search_fields = (
        'uuid',
        'display_name',
        'enterprise_customer_uuid',
        'catalog_uuid',
        'subsidy_uuid',
    )

    fieldsets = [
        (
            'Base configuration',
            {
                'fields': [
                    'enterprise_customer_uuid',
                    'display_name',
                    'description',
                    'active',
                    'catalog_uuid',
                    'subsidy_uuid',
                    'created',
                    'modified',
                ]
            }
        ),
        (
            'Spend limits',
            {
                'fields': [
                    'spend_limit',
                    'policy_spend_limit_dollars',
                    'per_learner_spend_limit',
                    'per_learner_spend_limit_dollars',
                ] if not super_admin_enabled() else EVERY_SPEND_LIMIT_FIELD
            }
        ),
    ]

    @admin.display(description='Per-learner spend limit (dollars)')
    def per_learner_spend_limit_dollars(self, obj):
        """Returns this policy's per_learner_spend_limit as a US Dollar string."""
        if obj.per_learner_spend_limit is None:
            return None
        return cents_to_usd_string(obj.per_learner_spend_limit)


@admin.register(models.AssignedLearnerCreditAccessPolicy)
class LearnerContentAssignmentAccessPolicy(DjangoQLSearchMixin, BaseSubsidyAccessPolicyMixin):
    """
    Admin configuration for AssignedLearnerCreditAccessPolicy.
    """
    search_fields = (
        'uuid',
        'display_name',
        'enterprise_customer_uuid',
        'catalog_uuid',
        'subsidy_uuid',
    )

    fieldsets = [
        (
            'Base configuration',
            {
                'fields': [
                    'enterprise_customer_uuid',
                    'display_name',
                    'description',
                    'active',
                    'catalog_uuid',
                    'subsidy_uuid',
                    'created',
                    'modified',
                ]
            }
        ),
        (
            'Spend limits',
            {
                'fields': [
                    'spend_limit',
                    'policy_spend_limit_dollars',
                ] if not super_admin_enabled() else EVERY_SPEND_LIMIT_FIELD
            }
        ),
    ]
