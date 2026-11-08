"""The three paths a tool call travels, each held end to end.

`WIRE-01`. Kasukabe has three wirings and every run depends on all three:

    tool -> world    a call actually mutates state and is charged
    tool -> sim      the registry the engine serves is what a model can
                     see and build arguments from
    tool -> output   what happened reaches the trace, so it can be read
                     back and reported on

Every one of them has been silently broken at some point in this
project's history, and each time the run looked healthy from the
outside. `agent.utterance.spoken` published into a bus nobody recorded
(RC-2). Politics events published into `noop_sink` for 23 runs
(CORE-02). Index rows telling a model a tool took no parameters, so it
called `harvest({})` twenty times (TRADE-02). None of those produced an
error; each produced a run that cost money and proved nothing.

This file boots a real town, drives one call down each path, and looks
for it at the far end. It is deliberately end-to-end rather than three
unit tests: what breaks is never the piece, it is the seam between two
pieces that each still pass their own tests.

TWO THINGS THIS FILE HAS ALREADY CAUGHT, both in its own first draft:

* it looked for a `required` key on index rows, when the parameters are
  in the DESCRIPTION as `Needs: goal_id.` - so it reported TRADE-02
  unwired when TRADE-02 was fine;
* it looked for speech in `events`, when speech has its own
  `utterances` table (RC-2).

Both are the EVAL-01 failure - a reader keyed to names nothing writes -
committed by the very test written to catch it. They are recorded here
because the next person to extend this file will be tempted the same
way.
"""
from __future__ import annotations

import sqlite3

import pytest
import pytest_asyncio

from world.run_genesis import (
    PUBLISHED_BY_TYPE,
    RECORDED_BY_TYPE,
    GenesisRunner,
    GenesisRunnerConfig,
    is_worth_recording,
)
from world.tests.map_assets import needs_the_map

pytestmark = needs_the_map


@pytest_asyncio.fixture
async def town(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "cp",
        world_mode="embedded", world_port=0, start_at_dawn=True,
    ))
    await runner.boot(resume=False)
    await runner.start_agents()
    try:
        yield runner, tmp_path / "trace.db"
    finally:
        await runner.shutdown()


def _somebody(runner) -> str:
    return sorted(runner.world_state.agents)[0]


@pytest.mark.asyncio
async def test_a_call_moves_real_state_and_is_paid_for(town):
    runner, _db = town
    world, registry = runner.world_state, runner.registry
    who = _somebody(runner)

    before = world.ledger.sim_balance(who)
    await registry.execute(world, who, "say", {"content": "the wire is live"})

    assert world.ledger.sim_balance(who) < before, "a call that changed the world was free"
    assert any(u.get("speaker_agent_id") == who for u in world.utterances), (
        "say did not reach world.utterances - RC-2, again"
    )


@pytest.mark.asyncio
async def test_a_walk_starts_a_journey_or_arrives(town):
    runner, _db = town
    world, registry = runner.world_state, runner.registry
    who = _somebody(runner)
    was = world.location_of(who)

    await registry.execute(
        world, who, "move", {"destination_location_id": "Kasukabe Government Complex"}
    )

    moved = world.location_of(who) != was
    walking = world.movement is not None and world.movement.has_active_journey(who)
    assert moved or walking, "move neither arrived nor started a journey"


@pytest.mark.asyncio
async def test_the_registry_the_engine_serves_is_usable(town):
    """A model builds arguments from these rows. If they are wrong it
    calls the right verb with the wrong shape, which is TRADE-01."""
    from world.tools.registry import tool_registry_rows

    runner, _db = town
    rows = tool_registry_rows(runner.registry, runner.world_state, _somebody(runner))

    assert rows, "the registry served nothing"
    full = [r for r in rows if not r.get("elsewhere")]
    index = [r for r in rows if r.get("elsewhere")]
    assert full and index, "the budget split produced only one kind of row"
    for row in full:
        assert isinstance(row.get("input_schema"), dict), (
            f"{row['name']} is served in full with no schema to build from"
        )


@pytest.mark.asyncio
async def test_an_index_row_you_can_act_on_names_its_parameters(town):
    """TRADE-02. An index row said `"properties": {}` - *this tool takes
    no parameters* - for 47 tools usable exactly where the resident
    stood, and the model called `harvest({})` twenty times of 38.

    Rows gated ELSEWHERE are exempt on purpose: "Only at the town hall"
    is the useful fact there, and the parameters do not matter until you
    have walked over.
    """
    from world.tools.registry import tool_registry_rows

    runner, _db = town
    registry = runner.registry
    rows = tool_registry_rows(registry, runner.world_state, _somebody(runner))

    unnamed = []
    checked = 0
    for row in rows:
        if not row.get("elsewhere") or "Only at" in row["description"]:
            continue
        spec = registry.get(row["name"])
        required = [
            key for key, field in spec.params_model.model_fields.items()
            if field.is_required()
        ]
        if not required:
            continue
        checked += 1
        if "Needs:" not in row["description"]:
            unnamed.append(row["name"])

    assert checked > 0, "no index row here takes parameters; this proves nothing"
    assert not unnamed, (
        f"{len(unnamed)} index rows are callable right here and do not say what "
        f"they need: {unnamed[:8]}"
    )


@pytest.mark.asyncio
async def test_what_happened_reaches_the_trace(town):
    runner, db_path = town
    world, registry = runner.world_state, runner.registry
    who = _somebody(runner)

    await registry.execute(world, who, "say", {"content": "for the record"})
    import asyncio

    await asyncio.sleep(0.4)

    db = sqlite3.connect(str(db_path))
    assert db.execute("select count(*) from utterances").fetchone()[0] > 0, (
        "speech reached nothing readable - the RC-2 failure"
    )
    assert db.execute("select count(*) from transactions").fetchone()[0] > 0, (
        "money moved and the ledger table is empty"
    )
    assert world.tool_invocations, "no call was logged for researchers"


@pytest.mark.asyncio
async def test_nothing_recordable_is_published_and_dropped(town):
    """CORE-24's two counters. An event published and not recorded is a
    recorder bug; one never published is a world bug; and for eleven
    `post_price` calls in run 24 we could not tell which."""
    runner, _db = town
    world, registry = runner.world_state, runner.registry
    who = _somebody(runner)

    await registry.execute(world, who, "say", {"content": "counted"})
    import asyncio

    await asyncio.sleep(0.4)

    dropped = {
        kind: published - RECORDED_BY_TYPE.get(kind, 0)
        for kind, published in PUBLISHED_BY_TYPE.items()
        if is_worth_recording(kind) and published > RECORDED_BY_TYPE.get(kind, 0)
    }
    assert not dropped, f"published and never recorded: {dropped}"
