"""Can an agent actually reach and use the authoring tools?

A tool that exists but never arrives in front of an agent is decoration. Three
things have to be true, and each was false at some point during the build:

1. `toolify`, `skillify`, `describe_primitives` and `quote_action_cost` are in
   the payload `GET /tools/registry` serves - the list every engine boots from.
2. What the agent reads about a tool says what it costs, because acting costs
   money now and nobody told it.
3. Something eventually suggests authoring, driven by what the agent has been
   doing rather than by a standing instruction nobody acts on.
"""

import pytest

from world.tools.bootstrap import build_world
from world.tools.locations import farmland_location
from world.tools.registry import tool_registry_rows
from world.tools.state import AUTHORING_HINT_REPEATS
from world.tools.tests.conftest import spawn_agent

FIELD = farmland_location("wheat")

AUTHORING_TOOLS = ("toolify", "skillify", "describe_primitives", "quote_action_cost")


async def _world():
    world, registry = await build_world(seed=61)
    await spawn_agent(world, "ada", FIELD, starting_sim=100_000)
    return world, registry


@pytest.mark.asyncio
async def test_every_authoring_tool_reaches_the_engine_payload() -> None:
    """`tool_registry_rows` is what both world hosts serve and what
    `build_world_tool_registry` turns into the agent's tool list. A tool absent
    here is a tool no agent can ever call, however well it works."""
    _, registry = await _world()
    served = {row["name"] for row in tool_registry_rows(registry)}

    missing = [t for t in AUTHORING_TOOLS if t not in served]
    assert not missing, f"agents can never call: {missing}"


@pytest.mark.asyncio
async def test_what_the_agent_reads_about_a_tool_says_what_it_costs() -> None:
    """The tool list is the only place an agent reliably looks. A price it has
    to go and ask for is a price it will not consult before acting."""
    _, registry = await _world()
    rows = {row["name"]: row for row in tool_registry_rows(registry)}

    assert "Cost:" in rows["harvest"]["description"]
    assert "sim-currency" in rows["harvest"]["description"]
    assert "0 sim-currency" in rows["toolify"]["description"]
    assert "0 sim-currency" in rows["skillify"]["description"]


@pytest.mark.asyncio
async def test_the_cost_sentence_stays_a_sentence() -> None:
    """`annotate_tool_description` splices the RULE's description into what the
    agent reads, so a verbose rule becomes a paragraph in every prompt, times
    46 tools, times 314 agents, every tick. This caught exactly that."""
    _, registry = await _world()
    for row in tool_registry_rows(registry):
        cost_sentence = row["description"].split("Cost:")[-1]
        assert len(cost_sentence) < 140, (
            f"{row['name']}'s price note is {len(cost_sentence)} chars - rule "
            "descriptions are agent-facing, keep them to a clause"
        )


@pytest.mark.asyncio
async def test_no_hint_until_the_agent_has_actually_repeated_something() -> None:
    """Silence is the default. A standing "you may invent things" in every
    prompt is furniture: no reason attached, so nothing to act on."""
    world, registry = await _world()
    assert world.authoring_hint("ada") is None

    await registry.execute(world, "ada", "harvest", {"crop": "wheat", "quantity": 1})
    assert world.authoring_hint("ada") is None, "one act is not a habit"


@pytest.mark.asyncio
async def test_a_repeated_job_earns_a_hint() -> None:
    world, registry = await _world()
    for _ in range(AUTHORING_HINT_REPEATS):
        await registry.execute(world, "ada", "harvest", {"crop": "wheat", "quantity": 1})

    hint = world.authoring_hint("ada")
    assert hint is not None
    assert "harvest" in hint
    assert "skillify" in hint and "toolify" in hint
    assert "steps" not in hint.lower().split("steps you already have")[0].replace(
        "steps you already have", "")
    for invented in ("Harvest Routine", "call it", "name it"):
        assert invented.lower() not in hint.lower()


@pytest.mark.asyncio
async def test_the_hint_goes_away_once_the_agent_has_written_it_down() -> None:
    """It is a prompt to act, not a nag. Acting on it must silence it."""
    world, registry = await _world()
    for _ in range(AUTHORING_HINT_REPEATS):
        await registry.execute(world, "ada", "harvest", {"crop": "wheat", "quantity": 1})
    assert world.authoring_hint("ada") is not None

    await registry.execute(
        world, "ada", "skillify",
        {
            "name": "working the field",
            "steps": [{"order": 1, "kind": "tool", "ref": "harvest", "note": ""}],
        },
    )
    assert world.authoring_hint("ada") is None, "the tool is covered now"


@pytest.mark.asyncio
async def test_an_agent_invents_a_tool_and_calls_it_the_same_tick() -> None:
    """The whole point of toolify, end to end.

    `define_tool` cannot do this: it gates on standing in the patent office,
    charges 1,000 sim, and files the tool unapproved, so the inventor cannot
    use its own invention until a seat-holder acts.
    """
    world, registry = await _world()
    before_version = registry.version
    before_balance = world.ledger.sim_balance("ada")

    result = await registry.execute(
        world, "ada", "toolify",
        {
            "name": "gather_and_look",
            "description": "take the wheat, then see who noticed",
            "param_schema": {},
            "steps": [
                {"tool_name": "harvest", "params": {"crop": "wheat", "quantity": 1}},
                {"tool_name": "look_around", "params": {}},
            ],
        },
    )

    assert result.result["callable_now"] is True
    assert world.ledger.sim_balance("ada") == before_balance, "no filing fee"
    assert registry.version > before_version, "engines will notice"

    await registry.execute(world, "ada", "gather_and_look", {})
    assert world.inventory.quantity("ada", "wheat") > 0


@pytest.mark.asyncio
async def test_an_invented_tool_still_pays_for_every_step_it_takes() -> None:
    """Bundling steps saves an agent THINKING, not labour. If a composite were
    charged once for the wrapper, inventing tools would be a way to make the
    world cheaper rather than a way to decide less often."""
    world, registry = await _world()
    await registry.execute(
        world, "ada", "toolify",
        {
            "name": "double_harvest", "description": "twice over", "param_schema": {},
            "steps": [
                {"tool_name": "harvest", "params": {"crop": "wheat", "quantity": 1}},
                {"tool_name": "harvest", "params": {"crop": "wheat", "quantity": 1}},
            ],
        },
    )

    before = world.ledger.sim_balance("ada")
    await registry.execute(world, "ada", "double_harvest", {})
    spent = before - world.ledger.sim_balance("ada")

    one = world.ledger.sim_balance("ada")
    await registry.execute(world, "ada", "harvest", {"crop": "wheat", "quantity": 1})
    single = one - world.ledger.sim_balance("ada")

    assert spent >= single * 2, "each inner step is charged, not just the wrapper"


@pytest.mark.asyncio
async def test_an_agent_can_ask_what_one_specific_call_would_cost() -> None:
    """`describe_primitives` gives the minimum; this prices the actual call,
    params and the agent's own discount included."""
    world, registry = await _world()

    result = await registry.execute(
        world, "ada", "quote_action_cost",
        {"tool_name": "harvest", "call_params": {"crop": "wheat", "quantity": 20}},
    )
    assert result.result["sim_cost"] > 0
    assert result.result["affordable"] is True
