"""ECON-28: reading one resident's recent burn must not cost the length of
the whole town's transaction log.

THE DEFECT, as filed (docs/review/12-economy-and-life.md, ECON-28, P3):
`gather_wallet_and_burn` summed `recent_burn_usd_cents` by filtering
`ledger.transactions` from end to end on every call. That call happens
once per resident per tick - `GenesisRunner.wallet_line`
(world/run_genesis.py:4742) builds the volatile line every resident reads
every turn out of it - so the work per tick was agents x transactions, on
a log that only ever grows. At the filed figures (250 residents, 144 ticks
a game-day) the scan gets steadily more expensive the longer the town has
already lived, which is exactly the wrong direction for a long run: the
cost of telling somebody their balance rises with how much history the
town has, not with how much they burned.

THE FIX CHOSEN, and why it is not the one the finding suggested. The
finding proposed a per-agent ring buffer in the runner, fed from
`economy.wallet.debited` events. That needs run_genesis.py, and a ring
buffer needs a length picked in advance - too short and a window longer
than the buffer reads low, with no error to say so. `cost_disclosure` is
handed the ledger already, so the index lives here instead: `burn_index_for`
keeps a per-ledger, per-agent list of tick-stamped burn amounts with a
running total, advanced by only the transactions appended since the last
call, and answers any inclusive window with two binary searches. Same
asymptotics as the ring buffer, no horizon, and no file outside this
module changes - `wallet_line` gets the improvement without being touched.

WHAT THIS FILE PINS.

1. The number is still right. `test_totals_match_a_full_scan_of_the_log`
   computes the same window three different ways against a real `Ledger`
   driven with real `burn_for_inference` calls, including the minor case
   (`on_behalf_of`), and compares against a literal end-to-end filter of
   `ledger.transactions` - the very code that was replaced. A faster
   wrong answer is worse than a slow right one.

2. The work is actually bounded. `CountingTransactionLog` counts every
   row the implementation touches, through both iteration and indexing,
   so the assertion is on real work done rather than on a wall clock that
   would be flaky on a loaded machine. The second reading of a 600-row log
   must touch a handful of rows, not 600.

3. It is WIRED. The counting tests drive `gather_wallet_and_burn` itself,
   not `burn_index_for`, so dropping the index back to a filter inside
   that function fails them; and
   `test_the_runners_volatile_line_reads_through_the_index` calls the real
   `GenesisRunner.wallet_line`, the actual once-per-resident-per-tick
   entry point the finding names, and asserts the same bound there. This
   codebase's commonest defect by far is a complete, tested mechanism
   nothing calls, and an index nothing reads would be that defect wearing
   a performance fix's clothes.

4. It survives a log that is not append-only.
   `test_a_replaced_transaction_log_is_re_derived` reproduces
   run_genesis.py:1830, which swaps `ledger.transactions` for a freshly
   validated list when a saved run is restored, and
   `test_a_shortened_log_is_re_derived` reproduces a rollback. A cached
   index that trusted its cursor would answer with burns that no longer
   exist - a stale number on a resident's own runway is the same class of
   lie ECON-17 was.
"""

from __future__ import annotations

from functools import partial
from types import SimpleNamespace
from typing import Any

import pytest

from world.ledger.core import Ledger
from world.ledger.models import Currency, TransactionReason
from world.ledger.money import TOTAL_USD_CENTS
from world.tools.cost_disclosure import (
    DEFAULT_BURN_WINDOW_TICKS,
    burn_index_for,
    gather_wallet_and_burn,
)


class CountingTransactionLog(list):
    """A real list that records how many rows anybody looked at.

    Both access shapes are counted: `__iter__` (what the pre-fix
    `for transaction in ledger.transactions` filter used) and integer
    `__getitem__` (what the index's incremental walk uses), so neither
    implementation can do its scanning through an uncounted door.
    Slicing is deliberately NOT counted per row - if the implementation
    ever copies the tail with a slice, that shows up as suspiciously
    cheap here and the correctness tests above still hold it honest.
    """

    def __init__(self, rows: list[Any] | None = None) -> None:
        super().__init__(rows or [])
        self.rows_touched = 0

    def __iter__(self):  # type: ignore[override]
        for row in list.__iter__(self):
            self.rows_touched += 1
            yield row

    def __getitem__(self, index):  # type: ignore[override]
        if isinstance(index, int):
            self.rows_touched += 1
        return list.__getitem__(self, index)


def reference_window_total(ledger: Ledger, agent_id: str, since_tick: int, tick: int) -> int:
    """The exact expression ECON-28 replaced, kept here as the oracle."""
    return sum(
        transaction.amount
        for transaction in list.__iter__(ledger.transactions)
        if transaction.currency is Currency.USD
        and transaction.reason is TransactionReason.INFERENCE_BURN
        and since_tick <= transaction.tick <= tick
        and (transaction.on_behalf_of or transaction.from_account) == agent_id
    )


async def burn(ledger: Ledger, agent_id: str, cents: int, tick: int) -> None:
    await ledger.burn_for_inference(agent_id, cents, tick)


async def funded_ledger(agent_ids: list[str], agent_usd_cents: int = 10_000) -> Ledger:
    """A ledger in a state the real $1000 invariant accepts.
    `burn_for_inference` moves USD through `Ledger.move`, which enforces
    it after every transaction, so `genesis()` is the only honest way to
    seed one (the idiom test_cost_disclosure.py itself uses).
    """
    ledger = Ledger()
    await ledger.genesis(
        agent_ids,
        agent_usd_cents=agent_usd_cents,
        treasury_usd_cents=TOTAL_USD_CENTS - agent_usd_cents * len(agent_ids),
    )
    return ledger


@pytest.mark.asyncio
async def test_totals_match_a_full_scan_of_the_log() -> None:
    ledger = await funded_ledger(["adult", "other", "child"])
    # genesis funds every agent uniformly; the minor link is set on the
    # Account afterward, matching test_ledger_parent_debit.py's idiom.
    ledger.accounts["child"].is_minor = True
    ledger.accounts["child"].parent_id = "adult"

    for tick in range(0, 300):
        await burn(ledger, "adult", 3, tick)
        await burn(ledger, "other", 7, tick)
        if tick % 5 == 0:
            await burn(ledger, "child", 11, tick)

    tick = 299
    since_tick = max(0, tick - DEFAULT_BURN_WINDOW_TICKS + 1)

    for agent_id in ("adult", "other", "child"):
        snapshot = gather_wallet_and_burn(ledger, agent_id, tick)
        expected = reference_window_total(ledger, agent_id, since_tick, tick)
        assert snapshot.recent_burn_usd_cents == expected, agent_id
        # Non-trivial: a test where every expected value is 0 proves nothing.
        assert expected > 0

    # A narrow window, off the end of the log, and one that starts before
    # tick 0 - the three places a binary search goes wrong.
    for window in (1, 2, 7, 144, 1_000):
        snapshot = gather_wallet_and_burn(ledger, "adult", tick, window)
        assert snapshot.recent_burn_usd_cents == reference_window_total(
            ledger, "adult", max(0, tick - window + 1), tick
        )
    early = gather_wallet_and_burn(ledger, "adult", 3, 2)
    assert early.recent_burn_usd_cents == reference_window_total(ledger, "adult", 2, 3)
    assert gather_wallet_and_burn(ledger, "adult", 0, 1).recent_burn_usd_cents == 3


@pytest.mark.asyncio
async def test_a_second_reading_does_not_rescan_the_log() -> None:
    ledger = await funded_ledger(["a1", "a2"])
    for tick in range(0, 300):
        await burn(ledger, "a1", 2, tick)
        await burn(ledger, "a2", 2, tick)

    log = CountingTransactionLog(list.__iter__(ledger.transactions))
    ledger.transactions = log
    assert len(log) >= 600

    gather_wallet_and_burn(ledger, "a1", 299)  # first call builds the index
    log.rows_touched = 0

    # A whole tick of readings over an unchanged log: 50 residents asking
    # what they burned must not cost 50 x 600 rows.
    for _ in range(50):
        gather_wallet_and_burn(ledger, "a1", 299)
        gather_wallet_and_burn(ledger, "a2", 299)
    assert log.rows_touched <= 200, (
        f"{log.rows_touched} rows read for 100 wallet lines over an unchanged "
        f"{len(log)}-row log"
    )

    # And a tick that DID add transactions pays for those, not for history.
    await burn(ledger, "a1", 2, 300)
    await burn(ledger, "a2", 2, 300)
    log.rows_touched = 0
    snapshot = gather_wallet_and_burn(ledger, "a1", 300)
    assert log.rows_touched <= 10, f"{log.rows_touched} rows read for 2 new transactions"
    assert snapshot.recent_burn_usd_cents == reference_window_total(
        ledger, "a1", max(0, 300 - DEFAULT_BURN_WINDOW_TICKS + 1), 300
    )


@pytest.mark.asyncio
async def test_the_runners_volatile_line_reads_through_the_index() -> None:
    """The real entry point: `GenesisRunner.wallet_line`, unbound, over the
    two attributes it actually reads. This is the call the finding counts
    (once per resident per tick), and the reason the fix had to live where
    every caller of `gather_wallet_and_burn` inherits it.
    """
    from world.run_genesis import GenesisRunner

    ledger = await funded_ledger(["a1"])
    for tick in range(0, 400):
        await burn(ledger, "a1", 2, tick)

    log = CountingTransactionLog(list.__iter__(ledger.transactions))
    ledger.transactions = log
    # ECON-20 gave `wallet_line` a second half - today's upkeep and head
    # tax, via `todays_obligations`. The REAL method is bound here rather
    # than stubbed: with `world_state=None` it returns (0, 0, None) by its
    # own "never guesses" rule, so this test still measures exactly what
    # it claims (ledger rows read per volatile line) while keeping the
    # production code in the path. A lambda would have hidden a future
    # `todays_obligations` that walks the transaction log.
    runner = SimpleNamespace(
        ledger=ledger, budget_hit_last_turn={}, world_state=None
    )
    runner.todays_obligations = partial(GenesisRunner.todays_obligations, runner)

    first = GenesisRunner.wallet_line(runner, "a1", 399)
    assert first is not None
    assert first["usd_wallet_cents"] == 10_000 - 800

    log.rows_touched = 0
    repeated = [GenesisRunner.wallet_line(runner, "a1", 399) for _ in range(30)]
    assert log.rows_touched <= 100, (
        f"{log.rows_touched} rows read for 30 volatile lines over a {len(log)}-row log"
    )
    assert all(line == first for line in repeated)
    # The line is still a real reading, not a cached zero.
    assert first["last_turn_usd_cents"] > 0
    assert first["runway_game_days"] is not None


@pytest.mark.asyncio
async def test_a_replaced_transaction_log_is_re_derived() -> None:
    """run_genesis.py:1830 replaces `ledger.transactions` wholesale when a
    saved run is restored. An index that kept its cursor would report
    burns from a log that is no longer there.
    """
    ledger = await funded_ledger(["a1"])
    for tick in range(0, 50):
        await burn(ledger, "a1", 4, tick)
    assert gather_wallet_and_burn(ledger, "a1", 49).recent_burn_usd_cents == 200

    restored = await funded_ledger(["a1"])
    for tick in range(0, 10):
        await burn(restored, "a1", 4, tick)
    # The restore shape: a different list object holding different rows.
    ledger.transactions = list(list.__iter__(restored.transactions))

    assert gather_wallet_and_burn(ledger, "a1", 49).recent_burn_usd_cents == 40


@pytest.mark.asyncio
async def test_a_shortened_log_is_re_derived() -> None:
    """A rolled-back call shortens the log in place. The index must notice
    the rows it consumed are gone rather than keep counting them.
    """
    ledger = await funded_ledger(["a1"])
    for tick in range(0, 20):
        await burn(ledger, "a1", 5, tick)
    assert gather_wallet_and_burn(ledger, "a1", 19).recent_burn_usd_cents == 100

    del ledger.transactions[10:]
    shortened = reference_window_total(ledger, "a1", 0, 19)
    assert shortened < 100
    assert gather_wallet_and_burn(ledger, "a1", 19).recent_burn_usd_cents == shortened

    # Rolled back and re-grown to the same length with different rows: the
    # length matches what was consumed, so only row identity catches it.
    ledger2 = await funded_ledger(["a1"])
    for tick in range(0, 6):
        await burn(ledger2, "a1", 5, tick)
    consumed = len(ledger2.transactions)
    assert gather_wallet_and_burn(ledger2, "a1", 5).recent_burn_usd_cents == 30
    del ledger2.transactions[consumed - 1 :]
    await burn(ledger2, "a1", 50, 5)
    assert len(ledger2.transactions) == consumed
    assert gather_wallet_and_burn(ledger2, "a1", 5).recent_burn_usd_cents == (
        reference_window_total(ledger2, "a1", 0, 5)
    )


@pytest.mark.asyncio
async def test_out_of_order_ticks_are_still_summed_correctly() -> None:
    """A binary search over an unsorted list answers a plausible wrong
    number. The index notices and walks the agent's own rows instead.
    """
    ledger = await funded_ledger(["a1"])
    for tick in (9, 3, 7, 1, 5):
        await burn(ledger, "a1", 6, tick)

    for window in (1, 3, 5, 10):
        snapshot = gather_wallet_and_burn(ledger, "a1", 9, window)
        assert snapshot.recent_burn_usd_cents == reference_window_total(
            ledger, "a1", max(0, 9 - window + 1), 9
        )


@pytest.mark.asyncio
async def test_two_ledgers_do_not_share_an_index() -> None:
    first = await funded_ledger(["a1"])
    await burn(first, "a1", 9, 0)

    second = await funded_ledger(["a1"])

    assert gather_wallet_and_burn(first, "a1", 0).recent_burn_usd_cents == 9
    assert gather_wallet_and_burn(second, "a1", 0).recent_burn_usd_cents == 0
    assert burn_index_for(first) is not burn_index_for(second)
