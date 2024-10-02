"""
Admin for workflows app.
"""

from django.contrib import admin
from viewflow.workflow.admin import ProcessAdmin

from enterprise_access.apps.workflows.models import DefaultEnterpriseCourseEnrollmentProcess

@admin.register(DefaultEnterpriseCourseEnrollmentProcess)
class DefaultEnterpriseCourseEnrollmentProcessAdmin(ProcessAdmin):
    """
    Admin view for DefaultEnterpriseCourseEnrollmentProcess.
    Inherits from Viewflow's ProcessAdmin to display process details.
    """
    list_display = ['id', 'created', 'modified', 'status']
    list_filter = ['status']
    search_fields = ['id']

    fields = '__all__'
