""" Admin configuration for subsidy_requests models. """

import logging

from django.conf import settings
from django.contrib import admin
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import ugettext_lazy as _

from enterprise_access.apps.core.models import User
from enterprise_access.apps.subsidy_requests.models import (
    SubsidyRequestCustomerConfiguration,
)
from enterprise_access.apps.subsidy_requests.utils import (
    get_data_from_jwt_payload,
    get_user_from_request_session,
)


logger = logging.getLogger(__name__)


class SubsidyRequestCustomerConfigurationAdmin(admin.ModelAdmin):
    """ Admin configuration for the SubsidyRequestCustomerConfiguration model. """
    writable_fields = [
        'subsidy_requests_enabled',
        'subsidy_type',
        'pending_request_reminder_frequency',
        
    ]
    exclude = ['changed_by']
    readonly_fields = [
        'enterprise_customer_uuid',
        'last_changed_by',
    ]

    def last_changed_by(self, obj):
        return 'LMS User: {} ({})'.format(
            obj.changed_by.lms_user_id,
            obj.changed_by.email,
        )

    def save_model(self, request, obj, form, change):
        """
        Override save_model method to keep our change records up to date.
        """
        current_user = get_user_from_request_session(request)
        jwt_data = get_data_from_jwt_payload(request, ['user_id'])
        # Make sure we update the user object's lms_user_id if it's not set
        # or if it has changed to keep our DB up to date, because we have
        # no way to predict if the user has hit a rest endpoint and had
        # their info prepopulated already.
        lms_user_id = jwt_data['user_id']
        if not current_user.lms_user_id or current_user.lms_user_id != lms_user_id:
            current_user.lms_user_id = lms_user_id
            current_user.save()

        obj.changed_by = current_user

        super().save_model(request, obj, form, change)


admin.site.register(
    SubsidyRequestCustomerConfiguration,
    SubsidyRequestCustomerConfigurationAdmin
)
