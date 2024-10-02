"""
App config for workflows
"""

from django.apps import AppConfig


class WorkflowsConfig(AppConfig):
    """
    App config for workflows.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'enterprise_access.apps.workflows'
