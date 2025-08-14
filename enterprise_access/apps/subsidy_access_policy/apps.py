""" App config for subsidy_access_policy """

from django.apps import AppConfig


class SubsidyAccessPolicyConfig(AppConfig):
    """
    Initialization app for enterprise_access.apps.subsidy_access_policy.
    Necessary so that django signals in this app are registered.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'enterprise_access.apps.subsidy_access_policy'

    def ready(self):
        super().ready()

        # pylint: disable=unused-import, import-outside-toplevel
        import enterprise_access.apps.subsidy_access_policy.signals
