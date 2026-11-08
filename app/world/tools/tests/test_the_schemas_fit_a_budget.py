"""ENG-03: tool schemas are two thirds of every turn's bill.

76 tools is ~53,000 chars, ~14K tokens by the engine's estimator. Run 23's
per-turn input was 15-20K. Persona is ~1.3K and perception ~0.4K, so the
schemas were the bill.

The location gate alone did almost nothing about it: most tools carry no
fixed gate, so they count as usable everywhere and every one came back in
full. Measured before this change: 59,310 chars against 50,389 - a 16%
saving with ZERO index lines.

The rule is now a budget, ordered by what matters where the resident is
standing.
"""
from __future__ import annotations

import json

import pytest

from world.tools.registry import (
    ALWAYS_IN_FULL,
    FULL_SCHEMA_BUDGET,
    tool_registry_rows,
)


@pytest.mark.asyncio
async def test_a_resident_is_served_far_fewer_full_schemas(world_and_registry):
    from world.tools.tests.conftest import spawn_agent

    world, registry = world_and_registry
    await spawn_agent(world, "n01", "Kasukabe Farmland")

    everything = tool_registry_rows(registry)
    here = tool_registry_rows(registry, world, "n01")

    full = [r for r in here if not r.get("elsewhere")]
    assert len(full) <= FULL_SCHEMA_BUDGET, (
        f"{len(full)} full schemas served against a budget of "
        f"{FULL_SCHEMA_BUDGET}; the schemas are two thirds of every turn's "
        "input bill (ENG-03)"
    )
    from world.tools.registry import CONVERSATION_ENABLED_TOOLS

    talking = bool(world.conversation_partners("n01"))
    callable_here = [
        spec
        for spec in registry.tools.values()
        if registry.visible_to(spec, world, "n01")
        and (talking or spec.name not in CONVERSATION_ENABLED_TOOLS)
    ]
    assert len(here) == len(callable_here), (
        "a reduced payload must still list every verb the resident may call"
    )

    before, after = len(json.dumps(everything)), len(json.dumps(here))
    assert after < before * 0.7, (
        f"{after:,} chars against {before:,} - only {100 - after*100//before}% "
        "saved. The location gate alone managed 16% and zero index lines."
    )


@pytest.mark.asyncio
async def test_the_verbs_a_resident_always_needs_stay_in_full(world_and_registry):
    """Reducing one of these costs a turn: the resident has to spend a call
    on `describe_primitives` before it can do the obvious thing."""
    from world.tools.tests.conftest import spawn_agent

    world, registry = world_and_registry
    await spawn_agent(world, "n01", "Kasukabe Farmland")
    rows = {r["name"]: r for r in tool_registry_rows(registry, world, "n01")}

    for verb in ("look_around", "move", "say", "eat", "keep_doing"):
        assert verb in rows, f"{verb} is not served at all"
        assert not rows[verb].get("elsewhere"), (
            f"{verb} was reduced to an index line; it is a verb a resident "
            "needs wherever it is standing"
        )


@pytest.mark.asyncio
async def test_a_tool_gated_to_here_beats_one_that_is_not(world_and_registry):
    """The reason to be standing somewhere outranks a generic verb."""
    from world.tools.registry import _gated_to_here
    from world.tools.tests.conftest import spawn_agent

    world, registry = world_and_registry
    await spawn_agent(world, "n01", "Kasukabe Farmland")
    here = world.location_of("n01")

    gated = [s for s in registry.tools.values() if _gated_to_here(s, world, here)]
    if not gated:
        pytest.skip("nothing is gated to the farmland in this fixture")

    rows = {r["name"]: r for r in tool_registry_rows(registry, world, "n01")}
    for spec in gated:
        assert not rows[spec.name].get("elsewhere"), (
            f"{spec.name} is gated to this exact place and was still reduced"
        )


def test_the_budget_is_a_knob_not_a_magic_number():
    import os

    assert FULL_SCHEMA_BUDGET == int(os.environ.get("KASUKABE_FULL_SCHEMA_BUDGET", "24"))
    assert ALWAYS_IN_FULL, "the always-on set is empty; every verb is now negotiable"


@pytest.mark.asyncio
async def test_an_anonymous_caller_still_gets_everything(world_and_registry):
    """The observatory, the tests, a boot before anybody has a location."""
    world, registry = world_and_registry
    rows = tool_registry_rows(registry)
    assert all(not r.get("elsewhere") for r in rows)
