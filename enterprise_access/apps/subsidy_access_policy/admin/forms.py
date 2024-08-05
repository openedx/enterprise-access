"""
Forms to be used for subsidy_access_policy django admin.
"""
from django import forms
from django.conf import settings
from django.utils.translation import gettext as _


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
