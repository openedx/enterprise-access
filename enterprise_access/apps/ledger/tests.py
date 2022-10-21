from django.test import TestCase
import pytest

from enterprise_access.apps.ledger import api
from enterprise_access.apps.ledger.models import Ledger, Transaction, Reversal, UnitChoices
# Create your tests here.

@pytest.mark.django_db
def test_create_ledger_happy_path():
    ledger = api.create_ledger(unit=UnitChoices.USD_CENTS, idempotency_key='my-happy-ledger')
    assert ledger.balance() == 0

    tx_1 = api.create_transaction(ledger, quantity=5000, idempotency_key='tx-1')
    assert ledger.balance() == 5000

    tx_2 = api.create_transaction(ledger, quantity=5000, idempotency_key='tx-2')
    assert ledger.balance() == 10000

    reversal = api.reverse_full_transaction(tx_2, idempotency_key='reversal-1')
    assert ledger.balance() == 5000

    other_ledger = api.create_ledger(unit=UnitChoices.USD_CENTS, idempotency_key='my-happy-ledger')
    assert ledger == other_ledger


@pytest.mark.django_db
def test_no_negative_balance():
    ledger = api.create_ledger(unit=UnitChoices.USD_CENTS, idempotency_key='my-other-ledger')
    assert ledger.balance() == 0

    with pytest.raises(Exception, match="d'oh!"):
        tx_1 = api.create_transaction(ledger, quantity=-1, idempotency_key='tx-1')

    tx_2 = api.create_transaction(ledger, quantity=999, idempotency_key='tx-2')
    with pytest.raises(Exception, match="d'oh!"):
        tx_3 = api.create_transaction(ledger, quantity=-1000, idempotency_key='tx-3')


@pytest.mark.django_db
def test_multiple_reversals():
    ledger = api.create_ledger(unit=UnitChoices.USD_CENTS, idempotency_key='my-other-ledger')
    assert ledger.balance() == 0

    tx_1 = api.create_transaction(ledger, quantity=5000, idempotency_key='tx-1')
    assert ledger.balance() == 5000

    reversal = api.reverse_full_transaction(tx_1, idempotency_key='reversal-1')
    assert ledger.balance() == 0

    second_reversal = api.reverse_full_transaction(tx_1, idempotency_key='reversal-2')
    assert ledger.balance() == 0
    assert reversal == second_reversal

    third_reversal = api.reverse_full_transaction(tx_1, idempotency_key='reversal-1')
    assert ledger.balance() == 0
    assert reversal == third_reversal
