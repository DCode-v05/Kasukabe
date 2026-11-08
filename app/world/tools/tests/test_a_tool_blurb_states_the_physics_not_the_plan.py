"""TOOL-26: a tool's description states the physics, never the strategy.

The tool-spec Addendum's rule 2 - quoted in this codebase at
`world/ledger/head_tax.py`, `world/life/soul_seed.py` and
`world/retrieval_depth.py` as "told the prices and the rules, never the
strategy" - is what keeps a resident's choices its own. Four registered
descriptions broke it, three of them named by the review:

  * `list_places` closed with a shopping list of errands to run;
  * `describe_primitives` told the resident at what point in its own
    reasoning to call it;
  * `eat` claimed to be the only way food is ever consumed - a nudge, and
    a false one: `cook` consumes food to make other food and a household's
    upkeep consumes it daily with no tool call at all;
  * `rest` told the resident what to want out of a journey. Not in the
    finding; found by sweeping every registered description for the same
    shape, and fixed with them.

This costs more than tidiness. A description is searchable text that
`describe_primitives(contains=...)` matches on, it is re-served to every
engine in every boot payload at the town's expense, and an instruction
inside it is the world telling a resident what to do in a system whose
whole claim is that residents decide.

The sweep at the bottom is the durable half: naming the four fixed strings
guards the four, and a new tool written next month with "Use this to..."
in its blurb would sail past that and fail the sweep.
"""

from __future__ import annotations

import re

import pytest

from world.tools.registry import tool_registry_rows
from world.tools.tests.conftest import spawn_agent

STRATEGY = re.compile(
    r"\b[Uu]se (?:this|it|these)\b|\bYou should\b|\bThe only way\b|\bbefore composing\b"
)


@pytest.mark.asyncio
async def test_the_four_blurbs_keep_the_fact_and_drop_the_plan(registry) -> None:
    kept = {
        "list_places": "List the places in Kasukabe you could travel to",
        "describe_primitives": "List the actions you can actually take",
        "eat": "Eat food you are holding",
        "rest": "If you were walking somewhere you keep walking",
    }
    for name, physics in kept.items():
        description = registry.tools[name].description
        assert physics in description, (
            f"{name} lost the fact along with the instruction; the blurb has "
            "to still say what the tool DOES"
        )
        found = STRATEGY.search(description)
        assert found is None, (
            f"{name} still instructs the resident: {found.group(0)!r} in "
            f"{description!r}"
        )


@pytest.mark.asyncio
async def test_no_registered_tool_tells_a_resident_what_to_do(registry) -> None:
    """The sweep. Every tool in the town, including the meta-tools and
    anything a later hand registers in bootstrap.py."""
    offenders = {
        name: STRATEGY.search(spec.description).group(0)
        for name, spec in registry.tools.items()
        if STRATEGY.search(spec.description)
    }
    assert not offenders, (
        f"{len(offenders)} tool descriptions carry strategy rather than "
        f"physics: {offenders}"
    )


@pytest.mark.asyncio
async def test_the_vocabulary_a_resident_reads_carries_no_strategy(
    world_and_registry,
) -> None:
    """WIRING, half one. `describe_primitives` through `registry.execute` -
    the only path a resident ever reaches it by - re-serves `spec
    .description` verbatim for every tool it lists, so a blurb fixed at
    the spec and re-introduced anywhere between here and the resident
    would still arrive.
    """
    world, registry = world_and_registry
    await spawn_agent(world, "n01", "Kasukabe Farmland")

    outcome = await registry.execute(world, "n01", "describe_primitives", {})

    for row in outcome.result["tools"]:
        found = STRATEGY.search(row["description"])
        assert found is None, (
            f"{row['name']} was read out to a resident with {found.group(0)!r} "
            "in it"
        )


@pytest.mark.asyncio
async def test_the_boot_payload_carries_no_strategy_either(
    world_and_registry,
) -> None:
    """WIRING, half two. The `GET /tools/registry` rows are the OTHER copy
    of every description, built by `tool_registry_rows` and re-sent to
    every engine on every fetch - the expensive one. An index row is
    truncated to its first sentence, so this checks the full-schema rows
    as well as the reduced ones by reading both payload shapes.
    """
    world, registry = world_and_registry
    await spawn_agent(world, "n01", "Kasukabe Farmland")

    for rows in (
        tool_registry_rows(registry),
        tool_registry_rows(registry, world, "n01"),
    ):
        assert rows
        for row in rows:
            found = STRATEGY.search(row["description"])
            assert found is None, (
                f"{row['name']} reaches the engine carrying "
                f"{found.group(0)!r}"
            )
