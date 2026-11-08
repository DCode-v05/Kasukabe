"""Buying bread and handing somebody money look identical in the record.

MEASURED on final1: two successful `sell` calls, and the trace holds only
`burn` and `transfer`. Both `buy` and `sell` record
`TransactionReason.TRANSFER`, which maps to the schema's `"transfer"` -
the same value `transfer` itself uses, which is the tool for giving money
away for nothing.

THIS IS THE DEFECT THAT ALREADY BIT THIS PROJECT ONCE. Six real `trade`
calls returned `"traded": true` and the settlements gate read zero,
because `TransactionReason.TRADE` had no trace mapping. The comment
written when that was fixed says why it matters, and applies word for
word here:

    Its own type, not folded into "transfer". A settlement - a price
    agreed out loud and then actually paid - is the clearest evidence
    there is that talking changed the world, and it cannot be counted if
    it looks identical to somebody handing over money for nothing.

`buy` and `sell` ARE that, and they were folded into transfer.

WHAT IT COSTS. G4 counts a settlement as a bargain struck in speech and
then paid. A bargain settled at the bakery counter - which is where most
bargains in a town with shops WOULD be settled - has been scoring zero
for the whole series, and the run reports have been reading that as "the
town does not trade" when what it means is "we cannot see it".

THE BAR DOES NOT MOVE. Build Spec v2 section 10.4 forbids changing a
threshold after seeing a result, and G4 stays at one settlement. What
changes is a detector that could not see the thing it was pointed at -
the same correction, for the same reason, as the one made for `trade`.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from world.ledger.models import Currency, TransactionReason
from world.wiring import TX_REASON_TO_SCHEMA_TYPE

SCHEMA = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "sqlite_schema.sql"


def allowed_tx_types() -> set[str]:
    """The values the trace store's CHECK constraint will accept.

    Read from the schema rather than duplicated, because the last time
    these two lists disagreed the tool worked, the agents used it, and
    every row was rejected in silence.
    """
    text = SCHEMA.read_text(encoding="utf-8")
    match = re.search(r"check \(tx_type in \(([^)]*)\)\)", text)
    assert match, "the tx_type CHECK constraint has moved or been removed"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_a_purchase_has_its_own_reason() -> None:
    assert hasattr(TransactionReason, "PURCHASE")


def test_a_sale_has_its_own_reason() -> None:
    assert hasattr(TransactionReason, "SALE")


@pytest.mark.parametrize("name", ["PURCHASE", "SALE"])
def test_the_new_reasons_reach_the_trace(name: str) -> None:
    """A reason with no mapping raises a KeyError in the trace writer, or
    worse, is caught and dropped."""
    reason = getattr(TransactionReason, name)
    assert reason in TX_REASON_TO_SCHEMA_TYPE


@pytest.mark.parametrize("name", ["PURCHASE", "SALE"])
def test_they_are_not_indistinguishable_from_a_gift(name: str) -> None:
    """The whole point. If these map to "transfer" the record cannot tell
    a bargain from a handout, which is the state this file exists to
    end."""
    reason = getattr(TransactionReason, name)
    assert TX_REASON_TO_SCHEMA_TYPE[reason] != "transfer"


@pytest.mark.parametrize("name", ["PURCHASE", "SALE"])
def test_the_schema_will_accept_them(name: str) -> None:
    """Both halves, or the mapping is a lie the CHECK constraint rejects
    row by row - exactly how six real trades vanished."""
    reason = getattr(TransactionReason, name)
    assert TX_REASON_TO_SCHEMA_TYPE[reason] in allowed_tx_types()


def test_every_reason_still_maps_to_something_the_schema_accepts() -> None:
    """The guard that generalises it: adding a reason to the ledger
    without a trace mapping the schema accepts must fail here rather than
    in production, silently."""
    for reason in TransactionReason:
        assert reason in TX_REASON_TO_SCHEMA_TYPE, f"{reason} has no trace mapping"
        assert TX_REASON_TO_SCHEMA_TYPE[reason] in allowed_tx_types(), (
            f"{reason} maps to {TX_REASON_TO_SCHEMA_TYPE[reason]!r}, which the "
            "schema's CHECK constraint will refuse"
        )


def test_a_plain_transfer_is_still_a_transfer() -> None:
    """Handing somebody money for nothing is a real thing residents do,
    and it must not be recorded as a purchase."""
    assert TX_REASON_TO_SCHEMA_TYPE[TransactionReason.TRANSFER] == "transfer"


# -- The gate reads them ----------------------------------------------


def test_the_settlement_detector_counts_a_purchase() -> None:
    """A bargain settled at the bakery counter is a bargain settled. G4
    is defined as a price agreed in speech and then moved by a tool call,
    and `buy` is one of the three tools that can move it.

    The BAR is unchanged at one settlement - section 10.4 forbids moving
    it, and this is a detector that could not see what it was pointed at,
    the same correction already made for `trade`.
    """
    import loop_report

    assert "purchase" in loop_report.SETTLING_TX_TYPES
    assert "sale" in loop_report.SETTLING_TX_TYPES
    assert "trade" in loop_report.SETTLING_TX_TYPES
    assert "transfer" not in loop_report.SETTLING_TX_TYPES, (
        "a plain transfer is a gift, and counting it would make G4 pass on "
        "somebody handing over money for nothing"
    )


# -- Which shop took the money ----------------------------------------
#
# The other half of the same blindness. A purchase moves sim from an
# agent to a BUSINESS, and build_transaction_row writes an agent id only
# when the account is a known agent - so the business leg is null and the
# row does not say which shop was paid. `from_agent_id is not null and
# to_agent_id is not null` then excludes the row from the settlement
# query entirely, so widening the type list alone would have changed
# nothing at all.
#
# The row now carries the non-agent account it moved to or from. Not a
# second agent column: a business is not an agent, and conflating them is
# how the pool columns went wrong once already.


def test_the_row_says_which_account_was_not_an_agent() -> None:
    from world.ledger.core import Ledger
    from world.ledger.models import Transaction
    from world.wiring import build_transaction_row

    ledger = Ledger()
    tx = Transaction(
        transaction_id="t1",
        from_account="n07",
        to_account="b_bakery",
        amount=45,
        currency=Currency.SIM,
        reason=TransactionReason.PURCHASE,
        tick=12,
    )
    row = build_transaction_row(ledger, tx, {"n07"})
    assert row["from_agent_id"] == "n07"
    assert row["to_agent_id"] is None, "a business is not an agent"
    assert row["counterparty_id"] == "b_bakery"


def test_an_agent_to_agent_row_has_no_counterparty() -> None:
    """Both sides are already named. A counterparty here would be a
    duplicate that could disagree with the columns beside it."""
    from world.ledger.core import Ledger
    from world.ledger.models import Transaction
    from world.wiring import build_transaction_row

    tx = Transaction(
        transaction_id="t2",
        from_account="n07",
        to_account="n01",
        amount=45,
        currency=Currency.SIM,
        reason=TransactionReason.TRADE,
        tick=12,
    )
    row = build_transaction_row(Ledger(), tx, {"n07", "n01"})
    assert row["counterparty_id"] is None


def test_the_schema_has_the_column() -> None:
    text = SCHEMA.read_text(encoding="utf-8")
    assert "counterparty_id" in text, (
        "the row carries a key the table has no column for, which sqlite "
        "rejects row by row - exactly how six real trades vanished"
    )
