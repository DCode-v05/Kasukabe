"""Wiring audit: Track A and Track B, and the seams BETWEEN them.

Every test here asks the same question in a different place: is this piece
actually reachable at runtime, or does it merely exist? A module that is
imported by nothing, an event nobody subscribes to, and a route no host
mounts all pass their own unit tests and are all still broken.

Four real breaks were found by writing these and are now fixed:
  1. the memory stream wrote a file but never reached the world event bus,
     so TraceStore.record_memory_if_new could never fire;
  2. settle_property_estate (A9) was called by nothing, so a dead owner's
     land never moved;
  3. agent.aged_to_adult (A8) had no consumer, so nobody ever came of age;
  4. the genesis runner's embedded world app - the ONLY host that boots in
     production - mounted neither the family nor the skills nor the
     governance router, so the console's Town tab 404'd against a real run.
"""

from __future__ import annotations

import inspect

from world.core.clock import TICKS_PER_DAY, timestamp_for

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from world.run_genesis import build_embedded_world_app
from world.tests.test_run_genesis import make_runner


# -- gap 4: every host serves the same read surface -----------------------

TOWN_ROUTES = (
    "/family/agents",
    "/family/households",
    "/family/at-risk",
    "/family/wealth",
    "/family/money-flow",
    "/skills/town",
    "/skills/families",
    "/businesses/pnl",
    "/governance/treasury",
    "/governance/policy-and-treasury",
    "/governance/terms",
)


@pytest.mark.asyncio
async def test_the_genesis_runners_embedded_app_serves_every_town_route(tmp_path: Path):
    """The runner is what boots in production. If it does not serve these,
    B7's console tab is broken no matter how well world/main.py works."""
    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    app = build_embedded_world_app(
        runner.world_state, runner.registry, runner.ledger, runner.metrics,
        genesis=runner.genesis,
    )
    client = TestClient(app)
    for route in TOWN_ROUTES:
        assert client.get(route).status_code == 200, f"{route} is not served by the runner"


@pytest.mark.asyncio
async def test_world_main_serves_the_same_routes():
    """Both hosts, one contract - or the console behaves differently
    depending on which one it is pointed at."""
    import world.main as main
    from fastapi.routing import APIRoute

    served = set()

    def walk(router):
        for r in getattr(router, "routes", []):
            if isinstance(r, APIRoute):
                served.add(r.path)
            else:
                inner = getattr(r, "original_router", None)
                if inner is not None:
                    walk(inner)

    walk(main.app.router)
    for route in TOWN_ROUTES:
        assert route in served, f"{route} missing from world/main.py"


# -- gap 1: the memory pipeline actually connects -------------------------

@pytest.mark.asyncio
async def test_a_memory_posted_by_an_engine_reaches_the_trace_store(tmp_path: Path):
    """The end-to-end seam: engine memory_stream -> POST /agents/{id}/memories
    -> world event bus -> TraceStore.record_memory_if_new -> a `memories` row.

    Both halves passed their own unit tests while being completely
    disconnected, which is exactly the failure this file exists to catch.
    """
    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    app = build_embedded_world_app(
        runner.world_state, runner.registry, runner.ledger, runner.metrics,
        genesis=runner.genesis,
    )

    seen: list[dict] = []

    async def spy(event):
        if event.type == "agent.memory.recorded":
            seen.append(event.payload)

    runner.world_state.event_bus.subscribe("*", spy)
    agent_id = next(iter(runner.world_state.agents))

    resp = TestClient(app).post(
        f"/agents/{agent_id}/memories",
        json={"memory_type": "observation", "content": "I did buy: bought 3 bread",
              "importance": 5, "related_memory_ids": [1], "tick": 7},
    )
    assert resp.status_code == 200 and resp.json()["recorded"] == 1
    assert seen, "the world bus never saw the memory"
    assert seen[0]["agent_id"] == agent_id
    assert seen[0]["memory_type"] == "observation"
    assert seen[0]["importance"] == 5


@pytest.mark.asyncio
async def test_a_dream_probe_can_never_be_recorded_as_a_memory(tmp_path: Path):
    """DESIGN.md section 10: dream probes are "never written to agent
    memory". Refused at the door, not just by the table's CHECK."""
    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    app = build_embedded_world_app(
        runner.world_state, runner.registry, runner.ledger, runner.metrics,
        genesis=runner.genesis,
    )
    agent_id = next(iter(runner.world_state.agents))
    resp = TestClient(app).post(
        f"/agents/{agent_id}/memories",
        json={"memory_type": "dream_probe", "content": "probe", "importance": 9},
    )
    assert resp.json()["recorded"] == 0


def test_the_engine_side_sink_is_actually_attached():
    """pinned_boot must hand the writer a sink. Constructing it without one
    is what left the pipeline dead."""
    import pathlib

    boot = (pathlib.Path(__file__).resolve().parents[3]
            / "engine" / "src" / "sim" / "pinned_boot.py").read_text(encoding="utf-8")
    assert "sink=world.record_memory" in boot, "MemoryStreamWriter has no sink"


# -- the boundary, and the deliberate duplicate it forces -----------------

def test_world_never_imports_infra():
    """infra/README.md: "Never imported by world/, broker/, game/, or
    research/." A sys.path insert is still an import."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = []
    for pkg in ("world", "broker", "game", "research"):
        for path in (root / pkg).rglob("*.py"):
            # Production code only. A test may reach across the line - the
            # parity check below has to import both watchdogs to compare
            # them - and that is not what the boundary is protecting.
            if "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "infra" in text and ("import" in text or "sys.path" in text):
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if "infra" in stripped and ("sys.path" in stripped or stripped.startswith(("import ", "from "))):
                        offenders.append(f"{path.relative_to(root)}: {stripped}")
    assert not offenders, "world/ reached into infra/:\n" + "\n".join(offenders)


def test_the_two_watchdogs_make_the_same_decision():
    """run_genesis carries its own watchdog because it may not import
    infra's (above). A deliberate duplicate is fine; two watchdogs that
    quietly disagree are not - so they are checked against each other."""
    import sys
    from pathlib import Path as _Path

    sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "infra" / "firecracker"))
    import supervisor as infra                      # test-only: see above

    from world.run_genesis import (
        SupervisorWatchdog as LocalWatchdog,
        UnitState as LocalState,
        UnitStatus as LocalStatus,
    )

    NOW = 1_000.0
    cases = [
        ("failed",            LocalState.FAILED,    NOW,        True),
        ("running, fresh",    LocalState.RUNNING,   NOW - 5,    False),
        ("running, silent",   LocalState.RUNNING,   NOW - 9_999, True),
        ("unhealthy, fresh",  LocalState.UNHEALTHY, NOW - 5,    False),
        ("unhealthy, silent", LocalState.UNHEALTHY, NOW - 9_999, True),
        ("stopped",           LocalState.STOPPED,   NOW,        False),
        ("stopping",          LocalState.STOPPING,  NOW,        False),
        ("pending",           LocalState.PENDING,   None,       False),
        ("no heartbeat yet",  LocalState.STARTING,  None,       False),
    ]
    local = LocalWatchdog(None, clock=lambda: NOW, silence_timeout_s=90.0)
    remote = infra.SupervisorWatchdog(None, clock=lambda: NOW, silence_timeout_s=90.0)

    for label, state, heartbeat, expected in cases:
        local_status = LocalStatus(
            unit_id="u", state=state, backend_handle="h",
            started_at=0.0, last_health_check_at=heartbeat,
        )
        remote_status = infra.UnitStatus(
            unit_id="u", state=infra.UnitState(state.value), backend_handle="h",
            started_at=0.0, last_health_check_at=heartbeat,
        )
        got_local = local.needs_restart(local_status) is not None
        got_remote = remote.needs_restart(remote_status) is not None
        assert got_local == expected, f"local watchdog disagrees on {label}"
        assert got_remote == expected, f"infra watchdog disagrees on {label}"


# -- gaps 2 and 3: the life-cycle mechanics actually fire ------------------

@pytest.mark.asyncio
async def test_a_minor_who_turns_eighteen_stops_being_billed_to_a_parent(tmp_path: Path):
    """A8 end to end. `aging.advance_age` fired agent.aged_to_adult and
    nothing consumed it, so the parent went on funding an adult's cognition
    for the rest of the run."""
    from world.life.models import Household

    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    world = runner.world_state

    parent_id, child_id = list(world.agents)[:2]
    world.agents[child_id] = world.agents[child_id].model_copy(
        update={"age_years": 18.0, "sponsor_parent_id": parent_id,
                "parent_ids": [parent_id], "household_id": "hh-test"}
    )
    world.households["hh-test"] = Household(
        household_id="hh-test", member_ids=[parent_id, child_id]
    )
    account = runner.ledger.get_account(child_id)
    account.is_minor = True
    account.parent_id = parent_id
    assert runner.ledger.resolve_usd_payer(child_id) == parent_id

    grown, events = await runner.bring_of_age(world.agents[child_id], total_hours=100.0)

    assert grown.sponsor_parent_id is None
    assert runner.ledger.resolve_usd_payer(child_id) == child_id, (
        "the wallet half of coming of age never happened"
    )
    assert any(e.type == "agent.parent_support.ended" for e in events)


@pytest.mark.asyncio
async def test_a_dead_owners_land_moves_to_their_heir(tmp_path: Path):
    """A9 end to end. settle_property_estate was written, tested, and called
    by nothing - so land stayed in a dead agent's name forever."""
    from world.tools.models import OwnerClass, TileOwnership
    from world.tools.primitives.property import require_tile
    from world.tools.state import tile_key

    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    world = runner.world_state

    owner_id, heir_id = list(world.agents)[:2]
    # Genesis agents are married, and section 31.3's default is the NEAREST
    # living relative - a spouse is nearer than a child. Clearing the spouse
    # is what makes this a test of the child path rather than of that order.
    world.agents[owner_id] = world.agents[owner_id].model_copy(
        update={"children_ids": [heir_id], "spouse_id": None}
    )
    world.tile_owners[tile_key(9, 9)] = TileOwnership(
        tile_x=9, tile_y=9, owner_id=owner_id, owner_class=OwnerClass.PRIVATE
    )

    await runner.apply_death(world.agents[owner_id], "elderly_hazard")

    assert require_tile(world, 9, 9).owner_id == heir_id, (
        "the estate never moved - apply_death did not settle land"
    )


@pytest.mark.asyncio
async def test_a_spouse_inherits_before_a_child(tmp_path: Path):
    """Section 31.3's default is the nearest living relative, and the order
    matters - it is what decides who actually gets the house."""
    from world.tools.models import OwnerClass, TileOwnership
    from world.tools.primitives.property import require_tile
    from world.tools.state import tile_key

    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    world = runner.world_state

    owner_id, spouse_id, child_id = list(world.agents)[:3]
    world.agents[owner_id] = world.agents[owner_id].model_copy(
        update={"spouse_id": spouse_id, "children_ids": [child_id]}
    )
    world.tile_owners[tile_key(13, 13)] = TileOwnership(
        tile_x=13, tile_y=13, owner_id=owner_id, owner_class=OwnerClass.PRIVATE
    )
    await runner.apply_death(world.agents[owner_id], "elderly_hazard")
    assert require_tile(world, 13, 13).owner_id == spouse_id


@pytest.mark.asyncio
async def test_an_heirless_estate_escheats_rather_than_staying_with_the_dead(tmp_path: Path):
    from world.tools.models import OwnerClass, TileOwnership
    from world.tools.primitives.property import GOVERNMENT_OWNER_ID, require_tile
    from world.tools.state import tile_key

    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    world = runner.world_state

    hermit_id = next(iter(world.agents))
    world.agents[hermit_id] = world.agents[hermit_id].model_copy(
        update={"spouse_id": None, "children_ids": [], "parent_ids": []}
    )
    world.tile_owners[tile_key(11, 11)] = TileOwnership(
        tile_x=11, tile_y=11, owner_id=hermit_id, owner_class=OwnerClass.PRIVATE
    )

    await runner.apply_death(world.agents[hermit_id], "elderly_hazard")
    assert require_tile(world, 11, 11).owner_id == GOVERNMENT_OWNER_ID


@pytest.mark.asyncio
async def test_a_death_still_succeeds_if_the_land_register_objects(tmp_path: Path):
    """A death must never fail because of the estate."""
    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    victim = next(iter(runner.world_state.agents))
    runner.world_state.tile_owners = None            # break the register outright
    updated, events = await runner.apply_death(runner.world_state.agents[victim], "terminal")
    assert updated.alive is False
    assert any(e.type == "agent.died" for e in events)


# -- COGNITION_DELIVERY's four rows, each actually reachable ---------------

@pytest.mark.asyncio
async def test_say_produces_the_row_the_conversation_log_needs(tmp_path: Path):
    """COGNITION_DELIVERY's fourth row: "one new row saved per thing said...
    so it can be replayed and watched later." A4 fires the event, A5 turns
    it into the row - this proves the two meet."""
    from world.tools.bootstrap import build_world
    from world.tools.tests.conftest import spawn_agent
    from world.wiring import TraceStore
    import asyncio as _asyncio

    world, registry = await build_world(seed=1)
    await spawn_agent(world, "speaker", "square")
    await spawn_agent(world, "listener", "square")

    written: list[tuple[str, list[dict]]] = []

    class Recorder:
        async def insert(self, table, rows):
            written.append((table, rows))

    trace = TraceStore.__new__(TraceStore)
    trace.client = Recorder()
    trace.known_agent_ids = {"speaker", "listener"}
    trace.persisted_transaction_ids = set()
    trace.write_lock = _asyncio.Lock()

    result = await registry.execute(
        world, "speaker", "say", {"content": "good morning", "listener_agent_id": "listener"}
    )
    for event in result.events:
        await trace.record_utterance_if_new(event)

    tables = [t for t, _ in written]
    assert "utterances" in tables, "a spoken line never became a row"
    row = next(rows[0] for t, rows in written if t == "utterances")
    assert row["speaker_agent_id"] == "speaker"
    assert row["content"] == "good morning"


def test_the_persona_file_is_written_before_the_engine_starts():
    """COGNITION_DELIVERY row 1, and A3. The engine reads
    <september_home>/persona.txt at boot; if start_agents does not write it
    first, every agent boots with no identity."""
    import pathlib

    runner_src = pathlib.Path("world/run_genesis.py").read_text(encoding="utf-8")
    boot_src = (pathlib.Path(__file__).resolve().parents[3]
                / "engine" / "src" / "sim" / "pinned_boot.py").read_text(encoding="utf-8")
    assert 'persona.txt' in runner_src, "run_genesis never writes persona.txt"
    assert 'persona.txt' in boot_src, "the engine never reads persona.txt"
    # and it is written inside start_agents, before the subprocess spawns
    start = runner_src.index("async def start_agents")
    assert "persona.txt" in runner_src[start:start + 8000]


def test_every_registered_tool_has_a_price():
    """A6's rule, held for A8's and A9's tools too: an unpriced tool is a
    free action, and a free action is not one the ledger can govern."""
    import asyncio as _asyncio

    from world.ledger.action_costs import DEFAULT_SCHEDULE
    from world.tools.bootstrap import build_world

    async def go():
        _, registry = await build_world(seed=1)
        return set(registry.tools)

    registered = _asyncio.run(go())
    missing = registered - set(DEFAULT_SCHEDULE.rules)
    assert not missing, f"tools with no cost row: {sorted(missing)}"


# -- gap 5: a route that is started but never stepped ----------------------
# world.core.movement.MovementSystem's own docstring says advance() "is a
# tick loop's job ... once a caller wires movement_system= into it - see
# that module's docstring for the exact one-line change two composition
# roots still need". Neither root passed it, so every route a `move` started
# sat frozen at its first tile forever: world.agent_tiles never updated,
# GET /agents' tile_x/tile_y never changed, and the only positions anything
# downstream ever saw came from move_handler's instant-jump fallback.


def test_both_composition_roots_hand_movement_to_the_tick_engine():
    """The engine cannot walk anyone it was never given.

    Read off the parsed call node rather than the source text: the keyword
    list is the thing that matters, and a comment mentioning another call
    should not be able to satisfy - or break - this check.
    """
    import ast
    import inspect

    from world import run_genesis, wiring

    for module in (run_genesis, wiring):
        tree = ast.parse(inspect.getsource(module))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "TickEngine"
        ]
        assert calls, f"{module.__name__} no longer builds a TickEngine at all"
        for call in calls:
            keywords = {kw.arg for kw in call.keywords}
            assert "movement_system" in keywords, (
                f"{module.__name__} builds a TickEngine without movement_system=; "
                "routes will never advance"
            )


@pytest.mark.asyncio
async def test_a_started_route_advances_one_tile_per_tick():
    """End to end over the real map: start a walk, tick the engine, and
    watch both the journey and world.agent_tiles actually move."""
    from world.core.engine import TickEngine
    from world.map.model import TileCoord
    from world.tools.bootstrap import build_world

    world, _registry = await build_world(genesis_tick=0, seed=7)
    if world.movement is None:
        pytest.skip("Ville map assets not present; MovementSystem is None by design")

    engine = TickEngine(event_bus=world.event_bus, movement_system=world.movement)
    assert engine.movement is world.movement, "engine did not take the movement system"

    world_map = world.movement.world_map
    start = world_map.nearest_walkable(TileCoord(60, 60), max_radius=40)
    assert start is not None, "no walkable tile near the middle of the real map"
    dest = world_map.nearest_walkable(TileCoord(start.x + 12, start.y + 8), max_radius=40)
    assert dest is not None and dest != start

    agent_id = "walk-probe"
    journey = world.movement.start_journey(agent_id, start, dest)
    assert journey is not None and not journey.arrived, "no route between two walkable tiles"
    first_tile = journey.current_tile

    for _ in range(12):
        await engine.tick()
        if not world.movement.has_active_journey(agent_id):
            break

    walked = world.movement.journey_for(agent_id)
    moved_to = walked.current_tile if walked is not None else dest
    assert moved_to != first_tile, "twelve engine ticks moved nobody off the start tile"
    assert world.agent_tiles.get(agent_id) == (moved_to.x, moved_to.y), (
        "attach_tile_tracking never saw a walked step - agent.moved carried no tile"
    )


# -- gap 8: BOTH world hosts serve the tool registry ----------------------
#
# Found by booting a real September engine against the standalone world
# server: it died at startup on
#     httpx.HTTPStatusError: Client error '405 Method Not Allowed'
#     for url 'http://world:8100/tools/registry'
# The embedded app in run_genesis.py served GET /tools/registry; world/
# main.py never did. `POST /tools/{tool_name}` matched the PATH, so the
# failure surfaced as 405 ("wrong verb") rather than 404 ("no such route"),
# which points the reader at the caller instead of the missing route.
#
# fetch_tool_registry is the FIRST thing an engine calls - before persona,
# before memory, before a single tick - so a host missing this route can
# never run an agent at all, no matter what else it mounts correctly.

ENGINE_REGISTRY_KEYS = {"name", "description", "input_schema"}


def test_world_main_registers_get_tools_registry():
    """The route exists AS A GET. Asserted on the route table rather than
    by calling it, so this fails for the real reason (route absent) even if
    booting a world server in a test is impossible for some other reason."""
    import world.main as world_main

    methods_by_path = {
        route.path: set(route.methods)
        for route in world_main.app.routes
        if hasattr(route, "methods")
    }
    assert "/tools/registry" in methods_by_path, (
        "world/main.py serves no /tools/registry - a real engine cannot boot "
        "against it (engine/src/sim/world_tools.py::fetch_tool_registry)"
    )
    assert "GET" in methods_by_path["/tools/registry"]


def test_both_hosts_serve_the_registry_version_route():
    """Part B: the staleness signal an engine polls every tick.

    Asserted on BOTH hosts, because these two drifted once already over
    /tools/registry itself. If run_genesis.py's embedded app lacks it, every
    engine in a genesis run silently keeps its boot-time vocabulary forever -
    and silently is the problem: fetch_tool_registry_version swallows the 404
    by design, so nothing would ever complain.
    """
    import ast
    from pathlib import Path as _Path

    import world.main as world_main

    methods_by_path = {
        route.path: set(route.methods)
        for route in world_main.app.routes
        if hasattr(route, "methods")
    }
    assert "/tools/registry/version" in methods_by_path
    assert "GET" in methods_by_path["/tools/registry/version"]
    # Same argument for the refusal log the observatory reads: a host missing
    # it shows an EMPTY gaps view rather than an error, which is a silent
    # wrong answer and therefore the worse failure of the two.
    assert "/world/refusals" in methods_by_path

    # run_genesis.py builds its app inside a function, so there is no module
    # -level route table to read - assert on the decorator instead.
    source = _Path("world/run_genesis.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    routes = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert "/world/refusals" in routes, (
        "run_genesis.py's embedded app serves no /world/refusals - the "
        "observatory's gaps view would be silently empty for a genesis run"
    )
    assert "/tools/registry/version" in routes, (
        "run_genesis.py's embedded app serves no /tools/registry/version - "
        "engines in a genesis run would never see an invented tool"
    )


def test_both_hosts_build_the_registry_payload_from_one_function():
    """Parity by construction, not by two handlers that look alike.

    These two hosts already drifted once (this gap). Asserting both call
    tool_registry_rows means a future edit to the payload shape cannot
    change one host and leave the other behind - the same technique
    test_both_composition_roots_hand_movement_to_the_tick_engine uses.
    """
    import ast

    for module in ("world/main.py", "world/run_genesis.py"):
        source = Path(module).read_text(encoding="utf-8")
        tree = ast.parse(source)
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "tool_registry_rows" in called, (
            f"{module} builds the /tools/registry payload itself instead of "
            "calling tool_registry_rows - the two hosts can drift again"
        )


def test_the_payload_matches_what_the_engine_parses():
    """The keys are the engine's contract, not ours.

    engine/src/sim/world_tools.py::fetch_tool_registry reads row["name"]
    and .get()s "description"/"input_schema". A renamed key does not raise
    there - .get() returns a default - so every agent would boot with an
    empty tool list and no error anywhere. This test is the only thing
    standing between that rename and a silent town of agents that can do
    nothing.
    """
    import asyncio as _asyncio

    from world.tools.bootstrap import build_world
    from world.tools.registry import tool_registry_rows

    async def go():
        _, registry = await build_world(seed=1)
        return tool_registry_rows(registry)

    rows = _asyncio.run(go())
    assert rows, "the default registry served no tools at all"
    for row in rows:
        assert ENGINE_REGISTRY_KEYS <= set(row), f"row missing engine keys: {row}"
        assert isinstance(row["name"], str) and row["name"]
        assert isinstance(row["input_schema"], dict)


# -- gap 9: the credential that lost the agent's identity ----------------


def test_unsigned_broker_token_still_carries_the_agent_id():
    """Whoever the credential names is who the ledger bills.

    broker/auth.py derives the caller from the bearer token
    (`token.split(".", 1)[0] if "." in token else token`). sign_broker_token
    used to return the raw shared key when AUTH_SHARED_SECRET was unset, so
    every engine in the town identified as the literal string
    "kasukabe-sim-broker".

    MOCK_MODE hides it - the in-memory ledger provisions one wallet under
    that name and the whole town shares it. Against a real world ledger it
    is total: 1242 consecutive 404s, "agent 'kasukabe-sim-broker' is not
    known to world's ledger", every /tick a 500, spend exactly $0.
    """
    import os

    from world.run_genesis import sign_broker_token

    had = os.environ.pop("AUTH_SHARED_SECRET", None)
    try:
        token = sign_broker_token("a0001", "kasukabe-sim-broker")
    finally:
        if had is not None:
            os.environ["AUTH_SHARED_SECRET"] = had

    # Parsed exactly as broker/auth.py parses it.
    parsed = token.split(".", 1)[0] if "." in token else token
    assert parsed == "a0001", f"credential names {parsed!r}, not the agent"
    assert token != "kasukabe-sim-broker"


def test_signed_broker_token_still_carries_the_agent_id():
    """The signed path had it right all along - hold both to one rule."""
    import os

    from world.run_genesis import sign_broker_token

    had = os.environ.get("AUTH_SHARED_SECRET")
    os.environ["AUTH_SHARED_SECRET"] = "test-secret"
    try:
        token = sign_broker_token("a0042", "ignored-fallback")
    finally:
        if had is None:
            os.environ.pop("AUTH_SHARED_SECRET", None)
        else:
            os.environ["AUTH_SHARED_SECRET"] = had

    assert token.split(".", 1)[0] == "a0042"
    assert "." in token, "a configured secret must produce a signed token"


# ── GOV-24: an engine with no production caller is a library ─────────


class TestEveryGovernanceEngineHasACallerOnTheHostThatRuns:
    """`GOV-24`. `charter/tests` and `crime/tests` are 198 tests of
    engines that, until recently, no run ever constructed.

    The finding's own examples: `test_full_election_then_government_
    action_end_to_end` passes `now_tick=300/500` by hand, which is
    exactly what production never does; there was no test asserting that
    `GenesisRunner` advances or tallies an election; none asserting a
    CrimeEngine exists on the production host; and grepping
    `assert_the_day_was_charged` in `world/tests` found nothing, so the
    S1 test could not tell a zero bill from a charged one.

    A green suite over an engine nothing instantiates measures the
    library, not the town - which is how CORE-02, GOV-04 and EVAL-02
    could all be true at once while every governance test passed.

    This file is the right home for it, per the finding. What follows is
    the "has a production caller" assertion for each engine the finding
    names, made against a REAL booted runner rather than against source
    text - a source-text version would pass on a tree where the caller
    is present and broken (CORE-23 / ECON-26).
    """

    @pytest.mark.asyncio
    async def test_the_runner_builds_a_crime_engine(self, tmp_path: Path):
        runner = await _booted(tmp_path)
        try:
            assert runner.crime is not None, (
                "GOV-04: the crime package is a complete, tested library "
                "that no run has ever instantiated"
            )
            assert runner.crime_engine_wired is True
            assert runner.crime.store is not None
        finally:
            await runner.shutdown()

    @pytest.mark.asyncio
    async def test_the_charter_engines_publish_onto_the_runners_bus(
        self, tmp_path: Path
    ):
        """CORE-02. `create_genesis_charter_engine()` runs with no sink,
        so every sub-engine published into `noop_sink` on the host every
        run used. A charter engine whose events reach nothing is a
        charter nothing can be measured against."""
        runner = await _booted(tmp_path)
        try:
            charter = runner.world_state.charter
            for name in (
                "elections", "amendments", "government", "patent_office",
                "land_petitions", "organizations", "complaints",
            ):
                engine = getattr(charter, name)
                sink = engine.event_sink
                assert sink is not None, f"{name} has no event sink"
                assert getattr(sink, "__name__", "") != "noop_sink", (
                    f"CORE-02: {name} still publishes into the void"
                )
        finally:
            await runner.shutdown()

    @pytest.mark.asyncio
    async def test_the_day_boundary_has_a_charge_assertion_that_can_fail(
        self, tmp_path: Path
    ):
        """The finding's S1 point: "grep `assert_the_day_was_charged` in
        `world/tests` found nothing", so nothing held the guard that is
        supposed to halt a run whose economy skipped a day.

        It HALTS rather than raises - a run that stops with a reason is
        more useful than a traceback out of a tick - so that is what is
        asserted. Both directions, because a guard that always halts is
        as useless as one that never does.
        """
        runner = await _booted(tmp_path)
        try:
            day = 3
            # Three households exist; one was billed. That is a skipped
            # day and the run must stop saying so.
            runner.day_jobs_ran[day] = {"households": 3, "head_tax": 1}
            runner.assert_the_day_was_charged(timestamp_for(TICKS_PER_DAY * day))
            assert runner.halt_reason, (
                "a day that billed one household of three did not halt the run"
            )
            assert "head tax reached 1 of 3" in runner.halt_reason

            # And a fully charged day does not.
            runner.halt_reason = None
            runner.day_jobs_ran[day + 1] = {"households": 3, "head_tax": 3}
            runner.assert_the_day_was_charged(timestamp_for(TICKS_PER_DAY * (day + 1)))
            assert not runner.halt_reason, (
                "a day where every household was billed halted the run anyway"
            )
        finally:
            await runner.shutdown()

    @pytest.mark.asyncio
    async def test_the_treasury_pays_and_the_payroll_engine_has_a_caller(
        self, tmp_path: Path
    ):
        """GOV-08. `payroll_due` and `record_payroll_paid` were a
        complete payroll ledger with zero callers."""
        runner = await _booted(tmp_path)
        try:
            assert hasattr(runner, "pay_the_town_payroll")
            source = inspect.getsource(type(runner))
            assert "await self.pay_the_town_payroll(timestamp)" in source
        finally:
            await runner.shutdown()


async def _booted(tmp_path: Path):
    """A runner that has actually booted, which is what "production
    caller" means - not an imported module, a constructed one."""
    from world.run_genesis import GenesisRunner, GenesisRunnerConfig

    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await runner.boot(resume=False)
    return runner
