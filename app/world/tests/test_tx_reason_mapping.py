"""Every ledger reason must reach the trace, or a whole mechanic goes dark.

MEASURED, iteration 4 of the autonomous loop. Six `trade` calls succeeded -
timber for 30, 45 and 75 sim, `"traded": true` in every result - and the trace
recorded **zero** trade transactions. The settlements gate read 0 on a town
that had just settled six bargains.

Cause: `TX_REASON_TO_SCHEMA_TYPE` is a hand-maintained dict from
`TransactionReason` to the trace schema's constrained `tx_type` column. Adding
`TransactionReason.TRADE` to the ledger did not add it here, so every trade
either raised a KeyError inside the trace writer or was quietly dropped, and
the run's most interesting economic event became invisible.

This is the same class as "every registered tool has a cost row", which this
repo already guards. A mapping that must be updated by hand, and that nothing
checks, will be forgotten - and the failure is silent, which is the worst
possible shape: the mechanic works, the agents use it, and the record says it
never happened.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from world.ledger.models import TransactionReason
from world.wiring import TX_REASON_TO_SCHEMA_TYPE


def schema_allowed_tx_types() -> set[str]:
    """The values the trace store's CHECK constraint will actually accept."""
    sql = (pathlib.Path("supabase") / "sqlite_schema.sql").read_text(encoding="utf-8")
    match = re.search(r"check \(tx_type in \(([^)]*)\)\)", sql)
    assert match, "could not find the tx_type CHECK constraint"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


@pytest.mark.parametrize("reason", list(TransactionReason), ids=lambda r: r.value)
def test_every_ledger_reason_has_a_trace_mapping(reason: TransactionReason) -> None:
    """Add a reason to the ledger, add it here. The test is the reminder."""
    assert reason in TX_REASON_TO_SCHEMA_TYPE, (
        f"TransactionReason.{reason.name} has no trace mapping, so every "
        "transaction carrying it is lost from the record"
    )


@pytest.mark.parametrize("reason", list(TransactionReason), ids=lambda r: r.value)
def test_every_mapping_is_a_value_the_schema_accepts(reason: TransactionReason) -> None:
    """A mapping to a value the CHECK constraint rejects is worse than no
    mapping: it fails at write time, in production, on a run already
    spending money."""
    mapped = TX_REASON_TO_SCHEMA_TYPE.get(reason)
    assert mapped in schema_allowed_tx_types(), (
        f"{reason.name} maps to {mapped!r}, which the trace schema's CHECK "
        f"constraint rejects (allowed: {sorted(schema_allowed_tx_types())})"
    )


def test_a_trade_is_distinguishable_from_a_plain_transfer() -> None:
    """The point of giving `trade` its own reason. A settlement - a price
    agreed in speech and then actually paid - is the clearest evidence that
    talking changed the world, and it cannot be counted if it looks
    identical to somebody handing over money for nothing."""
    assert (
        TX_REASON_TO_SCHEMA_TYPE[TransactionReason.TRADE]
        != TX_REASON_TO_SCHEMA_TYPE[TransactionReason.TRANSFER]
    )
