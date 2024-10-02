"""
Admin for workflows app.
"""

import json

from django.contrib import admin
from django.utils.safestring import mark_safe
from viewflow.workflow.admin import ProcessAdmin

from enterprise_access.apps.workflows.models import DefaultEnterpriseCourseEnrollmentProcess

@admin.register(DefaultEnterpriseCourseEnrollmentProcess)
class DefaultEnterpriseCourseEnrollmentProcessAdmin(ProcessAdmin):
    """
    Admin view for DefaultEnterpriseCourseEnrollmentProcess.
    Inherits from Viewflow's ProcessAdmin to display process details.
    """
    list_display = ['pk', 'flow_class', 'created', 'status', 'finished']
    list_filter = ['status']
    search_fields = ['id']

    fields = [
        'flow_class',
        'created',
        'finished',
        'status',
        'data_formatted',
        'activated_subscription_licenses_formatted',
        'default_enterprise_course_enrollments_formatted',
        'redeemable_default_enterprise_course_enrollments_formatted',
        'redeemed_default_enterprise_course_enrollments_formatted',
        'parent_task',
        'seed_content_type',
        'seed_object_id',
        'artifact_content_type',
        'artifact_object_id',
    ]

    readonly_fields = fields

    def data_formatted(self, obj):
        """
        Return JSON data as a formatted string.
        """
        formatted_json = json.dumps(obj.data, indent=4)
        return mark_safe(f"<pre>{formatted_json}</pre>")

    data_formatted.short_description = 'Data'

    def activated_subscription_licenses_formatted(self, obj):
        """
        Return JSON activated subscription licenses as a formatted string.
        """
        formatted_json = json.dumps(obj.activated_subscription_licenses, indent=4)
        return mark_safe(f"<pre>{formatted_json}</pre>")

    activated_subscription_licenses_formatted.short_description = 'Activated subscription licenses'

    def default_enterprise_course_enrollments_formatted(self, obj):
        """
        Return JSON default enterprise course enrollments as a formatted string.
        """
        formatted_json = json.dumps(obj.default_enterprise_course_enrollments, indent=4)
        return mark_safe(f"<pre>{formatted_json}</pre>")

    default_enterprise_course_enrollments_formatted.short_description = 'Default enterprise course enrollments'

    def redeemable_default_enterprise_course_enrollments_formatted(self, obj):
        """
        Return JSON redeemable default enterprise course enrollments as a formatted string.
        """
        formatted_json = json.dumps(obj.redeemable_default_enterprise_course_enrollments, indent=4)
        return mark_safe(f"<pre>{formatted_json}</pre>")

    redeemable_default_enterprise_course_enrollments_formatted.short_description =\
        'Redeemable default enterprise course enrollments'

    def redeemed_default_enterprise_course_enrollments_formatted(self, obj):
        """
        Return JSON redeemed default enterprise course enrollments as a formatted string.
        """
        formatted_json = json.dumps(obj.redeemed_default_enterprise_course_enrollments, indent=4)
        return mark_safe(f"<pre>{formatted_json}</pre>")

    redeemed_default_enterprise_course_enrollments_formatted.short_description =\
        'Redeemed default enterprise course enrollments'
