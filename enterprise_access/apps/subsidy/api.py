# The python API.
from uuid import uuid4
from enterprise_access.apps.subsidy import models


TRANSACTION_METADATA_KEYS = ['opportunity_id', 'request_user', 'request_timestamp', 'etc...']


def create_learner_credit_subsidy(customer_uuid, unit, **kwargs):
    """
    Create a subsidy record.
    Create a ledger with starting balance.
    Return the subsidy record.
    """
    subsidy, _ = models.LearnerCreditSubsidy.objects.get_or_create(
        customer_uuid=customer_uuid,
        unit=unit,
        defaults=kwargs,
    )

    if kwargs.get('ledger'):
        return subsidy

    from enterprise_access.apps.ledger.api import (
        create_ledger,
        create_idempotency_key_for_subsidy,
        create_transaction,
    )
    ledger = create_ledger(
        unit=unit,
        idempotency_key=create_idempotency_key_for_subsidy(subsidy),
    )

    subsidy.ledger = ledger
    if kwargs.get('starting_balance'):
        idpk = idpk_for_transaction(subsidy, kwargs['starting_balance'])
        tx = subsidy.create_transaction(idpk, kwargs['starting_balance'], {})

    subsidy.save()
    return subsidy

def create_subscription_subsidy(
        customer_uuid,
        subscription_plan_uuid,
        unit,
        do_sync=False,
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
    )
    ledger = create_ledger(
        unit=unit,
        idempotency_key=create_idempotency_key_for_subsidy(subsidy),
    )

    subsidy.ledger = ledger
    if kwargs.get('starting_balance'):
        idpk = idpk_for_transaction(subsidy, kwargs['starting_balance'])
        tx = subsidy.create_transaction(idpk, kwargs['starting_balance'], {})

        if do_sync and subscription_plan_uuid:
            sync_subscription(subsidy, subscription_plan_uuid)

    subsidy.save()
    return subsidy


def sync_subscription(subsidy, **metadata):
    licenses_by_type = subsidy.subscription_client.get_plan_metadata().get('licenses')
    total_licenses = licenses_by_type.get('total')

    current_balance = subsidy.current_balance()

    if current_balance > 0:
        # fine, zero out the ledger
        # TODO: sync one license uuid per transaction record.
        idpk = idpk_for_transaction(subsidy, current_balance * -1, **metadata)
        subsidy.create_transaction(idpk, current_balance * -1, {})

    # ...but there's a lot more to sync'ing

    if subsidy.current_balance() != 0:
        raise Exception('ledger still not zerod')


def idpk_for_transaction(subsidy, quantity, **metadata):
    """
    TODO: Hands are being waved here.  In a production environment,
    the caller is responsible for providing enough metadata as a parameter
    here to ensure that the corresponding operation is truly idempotent.
    """
    from enterprise_access.apps.ledger.api import create_idempotency_key_for_transaction
    idpk_data = {k: metadata[k] for k in TRANSACTION_METADATA_KEYS if k in metadata}
    if not idpk_data:
        idpk_data = {
            'default_identifier': uuid4(),
        }
    return create_idempotency_key_for_transaction(
        subsidy,
        quantity,
        **idpk_data,
    )
