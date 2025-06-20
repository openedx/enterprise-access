"""
Utils for subsidy_access_policy
"""
import hashlib

from django.apps import apps
from django.conf import settings
from edx_enterprise_subsidy_client import get_enterprise_subsidy_api_client
from simple_history.models import HistoricalRecords, registered_models

from enterprise_access.apps.subsidy_access_policy import constants

LEDGERED_SUBSIDY_IDEMPOTENCY_KEY_PREFIX = 'ledger-for-subsidy'
TRANSACTION_METADATA_KEYS = {
    'lms_user_id',
    'content_key',
    'subsidy_access_policy_uuid',
    'historical_redemptions_uuids',
}


def get_versioned_subsidy_client(version=None):
    """
    Returns an instance of the enterprise subsidy client as the version specified by the
    Django setting `ENTERPRISE_SUBSIDY_API_CLIENT_VERSION`, if any.
    """
    kwargs = {}
    if not version:
        if getattr(settings, 'ENTERPRISE_SUBSIDY_API_CLIENT_VERSION', None):
            kwargs['version'] = int(settings.ENTERPRISE_SUBSIDY_API_CLIENT_VERSION)
    else:
        kwargs['version'] = int(version)
    return get_enterprise_subsidy_api_client(**kwargs)


def create_idempotency_key_for_transaction(subsidy_uuid, **metadata):
    """
    Create a key that allows a transaction to be created idempotently.
    """
    idpk_data = {
        tx_key: value
        for tx_key, value in metadata.items()
        if tx_key in TRANSACTION_METADATA_KEYS
    }
    hashed_metadata = hashlib.md5(str(idpk_data).encode()).hexdigest()
    return f'{LEDGERED_SUBSIDY_IDEMPOTENCY_KEY_PREFIX}-{subsidy_uuid}-{hashed_metadata}'


def sort_subsidy_access_policies_for_redemption(queryset):
    """
    Sorts the query set during can-redeem by the following parameters
           - priority (of type)
           - expiration, sooner to expire first
           - balance, lower balance first
    """
    if queryset.count() <= 1:
        return queryset
    return sorted(
        queryset,
        key=lambda p: (p.priority, p.subsidy_expiration_datetime, p.subsidy_balance())
    )


class ProxyAwareHistoricalRecords(HistoricalRecords):
    """
    This specialized HistoricalRecords model field is to be used specifically for tracking history on instances of proxy
    models.  Set `history = ProxyAwareHistoricalRecords(inherit=True) on the parent (concrete) model, and proxy models
    that subclass the parent will have their history tracked in the history table of the parent.

    The only downside is that this seems to cause no-op migrations to be created for each proxy model (as opposed to no
    migration at all).  The migration defines a historical table for the proxy model with no fields, which sqlmigrate
    translates to an empty transaction: `BEGIN; COMMIT;`.

    Copied verbatim from https://github.com/jazzband/django-simple-history/issues/544#issuecomment-1538615799
    """
    def _find_base_history(self, opts):
        """
        Search for a history model in the parent models that we can re-use.
        """
        base_history = None
        for parent_class in opts.parents.keys():
            if hasattr(parent_class, 'history'):
                base_history = parent_class.history.model
        return base_history

    def create_history_model(self, model, inherited):
        """
        Override super.create_history_model() to force creation of a history model if this is a proxy model.
        """
        opts = model._meta
        if opts.proxy:
            base_history = self._find_base_history(opts)
            if base_history:
                return self.create_proxy_history_model(model, inherited, base_history)

        return super().create_history_model(model, inherited)

    def create_proxy_history_model(self, model, inherited, base_history):
        """
        Create a history model for this proxy model, inheriting from the parent history model so that historical records
        are tracked in the latter.
        """
        opts = model._meta
        attrs = {
            '__module__': self.module,
            '_history_excluded_fields': self.excluded_fields,
        }
        app_module = f'{opts.app_label}.models'
        if inherited:
            attrs['__module__'] = model.__module__
        elif model.__module__ != self.module:
            # registered under different app
            attrs['__module__'] = self.module
        elif app_module != self.module:
            # Abuse an internal API because the app registry is loading.
            app = apps.app_configs[opts.app_label]
            models_module = app.name
            attrs['__module__'] = models_module

        attrs.update(
            Meta=type(
                'Meta',
                (),
                {**self.get_meta_options(model), 'proxy': True}
            )
        )
        if self.table_name is not None:
            attrs['Meta'].db_table = self.table_name

        name = self.get_history_model_name(model)
        registered_models[opts.db_table] = model
        return type(str(name), (base_history,), attrs)


def cents_to_usd_string(cents):
    """
    Helper to convert cents as an int to dollars as a
    nicely formatted string.
    """
    if cents is None:
        return None
    return "${:,.2f}".format(float(cents) / constants.CENTS_PER_DOLLAR)
