"""CORE-24 + the run-24 price mystery: published vs recorded, per type.

Run 24 made **eleven successful `post_price` calls** and the `events` table
held **zero** `economy.price.posted` rows — while other tool events from the
same run (`politics.candidacy.declared`, tick 47) recorded normally, the
recorder never reported a dropped row, and the whole chain records correctly
in isolation.

That is unresolved. What made it unresolvable from the outside is that there
was no way to tell an event that never reached the bus from one that reached
it and was dropped on the way to the table. `TraceStore.stats()` has existed
since the recorder was written and had no route at all.

Two tallies of the same stream now answer it.
"""
from __future__ import annotations

import pytest

from world.run_genesis import (
    PUBLISHED_BY_TYPE,
    RECORDED_BY_TYPE,
    is_worth_recording,
)


def test_both_tallies_exist():
    assert isinstance(PUBLISHED_BY_TYPE, dict)
    assert isinstance(RECORDED_BY_TYPE, dict)


def test_persist_counts_what_it_publishes_and_what_it_keeps():
    """Both counters must be fed from the same function, or the comparison
    means nothing."""
    import inspect

    from world.run_genesis import GenesisRunner

    src = inspect.getsource(GenesisRunner.attach_persistence)
    assert "PUBLISHED_BY_TYPE[event.type]" in src, (
        "nothing counts what reaches the bus, so a missing event cannot be "
        "told from a dropped one"
    )
    assert "RECORDED_BY_TYPE[event.type]" in src


def test_the_route_reports_the_difference():
    """The one line worth reading: what reached the bus and not the table."""
    import inspect

    from world.run_genesis import build_embedded_world_app

    src = inspect.getsource(build_embedded_world_app)
    assert '"/world/recorder"' in src, "TraceStore.stats() still has no route"
    assert "published_but_not_recorded" in src


@pytest.mark.asyncio
async def test_a_recorded_event_appears_in_both_tallies(tmp_path, monkeypatch):
    from world.core.events import WorldEvent
    from world.run_genesis import GenesisRunner, GenesisRunnerConfig

    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "w.db"))
    PUBLISHED_BY_TYPE.clear()
    RECORDED_BY_TYPE.clear()

    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "c", world_mode="embedded",
        world_port=0, budget_cap_cents=40_000,
    ))
    await runner.boot()
    await runner.world_state.event_bus.publish(
        WorldEvent(type="economy.price.posted", tick=40,
                   payload={"item": "wheat", "ask_sim": 55})
    )
    assert PUBLISHED_BY_TYPE.get("economy.price.posted", 0) >= 1
    assert RECORDED_BY_TYPE.get("economy.price.posted", 0) >= 1, (
        "a price reached the bus and not the table - which is exactly the "
        "shape of run 24's unexplained zero"
    )


@pytest.mark.asyncio
async def test_an_excluded_event_is_published_but_not_recorded(tmp_path, monkeypatch):
    """`agent.moved` is deliberately excluded (the 21 GB). It must show up
    in the difference as a KNOWN exclusion, not look like a fault."""
    from world.core.events import WorldEvent
    from world.run_genesis import GenesisRunner, GenesisRunnerConfig

    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "w.db"))
    PUBLISHED_BY_TYPE.clear()
    RECORDED_BY_TYPE.clear()

    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "c", world_mode="embedded",
        world_port=0, budget_cap_cents=40_000,
    ))
    await runner.boot()
    await runner.world_state.event_bus.publish(
        WorldEvent(type="agent.moved", tick=40, payload={"agent_id": "n01"})
    )
    assert not is_worth_recording("agent.moved")
    assert PUBLISHED_BY_TYPE.get("agent.moved", 0) >= 1
    assert "agent.moved" not in RECORDED_BY_TYPE
