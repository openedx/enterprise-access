""" Admin configuration for subsidy_access_policy models. """
import json
import logging
from typing import Any, Dict

from django.conf import settings
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import re_path, reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.text import Truncator  # for shortening a text
from django.utils.translation import gettext_lazy
from django_object_actions import DjangoObjectActions, action
from djangoql.admin import DjangoQLSearchMixin
from pygments import highlight
from pygments.formatters import HtmlFormatter  # pylint: disable=no-name-in-module
from pygments.lexers import JsonLexer  # pylint: disable=no-name-in-module
from simple_history.admin import SimpleHistoryAdmin

from enterprise_access.apps.api.serializers.subsidy_access_policy import SubsidyAccessPolicyResponseSerializer
from enterprise_access.apps.core.models import User
from enterprise_access.apps.subsidy_access_policy import constants, models
from enterprise_access.apps.subsidy_access_policy.admin.utils import UrlNames
from enterprise_access.apps.subsidy_access_policy.admin.views import (
    SubsidyAccessPolicyDepositFundsView,
    SubsidyAccessPolicySetLateRedemptionView
)
from enterprise_access.apps.subsidy_access_policy.utils import cents_to_usd_string

from .forms import ForcedPolicyRedemptionForm, SubsidyAccessPolicyForm

logger = logging.getLogger(__name__)


EVERY_SPEND_LIMIT_FIELD = [
    'spend_limit',
    'policy_spend_limit_dollars',
    'per_learner_spend_limit',
    'per_learner_enrollment_limit',
]

FORCED_REDEMPTION_GEAG_KEYS = (
    'geag_first_name',
    'geag_last_name',
    'geag_date_of_birth',
    constants.FALLBACK_EXTERNAL_REFERENCE_ID_KEY,
)
FORCED_REDEMPTION_CURRENT_TIME_KEY = 'geag_terms_accepted_at'
FORCED_REDEMPTION_DATA_SHARE_CONSENT_KEY = 'geag_data_share_consent'
FORCED_REDEMPTION_EMAIL_KEY = 'geag_email'
GEAG_DATETIME_FMT = '%Y-%m-%dT%H:%M:%SZ'


def super_admin_enabled():
    return getattr(settings, 'DJANGO_ADMIN_POLICY_SUPER_ADMIN', False)


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
        'deposit_funds',
    )

    def get_form(self, *args, **kwargs):
        """
        Expand width of certain fields so that large integers (e.g. $100k in cents) are fully visible.
        """
        form = super().get_form(*args, **kwargs)
        form.base_fields['spend_limit'].widget.attrs['style'] = 'width: 10em;'  # Wide enough for billions of dollars.
        return form

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

    @action(
        label='Deposit Funds',
        description='Top-up the subsidy and spend_limit associated with this policy'
    )
    def deposit_funds(self, request, obj):
        """
        Object tool handler method - redirects to deposit_funds view.
        """
        # url names coming from get_urls are prefixed with 'admin' namespace
        deposit_funds_url = reverse('admin:' + UrlNames.DEPOSIT_FUNDS, args=(obj.uuid,))
        return HttpResponseRedirect(deposit_funds_url)

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
            re_path(
                r"^([^/]+)/deposit_funds",
                self.admin_site.admin_view(SubsidyAccessPolicyDepositFundsView.as_view()),
                name=UrlNames.DEPOSIT_FUNDS,
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
    form = SubsidyAccessPolicyForm

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
                    'retired_at',
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
    form = SubsidyAccessPolicyForm

    list_display = BaseSubsidyAccessPolicyMixin.list_display + (
        'per_learner_spend_limit_dollars',
    )
    history_list_display = BaseSubsidyAccessPolicyMixin.history_list_display + (
        'per_learner_spend_limit_dollars',
    )
    readonly_fields = BaseSubsidyAccessPolicyMixin.readonly_fields + (
        'per_learner_spend_limit_dollars',
        'retired_at',
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
                    'retired_at',
                    'catalog_uuid',
                    'subsidy_uuid',
                    'learner_credit_request_config',
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
    form = SubsidyAccessPolicyForm

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
        'retired_at',
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
                    'retired_at',
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
        'subsidy_access_policy__uuid',
        'enterprise_group_uuid',
    )

    list_display = (
        'subsidy_access_policy',
        'enterprise_group_uuid',
    )

    autocomplete_fields = [
        'subsidy_access_policy',
    ]


@admin.register(models.SubsidyAccessPolicy)
class SubsidAccessPolicyAdmin(admin.ModelAdmin):
    """
    We need this not-particularly-useful admin class
    to let the ForcedPolicyRedemptionAdmin class refer
    to subsidy access policies, of all types, via its
    ``autocomplete_fields``.
    It's hidden from the admin index page.
    """
    fields = []
    search_fields = [
        'uuid',
        'display_name',
    ]

    def has_module_permission(self, request):
        """
        Hide this view from the admin index page.
        """
        return False

    def has_change_permission(self, request, obj=None):
        """
        For good measure, declare no change permissions on this admin class.
        """
        return False


@admin.register(models.ForcedPolicyRedemption)
class ForcedPolicyRedemptionAdmin(DjangoQLSearchMixin, SimpleHistoryAdmin):
    """
    Admin class for the forced redemption model/logic.
    """
    form = ForcedPolicyRedemptionForm

    djangoql_completion_enabled_by_default = False
    search_fields = [
        'uuid',
        'subsidy_access_policy__uuid',
        'lms_user_id',
        'course_run_key',
    ]

    list_display = [
        'uuid',
        'policy_uuid',
        'lms_user_id',
        'course_run_key',
        'redeemed_at',
        'errored_at',
    ]
    list_filter = [
        'redeemed_at',
        'errored_at',
    ]
    autocomplete_fields = [
        'subsidy_access_policy',
    ]
    readonly_fields = [
        'redeemed_at',
        'errored_at',
        'transaction_uuid',
        'traceback',
    ]

    def save_model(self, request, obj, form, change) -> None:
        """
        If this record has not been successfully redeemed yet,
        and if ``wait_to_redeem`` is false, then call ``force_redeem()`` on
        the record.
        """
        super().save_model(request, obj, form, change)
        obj.refresh_from_db()

        if obj.transaction_uuid:
            message = gettext_lazy("{} has already been redeemed".format(obj))
            self.message_user(request, message, messages.SUCCESS)
            return

        if obj.wait_to_redeem:
            message = gettext_lazy(
                "{} has wait_to_redeem set to true, redemption will not occur "
                "until this is changed to false".format(obj)
            )
            self.message_user(request, message, messages.WARNING)
            return

        form.full_clean()  # populates cleaned_data below
        try:
            extra_metadata: Dict[str, Any] = {
                key: str(form.cleaned_data.get(key))
                for key in FORCED_REDEMPTION_GEAG_KEYS
                if form.cleaned_data.get(key)
            }
            if extra_metadata:
                user_record = User.objects.get(lms_user_id=form.cleaned_data.get('lms_user_id'))
                extra_metadata[FORCED_REDEMPTION_CURRENT_TIME_KEY] = timezone.now().strftime(GEAG_DATETIME_FMT)
                extra_metadata[FORCED_REDEMPTION_DATA_SHARE_CONSENT_KEY] = True
                extra_metadata[FORCED_REDEMPTION_EMAIL_KEY] = user_record.email
                obj.force_redeem(extra_metadata=extra_metadata)
            else:
                obj.force_redeem()
        except Exception as exc:  # pylint: disable=broad-except
            message = gettext_lazy("{} Failure reason: {}".format(obj, exc))
            self.message_user(request, message, messages.ERROR)
            logger.exception('Force redemption failed for %s', obj)
