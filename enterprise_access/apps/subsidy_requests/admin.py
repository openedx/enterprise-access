""" Admin configuration for subsidy_requests models. """

from django.contrib import admin
from django.utils.translation import ugettext_lazy as _

from enterprise_access.apps.subsidy_requests.models import (
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
