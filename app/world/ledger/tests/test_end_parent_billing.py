"""Ledger.end_parent_billing: the wallet half of coming of age.

DESIGN.md section 4's parent-debit rule is scoped to minors; until this
existed nothing ever took an account back out of that scope, so a parent
kept funding a grown child's cognition for the rest of the run.
"""

from __future__ import annotations

import pytest

from world.ledger.core import Ledger
from world.ledger.errors import CurrencyNotSupportedError


def ledger_with_family() -> Ledger:
    ledger = Ledger()
    ledger.open_agent("mum", starting_usd_cents=100_000)
    ledger.open_agent("kid", is_minor=True, parent_id="mum")
    return ledger


def test_a_minors_spends_resolve_to_the_parent():
    ledger = ledger_with_family()
    assert ledger.resolve_usd_payer("kid") == "mum"


def test_coming_of_age_moves_the_bill_to_the_new_adult():
    ledger = ledger_with_family()
    ledger.end_parent_billing("kid")
    assert ledger.resolve_usd_payer("kid") == "kid"
    account = ledger.get_account("kid")
    assert account.is_minor is False
    assert account.parent_id is None


def test_it_is_idempotent():
    """A tick loop that re-sees the same birthday must not blow up."""
    ledger = ledger_with_family()
    ledger.end_parent_billing("kid")
    ledger.end_parent_billing("kid")
    assert ledger.resolve_usd_payer("kid") == "kid"


def test_it_is_a_no_op_on_an_agent_who_never_had_a_sponsor():
    ledger = ledger_with_family()
    ledger.end_parent_billing("mum")
    assert ledger.resolve_usd_payer("mum") == "mum"


def test_it_refuses_a_non_agent_account():
    ledger = ledger_with_family()
    ledger.open_business("grocery", owner_agent_id="mum")
    with pytest.raises(CurrencyNotSupportedError):
        ledger.end_parent_billing("grocery")


def test_it_moves_no_money():
    ledger = ledger_with_family()
    before = ledger.get_account("mum").usd_cents
    ledger.end_parent_billing("kid")
    assert ledger.get_account("mum").usd_cents == before
