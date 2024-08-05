"""
Custom Django Admin views for subsidy_access_policy app.
"""
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import View

from enterprise_access.apps.subsidy_access_policy import constants
from enterprise_access.apps.subsidy_access_policy.admin.forms import (
    DepositFundsForm,
    LateRedemptionDaysFromNowChoices,
    SetLateRedemptionForm
)
from enterprise_access.apps.subsidy_access_policy.admin.utils import UrlNames
from enterprise_access.apps.subsidy_access_policy.models import SubsidyAccessPolicy
from enterprise_access.utils import localized_utcnow


class SubsidyAccessPolicySetLateRedemptionView(View):
    """
    View which allows admins to set the late redemption timeline for a given policy.
    """
    template = "subsidy_access_policy/admin/set_late_redemption.html"

    def get(self, request, policy_uuid):
        """
        Handle GET request - render "Set Late Redemption" form.

        Args:
            request (django.http.request.HttpRequest): Request instance
            policy_uuid (str): Subsidy Access Policy UUID

        Returns:
            django.http.response.HttpResponse: HttpResponse
        """
        policy = SubsidyAccessPolicy.objects.get(uuid=policy_uuid)
        opts = policy._meta
        context = {
            'ENTERPRISE_LEARNER_PORTAL_URL': settings.ENTERPRISE_LEARNER_PORTAL_URL,
            'set_late_redemption_form': SetLateRedemptionForm(),
            'subsidy_access_policy': policy,
            'opts': opts,
        }
        return render(request, self.template, context)

    def post(self, request, policy_uuid):
        """
        Handle POST request - handle form submissions.

        Arguments:
            request (django.http.request.HttpRequest): Request instance
            policy_uuid (str): Subsidy Access Policy UUID

        Returns:
            django.http.response.HttpResponse: HttpResponse
        """
        policy = SubsidyAccessPolicy.objects.get(uuid=policy_uuid)
        set_late_redemption_form = SetLateRedemptionForm(request.POST)

        if set_late_redemption_form.is_valid():
            days_from_now = set_late_redemption_form.cleaned_data.get('days_from_now')
            if days_from_now == LateRedemptionDaysFromNowChoices.DISABLE_NOW:
                policy.late_redemption_allowed_until = None
                policy.save()
            else:
                late_redemption_allowed_until = localized_utcnow() + timedelta(days=int(days_from_now))
                # Force time to the end-of-day UTC. This is consistent with the help text in the HTML template.
                late_redemption_allowed_until = late_redemption_allowed_until.replace(
                    hour=23,
                    minute=59,
                    second=59,
                    microsecond=999999,
                )
                policy.late_redemption_allowed_until = late_redemption_allowed_until
                policy.save()

            messages.success(request, _("Successfully set late redemption."))

            # Redirect to form GET if everything went smooth.
            set_late_redemption_url = reverse("admin:" + UrlNames.SET_LATE_REDEMPTION, args=(policy_uuid,))
            return HttpResponseRedirect(set_late_redemption_url)

        # Somehow, form validation failed. Re-render form.
        context = {
            'set_late_redemption_form': SetLateRedemptionForm(),
            'subsidy_access_policy': policy,
            'ENTERPRISE_LEARNER_PORTAL_URL': settings.ENTERPRISE_LEARNER_PORTAL_URL
        }
        return render(request, self.template, context)


class SubsidyAccessPolicyDepositFundsView(View):
    """
    View which allows admins to deposit funds into a Subsidy from a Policy.
    """
    template = "subsidy_access_policy/admin/deposit_funds.html"

    def get(self, request, policy_uuid):
        """
        Handle GET request - render "Deposit Funds" form.

        Args:
            request (django.http.request.HttpRequest): Request instance
            policy_uuid (str): Subsidy Access Policy UUID

        Returns:
            django.http.response.HttpResponse: HttpResponse
        """
        policy = SubsidyAccessPolicy.objects.get(uuid=policy_uuid)
        opts = policy._meta
        context = {
            'deposit_funds_form': DepositFundsForm(),
            'subsidy_access_policy': policy,
            'opts': opts,
        }
        return render(request, self.template, context)

    def post(self, request, policy_uuid):
        """
        Handle POST request - handle form submissions.

        Arguments:
            request (django.http.request.HttpRequest): Request instance
            policy_uuid (str): Subsidy Access Policy UUID

        Returns:
            django.http.response.HttpResponse: HttpResponse
        """
        policy = SubsidyAccessPolicy.objects.get(uuid=policy_uuid)
        opts = policy._meta
        deposit_funds_form = DepositFundsForm(request.POST)

        if deposit_funds_form.is_valid():
            desired_deposit_quantity_usd = deposit_funds_form.cleaned_data.get('desired_quantity_usd')
            sales_contract_reference_id = deposit_funds_form.cleaned_data.get('sales_contract_reference_id')

            policy.create_deposit(
                desired_deposit_quantity=desired_deposit_quantity_usd * constants.CENTS_PER_DOLLAR,
                sales_contract_reference_id=sales_contract_reference_id,
                sales_contract_reference_provider=settings.SALES_CONTRACT_REFERENCE_PROVIDER_SLUG,
            )

            messages.success(request, _("Successfully deposited funds."))

            # Redirect to form GET if everything went smooth.
            deposit_funds_url = reverse("admin:" + UrlNames.DEPOSIT_FUNDS, args=(policy_uuid,))
            return HttpResponseRedirect(deposit_funds_url)
        else:
            messages.error(request, _("One or more invalid inputs."))

        # Form validation failed. Re-render form.
        context = {
            'deposit_funds_form': deposit_funds_form,
            'subsidy_access_policy': policy,
            'opts': opts,
        }
        return render(request, self.template, context)
