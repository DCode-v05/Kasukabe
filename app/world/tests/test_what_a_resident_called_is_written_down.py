"""Every tool call survives the process that made it.

`EVAL-25`. `world/tools/state.py` said, in a comment, that
"`tool_invocations` is mirrored into the trace store". There was no such
table and no recorder. What existed was a 5,000-entry ring in world state
that dies when the process does.

So every downstream figure came from scraping `session_log.jsonl`: the
task labels (`EVAL-03`), refusal recovery, cost-per-action against
proficiency (C2), verb reach in the wild, and the "41 of 76 verbs never
called" number. A log was being parsed for facts the world itself already
knew and had thrown away.

The comment is the part worth dwelling on. It did not say "TODO" or
"planned"; it described the mirror in the present tense, as a reason the
ring could safely be capped. A reader checking whether the history was
safe would have read that sentence and stopped.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio

from world.run_genesis import GenesisRunner, GenesisRunnerConfig
from world.tools.models import ToolInvocationRecord

APP = Path(__file__).resolve().parents[2]
SCHEMA = APP / "supabase" / "sqlite_schema.sql"


def _invocation(n: int, agent_id: str = "a0001") -> ToolInvocationRecord:
    return ToolInvocationRecord(
        invocation_id=f"inv-{n}",
        tool_name="world_say",
        namespace="public",
        actor_id=agent_id,
        tick=n,
        params={"content": f"line {n}"},
    )


@pytest_asyncio.fixture
async def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await runner.boot()
    await runner.attach_persistence()
    return runner, tmp_path / "trace.db"


def _rows(db: Path) -> list[tuple]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(
            "select invocation_id, agent_id, tick, tool from tool_invocations "
            "order by tick"
        ).fetchall()
    finally:
        conn.close()


def test_the_table_exists(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert "tool_invocations" in tables, (
        "EVAL-25: the world's own comment says invocations are mirrored to "
        "the trace store and there is nowhere to mirror them to"
    )


@pytest.mark.asyncio
async def test_a_call_reaches_the_store(runner) -> None:
    """The finding itself."""
    run, db = runner
    run.world_state.tool_invocations.append(_invocation(1))

    written = await run.drain_tool_invocations()

    assert written == 1
    assert [row[3] for row in _rows(db)] == ["world_say"]


@pytest.mark.asyncio
async def test_nothing_is_written_twice(runner) -> None:
    """The drain runs every tick. A cursor that did not advance would
    write the whole ring again on each one."""
    run, db = runner
    run.world_state.tool_invocations.append(_invocation(1))

    await run.drain_tool_invocations()
    await run.drain_tool_invocations()
    await run.drain_tool_invocations()

    assert len(_rows(db)) == 1


@pytest.mark.asyncio
async def test_later_calls_are_picked_up(runner) -> None:
    run, db = runner
    run.world_state.tool_invocations.append(_invocation(1))
    await run.drain_tool_invocations()
    run.world_state.tool_invocations.append(_invocation(2))
    await run.drain_tool_invocations()

    assert [row[2] for row in _rows(db)] == [1, 2]


@pytest.mark.asyncio
async def test_the_parameters_are_hashed_not_copied(runner) -> None:
    """A `say` carries its whole utterance, and that already lives in
    `utterances`. Two copies of a corpus is two things that can disagree;
    what this table is for is counting and joining."""
    run, db = runner
    run.world_state.tool_invocations.append(_invocation(1))
    await run.drain_tool_invocations()

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        params_hash = conn.execute(
            "select params_hash from tool_invocations"
        ).fetchone()[0]
    finally:
        conn.close()
    assert params_hash and "line 1" not in params_hash


@pytest.mark.asyncio
async def test_the_same_call_hashes_the_same_way(runner) -> None:
    """The point of a hash here is grouping repeat calls."""
    run, db = runner
    run.world_state.tool_invocations.extend([_invocation(1), _invocation(1)])
    run.world_state.tool_invocations[1].invocation_id = "inv-1b"
    await run.drain_tool_invocations()

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        hashes = [r[0] for r in conn.execute("select params_hash from tool_invocations")]
    finally:
        conn.close()
    assert len(set(hashes)) == 1


@pytest.mark.asyncio
async def test_a_broken_store_does_not_end_the_tick(runner, monkeypatch) -> None:
    """Bookkeeping about a turn must not be able to cost the turn."""
    run, _db = runner
    run.world_state.tool_invocations.append(_invocation(1))

    async def boom(*args, **kwargs):
        raise RuntimeError("the store is gone")

    monkeypatch.setattr(run.trace_store, "record_tool_invocations", boom)
    assert await run.drain_tool_invocations() == 0      # must not raise


@pytest.mark.asyncio
async def test_no_trace_store_is_not_an_error(tmp_path) -> None:
    """A run whose recorder would not open still ticks - the rule
    `attach_persistence` already follows, since a missing recorder is a
    smaller loss than a stopped town.

    (An earlier draft assumed a runner that had only booted had no trace
    store. It does; `boot` resolves one. Cleared explicitly here, which is
    what the situation being tested actually looks like.)
    """
    run = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "c",
        world_mode="embedded", world_port=0,
    ))
    await run.boot()
    run.trace_store = None
    run.world_state.tool_invocations.append(_invocation(1))
    assert await run.drain_tool_invocations() == 0


# ── the wiring, which is the whole finding ─────────────────────────────


def test_the_tick_loop_drains() -> None:
    """A recorder nobody calls is what EVAL-25 IS. The mechanism existing
    is not the mechanism running - eight times over in this review."""
    source = (APP / "world" / "run_genesis.py").read_text(encoding="utf-8")
    assert "await self.drain_tool_invocations()" in source, (
        "EVAL-25: the mirror is written and nothing calls it, so the ring "
        "is still the only copy and still dies with the process"
    )


def test_the_comment_no_longer_claims_something_untrue() -> None:
    """The comment described the mirror in the present tense, as a reason
    the ring could safely be capped. A reader checking whether the history
    was safe would have read it and stopped."""
    source = (APP / "world" / "tools" / "state.py").read_text(encoding="utf-8")
    claim = source[source.index("The full history is not lost"):][:1400]
    assert "EVAL-25" in claim, (
        "the comment still asserts the mirror without recording that it was "
        "false for the life of the project"
    )


# ── the ring the cursor was indexing into ─────────────────────────────────
#
# The cursor was an index, justified in a comment that said "the list is
# append-only within a run". `registry.dispatch` trims that list from the
# FRONT at MAX_TOOL_INVOCATIONS_KEPT, so every record shifts down and the
# index stops pointing at what it pointed at. Once the ring was full the
# index equalled the length, `records[index:]` was empty, and the mirror
# wrote nothing for the rest of the run.
#
# Measured on the real numbers: 12,000 calls in, 5,000 written, 7,000 lost.
# That is the election30 report's "334 -> 0 -> 0" daily output and the 6,639
# day-1 model calls it filed as idle, because there were no invocations left
# to join them against.


def _trim(records: list, cap: int) -> None:
    """Exactly what `registry.dispatch` does before it appends."""
    if len(records) >= cap:
        del records[: len(records) - cap + 1]


@pytest.mark.asyncio
async def test_draining_survives_the_ring_filling_up(runner, monkeypatch) -> None:
    """The defect, at a cap small enough to read.

    Eight calls through a four-deep ring, drained every time, is the whole
    shape of a long run: the ring laps, and every call still has to be
    written exactly once.
    """
    run, db = runner
    cap = 4
    ring = run.world_state.tool_invocations

    for n in range(1, 9):
        _trim(ring, cap)
        ring.append(_invocation(n))
        await run.drain_tool_invocations()

    assert [row[2] for row in _rows(db)] == [1, 2, 3, 4, 5, 6, 7, 8], (
        "calls stopped reaching the store once the ring lapped - the cursor "
        "is indexing into a list that shifts"
    )
    assert run.invocations_lost == 0, "nothing was actually lost here"


@pytest.mark.asyncio
async def test_a_ring_that_laps_between_drains_says_what_it_lost(runner) -> None:
    """When records DO fall off, that is a hole in the run's tool log.

    The old code had a warning for this and it could never fire: it tested
    `_invocations_drained == 0`, and by the time the ring is full the cursor
    is long past 0. Silence made a 58% gap look like a quiet town.
    """
    run, db = runner
    cap = 4
    ring = run.world_state.tool_invocations

    ring.append(_invocation(1))
    await run.drain_tool_invocations()

    # No drain for a while, and the ring laps past the record we last wrote.
    for n in range(2, 10):
        _trim(ring, cap)
        ring.append(_invocation(n))

    written = await run.drain_tool_invocations()

    assert run.invocations_lost > 0, (
        "the ring dropped records before they were written and nothing said so"
    )
    assert written == len(ring), "what survived in the ring still has to be written"
    ticks = [row[2] for row in _rows(db)]
    assert ticks[0] == 1 and ticks[-1] == 9


@pytest.mark.asyncio
async def test_nothing_is_written_twice_after_a_lap(runner) -> None:
    """The property `test_nothing_is_written_twice` pins, once the ring has
    moved under the cursor."""
    run, db = runner
    cap = 4
    ring = run.world_state.tool_invocations

    for n in range(1, 7):
        _trim(ring, cap)
        ring.append(_invocation(n))
        await run.drain_tool_invocations()
    before = len(_rows(db))

    await run.drain_tool_invocations()
    await run.drain_tool_invocations()

    assert len(_rows(db)) == before, "a re-drain after a lap duplicated rows"


@pytest.mark.asyncio
async def test_a_run_that_never_laps_reports_no_loss(runner) -> None:
    """The counter has to stay quiet in the ordinary case, or it is noise."""
    run, _ = runner
    for n in range(1, 4):
        run.world_state.tool_invocations.append(_invocation(n))
        await run.drain_tool_invocations()

    assert run.invocations_lost == 0
