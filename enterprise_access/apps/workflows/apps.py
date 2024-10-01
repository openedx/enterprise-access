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

    def ready(self):
        """
        Import decorated workflow handlers when the app is ready
        """
        # pylint: disable=unused-import, import-outside-toplevel
        import enterprise_access.apps.workflows.handlers

        # Perform cleanup of the registry at startup
        self.cleanup_registry()

    def cleanup_registry(self):
        """
        Cleans up the action registry by removing any WorkflowActionSteps that no longer
        exist in the registered action list. This is called on app startup.
        """
        # pylint: disable=import-outside-toplevel
        from enterprise_access.apps.workflows.registry import WorkflowActionRegistry
        WorkflowActionRegistry.cleanup_registry()
