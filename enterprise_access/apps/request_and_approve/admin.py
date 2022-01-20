""" Admin configuration for request_and_approve models. """

from django.contrib import admin
from django.utils.translation import ugettext_lazy as _

from enterprise_access.apps.request_and_approve.models import (
	SubsidyRequestCustomerConfiguration,
)

class SubsidyRequestCustomerConfigurationAdmin(admin.ModelAdmin):
    """ Admin configuration for the SubsidyRequestCustomerConfiguration model. """
    list_display = ()
    fieldsets = ()


admin.site.register(
	SubsidyRequestCustomerConfiguration,
	SubsidyRequestCustomerConfigurationAdmin
)
