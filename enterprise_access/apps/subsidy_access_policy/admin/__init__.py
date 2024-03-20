""" Admin configuration for subsidy_access_policy models. """
import json
import logging

from django.conf import settings
from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import re_path, reverse
from django.utils.safestring import mark_safe
from django.utils.text import Truncator  # for shortening a text
from django_object_actions import DjangoObjectActions, action
from djangoql.admin import DjangoQLSearchMixin
from pygments import highlight
from pygments.formatters import HtmlFormatter  # pylint: disable=no-name-in-module
from pygments.lexers import JsonLexer  # pylint: disable=no-name-in-module
from simple_history.admin import SimpleHistoryAdmin

from enterprise_access.apps.api.serializers.subsidy_access_policy import SubsidyAccessPolicyResponseSerializer
from enterprise_access.apps.subsidy_access_policy import constants, models
from enterprise_access.apps.subsidy_access_policy.admin.utils import UrlNames
from enterprise_access.apps.subsidy_access_policy.admin.views import SubsidyAccessPolicySetLateRedemptionView

logger = logging.getLogger(__name__)


EVERY_SPEND_LIMIT_FIELD = [
    'spend_limit',
    'policy_spend_limit_dollars',
    'per_learner_spend_limit',
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


class BaseSubsidyAccessPolicyMixin(DjangoObjectActions, SimpleHistoryAdmin):
    """
    Mixin for common admin properties on subsidy access policy models.
    """
    list_display = (
        'uuid',
        'modified',
        'active',
        'retired',
        'display_name_or_short_description',
        'policy_spend_limit_dollars',
    )
    history_list_display = (
        'active',
        'retired',
        'spend_limit',
    )
    list_filter = (
        'active',
        'retired',
        'access_method',
    )
    ordering = ['-modified']

    readonly_fields = (
        'created',
        'modified',
        'policy_spend_limit_dollars',
        'late_redemption_allowed_until',
        'is_late_redemption_allowed',
        'api_serialized_repr',
    )

    change_actions = (
        'set_late_redemption',
    )

    @action(
        label='Set Late Redemption',
        description='Enable/disable the "late redemption" feature for this policy'
    )
    def set_late_redemption(self, request, obj):
        """
        Object tool handler method - redirects to set_late_redemption view.
        """
        # url names coming from get_urls are prefixed with 'admin' namespace
        set_late_redemption_url = reverse('admin:' + UrlNames.SET_LATE_REDEMPTION, args=(obj.uuid,))
        return HttpResponseRedirect(set_late_redemption_url)

    def get_urls(self):
        """
        Returns the additional urls used by the custom object tools.
        """
        additional_urls = [
            re_path(
                r"^([^/]+)/set_late_redemption",
                self.admin_site.admin_view(SubsidyAccessPolicySetLateRedemptionView.as_view()),
                name=UrlNames.SET_LATE_REDEMPTION,
            ),
        ]
        return additional_urls + super().get_urls()

    @admin.display(description='REST API serialization')
    def api_serialized_repr(self, obj):
        """
        Convenience method to see what the policy details REST API
        response is.  Thanks to:
        https://daniel.feldroy.com/posts/pretty-formatting-json-django-admin
        for this styling idea.
        """
        try:
            data = SubsidyAccessPolicyResponseSerializer(obj).data
            json_string = json.dumps(data, indent=4, sort_keys=True)

            # Get the Pygments formatter
            formatter = HtmlFormatter(style='default')

            # Highlight the data
            response = highlight(json_string, JsonLexer(), formatter)

            # Get the stylesheet
            style = "<style>" + formatter.get_style_defs() + "</style><br>"

            # Safe the output
            return mark_safe(style + response)
        except Exception:  # pylint: disable=broad-except
            return ''

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

    def get_fieldsets(self, request, obj=None):
        """
        Render the API serialization only when we're not
        adding a new policy record.
        """
        fieldsets = super().get_fieldsets(request, obj=obj)
        if obj and not obj._state.adding:  # pylint: disable=protected-access
            try:
                return fieldsets + [('Extra', {'fields': ['api_serialized_repr']})]
            except Exception:  # pylint: disable=broad-except
                return fieldsets
        return fieldsets


@admin.register(models.PerLearnerEnrollmentCreditAccessPolicy)
class PerLearnerEnrollmentCreditAccessPolicy(DjangoQLSearchMixin, BaseSubsidyAccessPolicyMixin):
    """
    Admin configuration for PerLearnerEnrollmentCreditAccessPolicy.
    """
    list_display = BaseSubsidyAccessPolicyMixin.list_display + (
        'per_learner_enrollment_limit',
    )
    history_list_display = BaseSubsidyAccessPolicyMixin.history_list_display + (
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
                    'retired',
                    'catalog_uuid',
                    'subsidy_uuid',
                    'late_redemption_allowed_until',
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
    history_list_display = BaseSubsidyAccessPolicyMixin.history_list_display + (
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
                    'retired',
                    'catalog_uuid',
                    'subsidy_uuid',
                    'late_redemption_allowed_until',
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

    readonly_fields = BaseSubsidyAccessPolicyMixin.readonly_fields + (
        'assignment_configuration',
        'per_learner_spend_limit',
        'per_learner_enrollment_limit',
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
                    'retired',
                    'catalog_uuid',
                    'subsidy_uuid',
                    'late_redemption_allowed_until',
                    'assignment_configuration',
                    'created',
                    'modified',
                ],
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


@admin.register(models.PolicyGroupAssociation)
class PolicyGroupAssociationAdmin(admin.ModelAdmin):
    """
    Admin configuration for PolicyGroupAssociation
    """
    search_fields = (
        'subsidy_access_policy',
        'enterprise_group_uuid',
    )

    list_display = (
        'subsidy_access_policy',
        'enterprise_group_uuid',
    )
