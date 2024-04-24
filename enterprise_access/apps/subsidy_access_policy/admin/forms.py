"""
Forms to be used for subsidy_access_policy django admin.
"""
from django import forms
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
