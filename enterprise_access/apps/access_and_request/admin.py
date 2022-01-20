""" Admin configuration for core models. """

from django.contrib import admin
from django.utils.translation import ugettext_lazy as _

from enterprise_access.apps.access_and_request.models import (
	SubsidyRequestCustomerConfiguration,
)

class SubsidyRequestCustomerConfigurationAdmin(admin.ModelAdmin):
    """ Admin configuration for the custom User model. """
    list_display = ()
    fieldsets = ()


admin.site.register(
	SubsidyRequestCustomerConfiguration,
	SubsidyRequestCustomerConfigurationAdmin
)
