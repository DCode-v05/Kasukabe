"""Every model call re-reads the tool payload, so it must carry physics, not prose.

Kimi K2.5 on Bedrock has no prompt caching. Every call pays full price for
every token it re-reads, and run 28's complete trace analysis found that
input was **95% of the bill** ($9.54 of $10.04). The tool payload is the
largest single block: for one `genesis_20` resident, 43,499 characters -
about 10,900 tokens - on every call, of a median 19,092.

Two things in it bought nothing:

1. **Developer docstrings served as schema descriptions.** `model_json_schema()`
   copies a pydantic model's docstring into its JSON schema. `ToolStep` and
   `StepCondition` carry long engineering notes (PROG-02's rationale, the
   ceiling argument), and both are nested in `toolify` and `do_in_order`, so
   those two rows were 5,172 and 4,412 characters. A field's own
   `description=` is physics a resident needs; a class docstring is a note to
   the next engineer.

2. **A budget spent alphabetically.** ENG-03 serves 24 full schemas: tools
   gated to here, then the always-on verbs, then "whatever else fits, stably
   ordered" - ordered by NAME. So `arrest`, `admit_to_org`, `ask_for_a_child`,
   `auction_land`, `build_structure` and `come_of_age` (1,337 characters) arrived
   in full to a farmhand, while `trade`, `transfer` and `give` - verbs the town
   calls every run - arrived as one line.
"""

from __future__ import annotations

import json

import pytest

from world.tools.registry import FULL_SCHEMA_BUDGET, tool_registry_rows
from world.tools.tests.conftest import spawn_agent

FIELD = "Kasukabe Farmland"


async def _rows(world_and_registry) -> dict[str, dict]:
    world, registry = world_and_registry
    await spawn_agent(world, "n01", FIELD)
    return {row["name"]: row for row in tool_registry_rows(registry, world, "n01")}


@pytest.mark.asyncio
async def test_a_class_docstring_is_not_served_to_the_model(world_and_registry):
    rows = await _rows(world_and_registry)

    served = json.dumps(rows["do_in_order"]["input_schema"])

    assert "PROG-02 added three optional fields" not in served, "ToolStep's docstring is billed on every call"
    assert "A condition can only see what a step already paid" not in served, (
        "StepCondition's docstring is billed on every call"
    )


@pytest.mark.asyncio
async def test_the_shape_and_the_field_descriptions_survive(world_and_registry):
    """Stripping prose must not strip what a resident needs to form a call.

    Read off `do_in_order`: since 2026-09-21 `toolify` is a one-line entry
    until a resident has authored something (X8), and its steps are
    `do_in_order`'s steps - the same ToolStep - which is served in full.
    """
    rows = await _rows(world_and_registry)

    step = rows["do_in_order"]["input_schema"]["$defs"]["ToolStep"]
    assert {"tool_name", "when", "repeat", "bind"} <= set(step["properties"])
    assert "True to also give up the journey" in json.dumps(rows["rest"]["input_schema"]), (
        "a field's own description is physics, and it was stripped with the docstrings"
    )


@pytest.mark.asyncio
async def test_the_full_schema_budget_goes_to_verbs_the_town_uses(world_and_registry):
    rows = await _rows(world_and_registry)
    full = {name for name, row in rows.items() if not row.get("elsewhere")}

    assert len(full) <= FULL_SCHEMA_BUDGET
    for used in ("trade", "transfer", "give"):
        if used in rows:
            assert used in full, f"{used} is called every run and arrived as a one-line index row"
    for rare in ("come_of_age", "ask_for_a_child", "auction_land", "admit_to_org"):
        assert rare not in full, f"{rare} took a full-schema slot from a verb the town uses"
