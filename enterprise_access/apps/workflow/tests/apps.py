""" App config for workflows, required for unit testing. """

from django.apps import AppConfig


class WorkflowTestsConfig(AppConfig):
    """
    App config for workflow.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'enterprise_access.apps.workflow.tests'
