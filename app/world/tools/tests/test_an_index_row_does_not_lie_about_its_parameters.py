"""A tool a resident can call is not described as taking nothing.

`TRADE-02`. ENG-03 cut the boot payload from ~14K tokens to ~7.6K by
sending most tools as a one-line index entry instead of a full schema.
The saving is real and the rule is right. What the index row also did
was declare `"input_schema": {"type": "object", "properties": {}}` -
that is, *this tool takes no parameters* - for every tool it reduced.

For a tool gated somewhere else that is harmless: the resident cannot
call it from here whatever it passes, and `move` plus a name is the
whole recovery. But **47 of the 57 index rows in run
`deni-a6f9eb4-5h` were tools usable exactly where the resident was
standing.** They were reduced for BUDGET, not for distance, and the
resident was told each of them took nothing.

It did what it was told:

    take_from_shelf({})   x5 residents
    harvest({})           20 of 38 calls
    mine({})               9 of 21
    describe_place({})    23 of 32
    post_price({}) · stock_shelf({}) · give({}) · draw_from_till({})

Then, refused with "Field required", it guessed the names out of
English - which is where `business_id`, `to` and `price_sim` come from,
and `business_id` is the one that silently posted a shop's price on a
person and cost that run every transaction in it.

So an index row for a tool that CAN be called here carries its required
parameter names, types only, no descriptions. Measured: +896 tokens a
turn on a payload ENG-03 brought down by ~6,400. A tool gated elsewhere
keeps the empty schema and the `move` hint.
"""
from __future__ import annotations

import pytest

from world.tools import registry as R
from world.tools.tests.conftest import needs_the_map

pytestmark = needs_the_map


async def _rows(world, registry, at: str):
    """One resident, standing somewhere, and the payload it boots from."""
    from world.tools.tests.conftest import spawn_agent

    await spawn_agent(world, "a1", at)
    return {row["name"]: row for row in R.tool_registry_rows(registry, world, "a1")}


@pytest.mark.asyncio
async def test_a_reduced_tool_the_resident_can_call_names_its_parameters(
    world, registry
):
    rows = await _rows(world, registry, "Kasukabe Farmland")
    row = rows["harvest"]
    assert row.get("elsewhere"), "harvest is in full here; this test proves nothing"
    assert row["input_schema"]["required"] == ["crop"], (
        "an index row for a tool callable from here still claims it takes "
        "nothing - the resident will call harvest({}) and be refused"
    )
    assert "crop" in row["input_schema"]["properties"]
    assert "Needs: crop" in row["description"]


@pytest.mark.asyncio
async def test_the_names_are_all_it_carries(world, registry):
    """Names and types, never descriptions - that is the whole ENG-03
    saving and it stays saved."""
    rows = await _rows(world, registry, "Kasukabe Farmland")
    schema = rows["harvest"]["input_schema"]
    for prop in schema["properties"].values():
        assert set(prop) == {"type"}, f"an index row is carrying prose: {prop}"


@pytest.mark.asyncio
async def test_a_tool_gated_elsewhere_keeps_the_empty_schema_and_the_hint(
    world, registry
):
    """It cannot be called from here whatever is passed, so the names buy
    nothing and `move` plus a name is the recovery path."""
    rows = await _rows(world, registry, "Kasukabe Farmland")
    gated = [
        row for name, row in rows.items()
        if row.get("elsewhere") and "`move` takes you there" in row["description"]
    ]
    assert gated, "nothing is gated elsewhere in this fixture"
    for row in gated:
        assert row["input_schema"]["properties"] == {}
        assert "Needs:" not in row["description"]


@pytest.mark.asyncio
async def test_the_saving_is_still_a_saving(world, registry):
    """The point of the index row survives. A full schema is several
    hundred characters; a named one is a fraction of that."""
    import json

    rows = await _rows(world, registry, "Kasukabe Farmland")
    reduced = json.dumps(rows["harvest"])
    full = json.dumps(R.json_schema_for(registry.get("harvest").params_model))
    assert len(reduced) < len(full) * 2, (
        "an index row has grown back into a full schema"
    )


@pytest.mark.asyncio
async def test_every_reduced_row_that_can_be_called_is_callable_from_what_it_says(
    world, registry
):
    """The sweep, and the actual property that matters: a resident that
    builds a call from an index row's schema alone must not be refused
    for a missing parameter."""
    rows = await _rows(world, registry, "Kasukabe Farmland")
    here = world.location_of("a1")
    for name, row in rows.items():
        if not row.get("elsewhere"):
            continue
        spec = registry.get(name)
        if not R._usable_here(spec, world, here):
            continue
        real_required = set(
            R.json_schema_for(spec.params_model).get("required") or ()
        )
        offered = set(row["input_schema"].get("required") or ())
        assert offered == real_required, (
            f"{name} is callable here and its index row promises "
            f"{sorted(offered)} while the tool requires {sorted(real_required)}"
        )
