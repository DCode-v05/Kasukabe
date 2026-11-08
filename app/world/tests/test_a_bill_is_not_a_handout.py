"""The trace can tell a bill from a gift.

`ECON-03`. Upkeep, action costs, refunds, wedding and pharmacy fees,
revival payments and prayer donations were every one of them booked as
`TransactionReason.TRANSFER` with a memo. Only head tax had its own
reason.

The consequence was not a matter of taste. `build_transaction_row`
writes `reason = transaction.reason.value`, and
`run_report._transactions` queries `where reason = 'daily_upkeep'` - a
string no row ever held, because upkeep was written as `transfer`. So
the report asked the database for the upkeep and got an empty list back,
and run 23 printed "no upkeep charged" over ninety sim per household per
day of real money. GDP-per-inference-dollar, velocity and Gini cannot be
computed from a table where a bill and a handout look alike.

WHY THE TRACE SCHEMA WAS NOT WIDENED. `tx_type`'s CHECK is a closed
vocabulary and SQLite cannot ALTER a constraint - widening it would leave
every EXISTING database rejecting the new values, which is precisely the
"213 transaction rows dropped in six ticks" failure that
`SqliteClient.ensure_schema` was written to end. Nothing is lost by
keeping `tx_type` coarse, because the fine grain lives in the free-text
`reason` column beside it, and that is the column the report reads.
"""
from __future__ import annotations

import inspect

import pytest

from world.ledger.models import TransactionReason
from world.tools.economy_bridge import LedgerEconomyPort
from world.wiring import TX_REASON_TO_SCHEMA_TYPE

# The seven the finding names.
NEW_REASONS = (
    "UPKEEP", "ACTION_COST", "REFUND", "FEE",
    "DONATION", "REVIVAL", "ENERGY_EXCHANGE",
)


@pytest.mark.parametrize("name", NEW_REASONS)
def test_the_vocabulary_has_the_word(name) -> None:
    assert hasattr(TransactionReason, name), (
        f"ECON-03: there is still no {name} reason, so that movement is "
        "recorded as an ordinary transfer"
    )


def test_every_reason_can_reach_the_trace() -> None:
    """A reason with no schema mapping raises `KeyError` in
    `build_transaction_row` and loses the row - which is worse than the
    coarse grouping it was added to escape."""
    missing = [r.name for r in TransactionReason if r not in TX_REASON_TO_SCHEMA_TYPE]
    assert not missing, (
        f"ECON-03: {missing} have no tx_type mapping, so writing one raises "
        "KeyError and the transaction never reaches the trace at all"
    )


def test_the_schema_vocabulary_was_not_widened() -> None:
    """Deliberate. SQLite cannot ALTER a CHECK, so a new `tx_type` value
    would be rejected by every database that already exists - silently
    dropping rows, which is the failure ensure_schema documents."""
    from pathlib import Path

    schema = (
        Path(__file__).resolve().parents[2] / "supabase" / "sqlite_schema.sql"
    ).read_text(encoding="utf-8")
    allowed = schema.split("check (tx_type in (", 1)[1].split("))", 1)[0]
    for value in set(TX_REASON_TO_SCHEMA_TYPE.values()):
        assert f"'{value}'" in allowed, (
            f"ECON-03: tx_type {value!r} is not in the schema's CHECK, so "
            "every row carrying it is rejected - on new databases by the "
            "constraint, on old ones silently"
        )


# ── the mapping the finding asks for ────────────────────────────────────

def test_upkeep_is_booked_as_upkeep() -> None:
    """The exact string the report queries. `run_report._transactions`
    asks `where reason = 'daily_upkeep'`."""
    assert LedgerEconomyPort.reason_for("daily_upkeep") is TransactionReason.UPKEEP
    assert TransactionReason.UPKEEP.value == "daily_upkeep", (
        "ECON-03: the enum value and the report's query string differ, so "
        "the report still asks for a row that does not exist"
    )


def test_the_report_asks_for_a_string_the_writer_writes() -> None:
    """The whole finding in one assertion. These two were written by
    different people at different times and never compared."""
    from world import run_report
    from world.wiring import build_transaction_row  # noqa: F401

    report_source = inspect.getsource(run_report)
    written = {r.value for r in TransactionReason}
    asked = set()
    for line in report_source.splitlines():
        if "_transactions(" in line and '"' in line:
            asked.add(line.split('"')[1])
    unknown = {a for a in asked if a not in written}
    assert not unknown, (
        f"ECON-03: the report queries transactions.reason for {unknown}, "
        "and no TransactionReason has that value - so the query matches "
        "nothing and the section prints a comfortable zero"
    )


@pytest.mark.parametrize(
    "life_reason,expected",
    [
        ("daily_upkeep", TransactionReason.UPKEEP),
        ("revival_payment", TransactionReason.REVIVAL),
        ("baby_blessing", TransactionReason.BLESSING),
        ("temple_wedding_fee", TransactionReason.FEE),
        ("pharmacy_treatment", TransactionReason.FEE),
    ],
)
def test_each_life_fee_books_its_own_reason(life_reason, expected) -> None:
    assert LedgerEconomyPort.reason_for(life_reason) is expected


def test_an_unknown_reason_is_not_guessed_into_a_category() -> None:
    """A movement filed under the WRONG heading is worse for the research
    than one filed under none, and the memo still carries the string."""
    assert LedgerEconomyPort.reason_for("something_nobody_mapped") is (
        TransactionReason.TRANSFER
    )


# ── the two ledger-side sites ───────────────────────────────────────────

def test_an_action_cost_says_it_is_one() -> None:
    from world.ledger import action_costs

    source = inspect.getsource(action_costs)
    assert "reason=TransactionReason.ACTION_COST" in source, (
        "ECON-03: tool wear is still booked as a transfer, so it cannot be "
        "separated from a resident giving money away"
    )


def test_a_refund_says_it_is_one() -> None:
    from world.ledger import action_costs

    source = inspect.getsource(action_costs)
    assert "reason=TransactionReason.REFUND" in source, (
        "ECON-03: a reversal is booked as a transfer, so a run that "
        "refunded heavily reads as a generous treasury"
    )


def test_the_bridge_no_longer_flattens_everything() -> None:
    from world.tools import economy_bridge

    source = inspect.getsource(economy_bridge.LedgerEconomyPort.debit_sim)
    assert "reason=self.reason_for(reason)" in source, (
        "ECON-03: LedgerEconomyPort.debit_sim still hard-codes TRANSFER, so "
        "every life-side fee reaches the trace as a gift"
    )
