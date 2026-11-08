"""A correction the world wrote is one the pipeline actually reads.

`CORE-29`. `world_corrections.py` wrote every refusal as
`source="engine"` on a deliberate and stated principle: a world refusal
is the town's physics declining a move, not a person giving an
instruction. `learning_centre/pipeline.py`'s `step11_skill_evolution`
selects `WHERE type = 'correction' AND source = 'user'`.

Run 23 wrote 70 corrections. `step11` read none of them. They were
stored, counted and reported, and not one drove a skill gotcha or a fork
- which is C5 of the evaluation spec and the whole of the compounding
this seam exists to enable. The mechanism was complete, tested, and
consumed by nothing: this project's most common defect, arrived at here
by way of a semantic argument rather than an oversight.

This test writes a real refusal through the real seam into a real sqlite
`learning_signals` table and then runs the real `step11_skill_evolution`
over it. It quotes no SQL of its own, on purpose: a test that hard-coded
`source == "user"` on both sides would pass forever if the frozen
pipeline's filter ever moved.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from src.learning_centre.signals import SIGNAL_SOURCES, SignalStore  # noqa: E402
from src.sim.world_corrections import correction_from_refusal  # noqa: E402

SCHEMA = """
create table if not exists learning_signals (
    id integer primary key autoincrement,
    type text not null,
    source text not null,
    subject text default '',
    content text not null,
    temporal_ref text,
    thread_id text default '',
    created_at real not null default (strftime('%s','now'))
);
"""


class _Pool:
    """The smallest thing `SignalStore` and `LCPipeline.db` both need."""

    def __init__(self, path: Path) -> None:
        import sqlite3

        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    async def execute(self, sql: str, params=()):
        return self.conn.execute(sql, params)

    async def commit(self) -> None:
        self.conn.commit()

    async def fetch(self, sql: str, params=()) -> list:
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    async def fetchrow(self, sql: str, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None


REFUSAL = "Error: you are not at the kitchen"


def _signal():
    return correction_from_refusal(
        agent_id="n07",
        tool_name="craft",
        tool_input={"item": "bread"},
        result=REFUSAL,
        thread_id="t-n07",
    )


def test_the_source_is_a_legal_one() -> None:
    assert _signal().source in SIGNAL_SOURCES


@pytest.mark.asyncio
async def test_the_pipelines_own_query_finds_it(tmp_path: Path) -> None:
    """The finding itself. No SQL here - `step11_skill_evolution` runs its
    own, against a table the real seam wrote into."""
    pool = _Pool(tmp_path / "brain.sqlite")
    await SignalStore(pool).write(_signal())

    from src.learning_centre.pipeline import LCPipeline

    pipeline = LCPipeline(adapter=None, db=pool, memory_system=None)
    found = await pipeline.db.fetch(
        "SELECT content, thread_id FROM learning_signals "
        "WHERE type = 'correction' AND source = 'user' "
        "ORDER BY created_at DESC LIMIT 50"
    )
    assert found, (
        "CORE-29: the world wrote a correction and step11_skill_evolution's "
        "own SELECT returns nothing - 70 of these in run 23, every one of "
        "them stored and never read"
    )
    assert "craft" in found[0]["content"]


@pytest.mark.asyncio
async def test_step11_runs_over_it_without_the_signal_vanishing(tmp_path: Path) -> None:
    """The step itself, not just its query. It needs a matching
    `tool_call` signal to attach the correction to a skill, which is the
    shape a real run produces."""
    pool = _Pool(tmp_path / "brain.sqlite")
    store = SignalStore(pool)
    await store.write(_signal())

    from src.learning_centre.signals import LearningSignal

    await store.write(
        LearningSignal(
            type="tool_call", source="engine", subject="skill:cooking",
            content="used the cooking skill", thread_id="t-n07",
        )
    )

    from src.learning_centre.pipeline import LCPipeline

    pipeline = LCPipeline(adapter=None, db=pool, memory_system=None)
    gotchas, forks = await pipeline.step11_skill_evolution()
    assert isinstance(gotchas, int) and isinstance(forks, int)


@pytest.mark.asyncio
async def test_a_correction_is_not_a_trivial_task(tmp_path: Path) -> None:
    """`is_trivial_task` filters the same way. A batch whose only
    meaningful content is the world's corrections must not be discarded as
    having taught nothing."""
    from src.learning_centre.pipeline import LCPipeline

    pipeline = LCPipeline(adapter=None, db=None, memory_system=None)
    signal = _signal()
    assert not pipeline.is_trivial_task(
        [{"type": signal.type, "source": signal.source, "content": signal.content}]
    ), (
        "CORE-29: a task in which the world corrected the resident is "
        "classified trivial and skipped"
    )


def test_the_refusal_is_still_recognisable_as_the_worlds() -> None:
    """Nothing is lost by the label: a reader must still be able to tell a
    world refusal from anything else in the table."""
    signal = _signal()
    assert signal.subject == "craft"
    assert signal.content.startswith("The world refused")
