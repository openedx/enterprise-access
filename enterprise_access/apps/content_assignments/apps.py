""" App config for content_assignments """

from django.apps import AppConfig


class ContentAssignmentsConfig(AppConfig):
    """
    App config for content_assignments.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'enterprise_access.apps.content_assignments'

    def ready(self):
        super().ready()

        # pylint: disable=unused-import, import-outside-toplevel
        import enterprise_access.apps.content_assignments.signals
