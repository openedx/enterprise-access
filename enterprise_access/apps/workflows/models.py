"""
Models for the workflows app.
"""

from django.db import models
from viewflow.workflow.models import Process
from viewflow import jsonstore


class DefaultEnterpriseCourseEnrollmentProcess(Process):
    """
    Process model to store workflow data for subscription licenses and enrollments.
    """
    activated_subscription_licenses = jsonstore.JSONField(null=True, blank=True)
    default_enterprise_course_enrollments = jsonstore.JSONField(null=True, blank=True)
    redeemable_default_enterprise_course_enrollments = jsonstore.JSONField(null=True, blank=True)

    class Meta:
        proxy = True
