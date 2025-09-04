"""
Forms to be used for subsidy_access_policy django admin.
"""
import requests
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from enterprise_access.apps.subsidy_access_policy.utils import get_versioned_subsidy_client

from ..models import ForcedPolicyRedemption, SubsidyAccessPolicy


class LateRedemptionDaysFromNowChoices:
    """
    Enumerate different choices for the type of Subsidy.  For example, this can be used to control whether enrollments
    associated with this Subsidy should be rev rec'd through our standard commercial process or not.
    """
    DISABLE_NOW = "disable_now"
    CHOICES = (
        (DISABLE_NOW, _("Disable now")),
        ("1", _("1")),
        ("2", _("2")),
        ("3", _("3")),
        ("4", _("4")),
        ("5", _("5")),
        ("6", _("6")),
        ("7", _("7")),
    )


class SetLateRedemptionForm(forms.Form):
    """
    Form to set late redemption timeline.
    """
    days_from_now = forms.ChoiceField(
        label=_("Enable late redemptions until _ days from now"),
        choices=LateRedemptionDaysFromNowChoices.CHOICES,
        help_text=_("Unless disabled now, late redemptions will be disabled at midnight UTC of the selected day."),
        required=True,
    )


class DepositFundsForm(forms.Form):
    """
    Form to deposit funds into a subsidy from a policy.
    """
    desired_quantity_usd = forms.FloatField(
        label=_("Top-up quantity in USD"),
        help_text=_(
            "Amount of funds to add to the associated Subsidy, and also by which to increase this Policy's spend_limit."
        ),
        required=True,
        min_value=1,
    )
    sales_contract_reference_id = forms.RegexField(
        label=settings.SALES_CONTRACT_REFERENCE_PROVIDER_NAME,
        help_text=_(
            "Reference the original sales object that originated this deposit. "
            "This must be the 18-character case-insensitive version of the ID."
        ),
        required=True,
        regex="^00[kK][0-9a-zA-Z]{15}$",
        error_messages={
            "invalid": 'Salesforce Opportunity Line Item ID is invalid. Must be 18 characters and start with "00k".',
            "required": 'Salesforce Opportunity Line Item ID is required.',
        }
    )


class ForcedPolicyRedemptionForm(forms.ModelForm):
    """
    Admin form for the ForcedPolicyRedemption model.
    """
    geag_first_name = forms.CharField(
        label=_("Learner First Name (EE-only)"),
        help_text=_(
            "First name of learner, only used for Exec Ed Redemptions. "
            "This value (along with last name and D.O.B.) are not persisted; "
            "if you select 'Wait to redeem', the values will be gone when you try to save later."
        ),
        required=False,
    )
    geag_last_name = forms.CharField(
        label=_("Learner Last Name (EE-only)"),
        help_text=_("Last name of learner, only used for Exec Ed Redemptions"),
        required=False,
    )
    geag_date_of_birth = forms.DateField(
        label=_("Learner Date of Birth (EE-only)"),
        help_text=_("Learner date of birth, only used for Exec Ed Redemptions"),
        required=False,
    )
    fallback_external_fulfillment_reference_id = forms.UUIDField(
        label=_("Order UUID of existing GEAG allocation (EE-only)"),
        help_text=_(
            "Link new transaction to a specific Order UUID. This is rarely needed, "
            "as a GEAG allocation will typically not yet exist and neither will an "
            "Order UUID. Only used for Exec Ed Redemptions."
        ),
        required=False,
    )

    class Meta:
        model = ForcedPolicyRedemption
        fields = '__all__'


class SubsidyAccessPolicyForm(forms.ModelForm):
    """
    Admin form for the SubsidyAccessPolicy model.
    """
    def clean_subsidy_uuid(self):
        """
        Validate that the subsidy exists and is assigned to the same enterprise customer as the budget.
        """
        # 1. check if the subsidy_uuid actually exists
        # 2. subsidy is assigned to the same enterprise customer as the budget
        # if any of these checks fail, raise a ValidationError
        client = get_versioned_subsidy_client(version=1)
        try:
            subsidy = client.retrieve_subsidy(self.cleaned_data["subsidy_uuid"])
        except requests.exceptions.HTTPError as exc:
            raise ValidationError("Subsidy does not exist") from exc

        if str(subsidy["enterprise_customer_uuid"]) != str(self.cleaned_data["enterprise_customer_uuid"]):
            raise ValidationError("Subsidy is not assigned to the same enterprise customer as the budget")

        return self.cleaned_data["subsidy_uuid"]

    class Meta:
        model = SubsidyAccessPolicy
        fields = '__all__'
