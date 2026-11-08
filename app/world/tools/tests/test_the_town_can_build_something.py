"""GROW-02 / GROW-04: two of the four reasons nobody can build anything.

Four findings, one story. Nothing creates a plot (GROW-01), nothing issues
a builder's licence (GROW-02), `complete_construction` has zero callers so
nothing ever finishes (GROW-04), and a finished building satisfies no
location gate so you cannot stand in it (GROW-03).

This file closes the two that are pure wiring:

  * `charter/government.py::issue_builder_license` has been complete since
    genesis and the only other mention of its name in the tree is a
    docstring. `build_structure` requires the licence, so the gate refused
    everyone - including the town's own carpenter - for 23 runs.
  * `complete_construction` and `buildings_due_for_completion` are both
    written and tested. The second's docstring describes the tick loop that
    calls them. That loop was never written.

GROW-01 and GROW-03 are still open, so this does NOT claim the town can
build. It claims two of the four locks are off.
"""
from __future__ import annotations

import pytest

from world.tools.tests.conftest import spawn_agent


@pytest.mark.asyncio
async def test_the_licence_tool_exists_and_is_registered(world_and_registry):
    _, registry = world_and_registry
    assert "issue_builder_license" in registry.tools, (
        "no tool issues a builder's licence, so `build_structure`'s gate "
        "refuses every resident in the town (GROW-02)"
    )


@pytest.mark.asyncio
async def test_build_structure_still_demands_one(world_and_registry):
    """The gate must stay a gate - this is not 'anyone may build'."""
    _, registry = world_and_registry
    spec = registry.tools["build_structure"]
    required = spec.actor_requirements.required_license_types
    assert "builder_license" in required, (
        "build_structure no longer requires a licence; the fix removed the "
        "rule instead of making it satisfiable"
    )


@pytest.mark.asyncio
async def test_the_completion_driver_exists_and_is_called(tmp_path):
    """GROW-04. A filter and a completer, both written, neither driven."""
    import ast
    import inspect
    import textwrap

    from world.run_genesis import GenesisRunner

    src = textwrap.dedent(inspect.getsource(GenesisRunner.finish_what_the_town_built))
    called = {
        node.func.id
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"buildings_due_for_completion", "complete_construction"} <= called

    tick = textwrap.dedent(inspect.getsource(GenesisRunner.run_life_ticks))
    attrs = {
        node.func.attr
        for node in ast.walk(ast.parse(tick))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "finish_what_the_town_built" in attrs, (
        "nothing on the tick path finishes a building, so one that is due "
        "stays UNDER_CONSTRUCTION for the rest of the run (GROW-04)"
    )


@pytest.mark.asyncio
async def test_a_due_building_actually_completes(tmp_path):
    """The behaviour, not the wiring: put a due building in the world and
    tick, and it must flip to COMPLETE."""
    from world.core.clock import timestamp_for
    from world.run_genesis import GenesisRunner, GenesisRunnerConfig
    from world.tools.models import BuildingStatus

    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "c", world_mode="embedded",
        world_port=0, budget_cap_cents=40_000,
    ))
    await runner.boot()
    world = runner.world_state
    builder = next(iter(world.agents))

    from world.tools.models import Building

    world.buildings["b1"] = Building(
        building_id="b1",
        plot_id="p1",
        purpose="smithy",
        builder_agent_id=builder,
        footprint=frozenset({(1, 1), (1, 2)}),
        timber_used=10,
        metal_used=2,
        started_tick=0,
        completion_tick=0,
        status=BuildingStatus.UNDER_CONSTRUCTION,
    )

    await runner.finish_what_the_town_built(timestamp_for(world.current_tick))

    assert world.buildings["b1"].status is BuildingStatus.COMPLETE, (
        "a building whose completion tick has passed is still UNDER_"
        "CONSTRUCTION - nothing in 23 runs has ever finished (GROW-04)"
    )
    assert runner.buildings_completed == 1


@pytest.mark.asyncio
async def test_a_building_not_yet_due_is_left_alone(tmp_path):
    from world.core.clock import timestamp_for
    from world.run_genesis import GenesisRunner, GenesisRunnerConfig
    from world.tools.models import Building, BuildingStatus

    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "c", world_mode="embedded",
        world_port=0, budget_cap_cents=40_000,
    ))
    await runner.boot()
    world = runner.world_state
    world.buildings["b2"] = Building(
        building_id="b2",
        plot_id="p2",
        purpose="smithy",
        builder_agent_id=next(iter(world.agents)),
        footprint=frozenset({(3, 3)}),
        timber_used=10,
        metal_used=2,
        started_tick=0,
        completion_tick=99_999,
        status=BuildingStatus.UNDER_CONSTRUCTION,
    )
    await runner.finish_what_the_town_built(timestamp_for(world.current_tick))
    assert world.buildings["b2"].status is BuildingStatus.UNDER_CONSTRUCTION
    assert runner.buildings_completed == 0


@pytest.mark.asyncio
async def test_a_seat_holder_can_grant_ground(world_and_registry):
    """GROW-01. Nothing in this world could create a Plot.

    The only construction of one outside the tests is `plot_from_snapshot`,
    which RESTORES a plot that already existed - so on a fresh run
    `world.plots` was empty forever and `build_structure`'s very first
    check refused every resident before any other gate was reached.
    Construction.py's own docstring admits it: "tests and bootstrap seed
    plots directly".
    """
    world, registry = world_and_registry
    assert "grant_plot" in registry.tools, (
        "nothing creates a plot, so build_structure refuses everyone at its "
        "first check (GROW-01)"
    )
    assert not world.plots, "the fixture already has plots; this proves nothing"


@pytest.mark.asyncio
async def test_a_finished_building_becomes_somewhere_you_can_work(world_and_registry):
    """GROW-03. A completed smithy must satisfy the `smithy` gate.

    `build_structure` takes a purpose and `complete_construction` flips the
    building to COMPLETE - and nothing made standing there mean anything.
    A finished smithy was a database row: the resident who built it still
    could not forge in it.
    """
    from world.tools.models import Building, BuildingStatus, Plot
    from world.tools.place_aliases import satisfies_gate, satisfies_gate_in

    world, _ = world_and_registry
    await spawn_agent(world, "n01", "Kasukabe Farmland")

    world.plots["p9"] = Plot(
        plot_id="p9",
        owner_agent_id="n01",
        location_id="Kasukabe Farmland",
        footprint=frozenset({(5, 5)}),
        permitted=True,
    )
    world.buildings["b9"] = Building(
        building_id="b9",
        plot_id="p9",
        purpose="smithy",
        builder_agent_id="n01",
        footprint=frozenset({(5, 5)}),
        timber_used=10,
        metal_used=4,
        started_tick=0,
        completion_tick=0,
        status=BuildingStatus.COMPLETE,
    )

    assert not satisfies_gate("Kasukabe Farmland", "smithy"), (
        "the pure gate should still say no - being a smithy is a property of "
        "what stands on the ground, not of the string"
    )
    assert satisfies_gate_in(world, "Kasukabe Farmland", "smithy"), (
        "the town built a smithy and standing in it does not satisfy the "
        "smithy gate, so it is a row rather than a place (GROW-03)"
    )


@pytest.mark.asyncio
async def test_a_half_built_building_provides_nothing_yet(world_and_registry):
    """UNDER_CONSTRUCTION is not a workshop."""
    from world.tools.models import Building, BuildingStatus, Plot
    from world.tools.place_aliases import satisfies_gate_in

    world, _ = world_and_registry
    await spawn_agent(world, "n01", "Kasukabe Farmland")
    world.plots["p8"] = Plot(
        plot_id="p8", owner_agent_id="n01", location_id="Kasukabe Farmland",
        footprint=frozenset({(6, 6)}), permitted=True,
    )
    world.buildings["b8"] = Building(
        building_id="b8", plot_id="p8", purpose="smithy", builder_agent_id="n01",
        footprint=frozenset({(6, 6)}), timber_used=10, metal_used=4,
        started_tick=0, completion_tick=999,
        status=BuildingStatus.UNDER_CONSTRUCTION,
    )
    assert not satisfies_gate_in(world, "Kasukabe Farmland", "smithy")


@pytest.mark.asyncio
async def test_a_building_somewhere_else_does_not_help(world_and_registry):
    """A smithy across town is not the ground you are standing on."""
    from world.tools.models import Building, BuildingStatus, Plot
    from world.tools.place_aliases import satisfies_gate_in

    world, _ = world_and_registry
    await spawn_agent(world, "n01", "Kasukabe Forest")
    world.plots["p7"] = Plot(
        plot_id="p7", owner_agent_id="n01", location_id="Kasukabe Farmland",
        footprint=frozenset({(7, 7)}), permitted=True,
    )
    world.buildings["b7"] = Building(
        building_id="b7", plot_id="p7", purpose="smithy", builder_agent_id="n01",
        footprint=frozenset({(7, 7)}), timber_used=10, metal_used=4,
        started_tick=0, completion_tick=0, status=BuildingStatus.COMPLETE,
    )
    assert not satisfies_gate_in(world, "Kasukabe Forest", "smithy")
