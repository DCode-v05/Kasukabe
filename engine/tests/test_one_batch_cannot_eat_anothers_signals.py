"""A finished batch deleted every claimed row in the table, not its own.

The learning-cadence spec put this first in its plan and was explicit
about the ordering:

    "delete_batch ends the cross-batch deletion. This is the one change
     that must land BEFORE the cadence change, not with it."

The cadence change landed and this did not. `SimLearningGate` now runs a
batch every 18 waking ticks (`config.DEFAULT_LEARN_AWAKE_INTERVAL_TICKS`)
and `trigger_extra()` runs another at end-of-run - and run 25's shutdown
fired the drain twice. So the window the spec called survivable at four
batches a day was live at roughly ten per run, with this in the cleanup
phase:

    DELETE FROM learning_signals WHERE processed = 1

`processed = 1` means "claimed by SOME batch". A drain finishing while a
waking batch is mid-encode therefore deletes the waking batch's rows
before they are written. No error, no log line, and the signals are gone -
which for a correction means the resident is never taught the thing the
world already told it.

`mark_processed` and `unmark_processed` have both been batch-scoped since
they were written. Only the delete was not, and the delete is the
irreversible one.
"""

from __future__ import annotations

import pytest

from src.learning_centre.signals import SignalStore


class FakeDb:
    """Enough of the pool to run the two statements under test.

    A list of rows, not a mock: the thing being tested is which rows a
    WHERE clause selects, and a mock that records calls would let a wrong
    clause pass as long as it was called.
    """

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.commits = 0

    async def fetchval(self, sql: str, params: tuple = ()):  # noqa: D102
        return len(self._matching(sql, params))

    async def execute(self, sql: str, params: tuple = ()):  # noqa: D102
        if sql.strip().upper().startswith("DELETE"):
            doomed = {id(r) for r in self._matching(sql, params)}
            self.rows = [r for r in self.rows if id(r) not in doomed]

    async def commit(self) -> None:  # noqa: D102
        self.commits += 1

    def _matching(self, sql: str, params: tuple) -> list[dict]:
        if "batch_id = ?" in sql:
            return [r for r in self.rows if r.get("batch_id") == params[0]]
        if "processed = 1" in sql:
            return [r for r in self.rows if r.get("processed") == 1]
        return []


def _two_batches_in_flight() -> FakeDb:
    return FakeDb([
        {"id": 1, "processed": 1, "batch_id": "waking", "content": "harvest needs farmland"},
        {"id": 2, "processed": 1, "batch_id": "waking", "content": "Bram owes 260 sim"},
        {"id": 3, "processed": 1, "batch_id": "drain", "content": "bread sells at 50"},
        {"id": 4, "processed": 0, "batch_id": None, "content": "written mid-batch"},
    ])


@pytest.mark.asyncio
async def test_a_batch_deletes_only_its_own_rows():
    db = _two_batches_in_flight()
    store = SignalStore(db)

    deleted = await store.delete_batch("drain")

    assert deleted == 1
    remaining = {r["id"] for r in db.rows}
    assert remaining == {1, 2, 4}, "the drain took the waking batch's rows with it"


@pytest.mark.asyncio
async def test_the_unclaimed_row_is_never_touched():
    """A signal written while the agents were running has processed = 0
    and belongs to the NEXT batch. That is the snapshot cut, and it is the
    one part of this the spec found already correct."""
    db = _two_batches_in_flight()

    await SignalStore(db).delete_batch("waking")

    assert any(r["id"] == 4 for r in db.rows)


@pytest.mark.asyncio
async def test_a_lost_batch_id_deletes_nothing():
    """Not a fallback to the flag. A caller that lost its batch id must
    not take the whole table with it - that is the failure this replaces,
    reintroduced through the back door."""
    db = _two_batches_in_flight()
    before = len(db.rows)

    deleted = await SignalStore(db).delete_batch("")

    assert deleted == 0
    assert len(db.rows) == before


@pytest.mark.asyncio
async def test_the_old_call_really_did_take_everything():
    """The defect, demonstrated rather than described - so this file
    proves the fix was needed and not just that the new method works."""
    db = _two_batches_in_flight()

    await SignalStore(db).delete_processed()

    assert {r["id"] for r in db.rows} == {4}, (
        "delete_processed is supposed to be the indiscriminate one"
    )


@pytest.mark.asyncio
async def test_the_pipeline_calls_the_batch_scoped_one():
    """Parsed, not grepped: a source-string check passes when the call is
    commented out. Same defect as the `/world/link` test that asserted a
    route's source text while the route raised NameError on every
    request."""
    import ast
    import inspect

    from src.learning_centre import pipeline

    tree = ast.parse(inspect.getsource(pipeline))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "delete_batch" in called, "the pipeline still deletes by the flag"
    assert "delete_processed" not in called, (
        "delete_processed is still called somewhere in the pipeline"
    )
