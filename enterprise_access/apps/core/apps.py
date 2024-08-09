""" App config for the core module. """

from django.apps import AppConfig


class CoreAppConfig(AppConfig):
    default_auto_field = 'django.db.models.AutoField'
    name = 'enterprise_access.apps.core'
