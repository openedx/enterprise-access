""" App config for customer_billing """

from django.apps import AppConfig


class CustomerBillingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'enterprise_access.apps.customer_billing'

    def ready(self):
        import enterprise_access.apps.customer_billing.signals  # noqa
