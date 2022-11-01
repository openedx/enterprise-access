# The python API.
from enterprise_access.apps.subsidy import models


TRANSACTION_METADATA_KEYS = ['opportunity_id', 'request_user', 'request_timestamp', 'etc...']


def create_subscription_subsidy(
        customer_uuid,
        subscription_plan_uuid,
        unit,
        **kwargs,
):
    subsidy, was_created = models.SubscriptionSubsidy.objects.get_or_create(
        customer_uuid=customer_uuid,
        subscription_plan_uuid=subscription_plan_uuid,
        unit=unit,
        defaults=kwargs,
    )

    if kwargs.get('ledger'):
        return subsidy

    from enterprise_access.apps.ledger.api import (
        create_ledger,
        create_idempotency_key_for_subsidy,
        create_transaction,
        create_idempotency_key_for_transaction,
    )
    ledger = create_ledger(
        unit=unit,
        idempotency_key=create_idempotency_key_for_subsidy(subsidy),
    )

    subsidy.ledger = ledger
    if kwargs.get('starting_balance'):
        idpk_data = {k: kwargs[k] for k in TRANSACTION_METADATA_KEYS if k in kwargs}
        idpk = create_idempotency_key_for_transaction(
            subsidy,
            kwargs['starting_balance'],
            **idpk_data,
        )
        tx = create_transaction(ledger, kwargs['starting_balance'], idpk)

    subsidy.save()
    return subsidy
