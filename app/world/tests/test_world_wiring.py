"""Boot-proof test for wiring.build_world_server() / WorldServer
(task "wire-world"'s own spec: "a test that constructs the world,
advances 10 ticks with mock agents, and asserts events flowed and the
ledger invariant held").

"Mock agents" here means no real LLM turn is ever run (app/CONVENTIONS.md:
"Never call a real LLM API in a test") - the tick loop itself, the
scheduler, the ledger, charter, crime, and the trace store are all real,
exactly what a live genesis run boots. Each due agent's "turn" is mocked
by immediately calling mark_agent_decided() with no tool call attached,
matching core/engine.py's own documented caller contract: "the caller
(an agent-runtime layer sitting above world-core) runs turns for the due
agents and reports each one back via mark_agent_decided()".

KASUKABE_SQLITE_PATH is deliberately left unset so every test gets its
own isolated in-memory trace store (supabase/config.py's own default),
matching every other suite in this repo.
"""

from __future__ import annotations

import pytest

from world.core.events import WorldEvent
from world.ledger.money import TOTAL_USD_CENTS
from world.wiring import WorldConfig, build_world_server
from world.tests.map_assets import needs_the_map

# TOOL-27. This module needs the Ville map assets, which live in the
# sibling `smallville/` checkout, are gitignored, and are absent on any
# host that did not clone them. Without them these FAILED rather than
# skipped, and red for environment hides real regressions inside the
# noise - which is how TOOL-09 stayed invisible for a whole cycle.
pytestmark = needs_the_map


@pytest.mark.asyncio
async def test_world_server_boots_with_full_genesis_population() -> None:
    server = await build_world_server(WorldConfig())
    try:
        # The town is the generated population PLUS the institution
        # roster (hospital, school, police, fire...). Read from the
        # genesis the server actually loaded rather than pinned to a
        # literal, so restaffing the town does not silently fail here.
        assert len(server.world.agents) == server.genesis.population
        assert len(server.world.households) == len(server.genesis.households)
        # Every genesis agent is enrolled with the scheduler and placed
        # somewhere on the map (tick_engine.register_agent's two jobs).
        assert server.tick_engine.scheduler.agent_count == server.genesis.population
        for agent_id in server.world.agents:
            assert server.tick_engine.locations.location_of(agent_id) is not None
            assert server.world.locations.location_of(agent_id) is not None
        # The 4 owned genesis businesses each have a funded ledger account;
        # the 10 control businesses are exposed read-only, not ledger accounts.
        # Counted from the cast file rather than pinned at 4. The number moved
        # when four bakers, four innkeepers and five shopkeepers were finally
        # given somewhere to work - see
        # `test_a_trade_has_somewhere_to_practise_it`. A magic number here
        # would have to be edited every time the town gains a trade, and says
        # nothing about whether the right businesses were seeded.
        import json as _json
        from pathlib import Path as _Path
        _cast = _json.loads(
            (_Path(__file__).resolve().parents[2] / "world" / "genesis.json")
            .read_text(encoding="utf-8")
        )
        _expected = len(_cast["small_businesses"]) + (
            1 if _cast.get("construction_company") else 0
        )
        assert len(server.world.businesses) == _expected
        assert len(server.control_businesses) == 10
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ten_ticks_flow_events_and_hold_the_ledger_invariant() -> None:
    server = await build_world_server(WorldConfig())
    try:
        seen_types: set[str] = set()
        tick_advanced_count = 0

        async def collect(event: WorldEvent) -> None:
            nonlocal tick_advanced_count
            seen_types.add(event.type)
            if event.type == "world.tick.advanced":
                tick_advanced_count += 1

        server.event_bus.subscribe("*", collect)

        for _ in range(10):
            result = await server.advance_tick()
            # Mock agent turns (app/CONVENTIONS.md: never a real LLM call
            # in a test): every due agent's "decision" is a scripted move
            # tool call instead of an inference call, then reported back
            # via mark_agent_decided() exactly like a real agent-runtime
            # layer would - core/engine.py's own documented caller
            # contract for TickEngine.tick()'s due_agents.
            for agent_id in result.due_agents:
                await server.execute_tool(agent_id, "move", {"destination_location_id": "town_hall"})
                server.mark_agent_decided(agent_id)

        assert server.world.current_tick == 10
        # Real events actually flowed on the shared bus: one
        # world.tick.advanced per tick (deterministic) plus real
        # agent.moved events from the mock agents' scripted tool calls.
        assert tick_advanced_count == 10
        assert "world.tick.advanced" in seen_types
        assert "agent.moved" in seen_types
        assert len(seen_types) > 1

        status = server.ledger_invariant_status()
        assert status["halted"] is False
        assert status["usd_total_cents"] == TOTAL_USD_CENTS == 100_000
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ledger_transactions_and_agent_rows_reach_the_trace_store() -> None:
    """Proves the SQLite-WAL persistence wiring end to end: genesis
    funding produced real Transaction rows, and record_event's live
    EventBus subscription actually persisted them (supabase/'s SQLite
    client, not a mock)."""
    server = await build_world_server(WorldConfig())
    try:
        assert server.trace_store is not None
        agent_rows = await server.trace_store.client.select("agents", limit=1000)
        assert len(agent_rows) == server.genesis.population

        transaction_rows = await server.trace_store.client.select("transactions", limit=10)
        assert len(transaction_rows) > 0
        usd_rows = [r for r in transaction_rows if r["currency"] == "usd"]
        for row in usd_rows:
            assert (
                row["usd_wallets_total_after"] + row["usd_treasury_total_after"] + row["usd_burned_total_after"]
                == TOTAL_USD_CENTS
            )

        event_rows = await server.trace_store.client.select("events", limit=10)
        assert len(event_rows) > 0
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_manual_checkpoint_reaches_world_snapshots_table() -> None:
    """Day-boundary checkpoints only fire every 144 ticks (too slow for a
    unit test); this exercises the exact same TickEngine.checkpoints.write()
    path directly instead of burning 144 ticks to reach one for real."""
    server = await build_world_server(WorldConfig())
    try:
        checkpoint = await server.tick_engine.checkpoints.write(tick_index=1, game_day=0)
        assert "world_state" in checkpoint.components
        assert "map" in checkpoint.components

        assert server.trace_store is not None
        snapshot_rows = await server.trace_store.client.select("world_snapshots", limit=10)
        assert len(snapshot_rows) == 1
        assert snapshot_rows[0]["agent_count"] == server.genesis.population
        assert (
            snapshot_rows[0]["usd_wallets_total_cents"]
            + snapshot_rows[0]["usd_treasury_total_cents"]
            + snapshot_rows[0]["usd_burned_total_cents"]
            == TOTAL_USD_CENTS
        )
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_a_real_tool_call_moves_state_and_publishes_events() -> None:
    """The internal async API's tool-call path (WorldServer.execute_tool,
    the same call main.py's POST /tools/{tool_name} makes) actually
    mutates world state through the real ToolRegistry, not a stub."""
    server = await build_world_server(WorldConfig())
    try:
        agent_id = next(iter(server.world.agents))
        origin = server.world.location_of(agent_id)
        result = await server.execute_tool(agent_id, "move", {"destination_location_id": "town_hall"})

        assert result.result["location_id"] == "town_hall"
        # TOOL-05: `town_hall` has no map entry point, so this is the
        # opaque jump - and the result now says so rather than looking
        # identical to a walk. Asserted on the key rather than the whole
        # dict so the shape can grow without this test deciding it.
        assert result.result["walked"] is False
        assert "no route on the map" in result.result["note"]
        assert server.world.location_of(agent_id) == "town_hall"
        assert server.world.location_of(agent_id) != origin
        assert any(e.type == "agent.moved" for e in result.events)
    finally:
        await server.close()
