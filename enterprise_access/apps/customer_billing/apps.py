""" App config for customer_billing """

from django.apps import AppConfig


class CustomerBillingConfig(AppConfig):
    """ App config for customer_billing. """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'enterprise_access.apps.customer_billing'

    def ready(self):
        import enterprise_access.apps.customer_billing.signals  # pylint: disable=import-outside-toplevel,unused-import
