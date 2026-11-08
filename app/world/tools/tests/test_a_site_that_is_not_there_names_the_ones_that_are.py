"""A `work_site` refusal names the sites that are open.

election30 made seven `work_site` calls and every one failed on an invented
building id - `b_construction`, `whitlock_repair`, ids that read like the
crew's own talk about the job rather than anything the world holds. Each was
told only that the id did not exist. A site id is not something a resident can
look up, so there was nowhere to go from there: nobody worked a site all run,
0 buildings were raised, and 52 successful timber fellings and the planks made
from them went into nothing.

Same rule as `withdraw_item`'s storages and `teach_skill`'s skills - a refusal
teaches or it is noise - and the same shape, because `NotFoundError` already
takes the list.
"""

from __future__ import annotations

import pytest

from world.charter.models import License
from world.tools.errors import NotFoundError
from world.tools.models import BuildingStatus, Plot
from world.tools.primitives.construction import sites_worth_naming
from world.tools.tests.conftest import spawn_agent


async def _open_a_site(world, registry, plot_id: str, builder: str = "builder") -> str:
    """Break ground for real, through `build_structure`.

    Built rather than fabricated: a hand-made Building could drift from what
    the verb actually produces, and then this file would be testing a shape
    the town never makes.
    """
    await spawn_agent(world, builder, plot_id)
    await world.charter.store.put_license(
        License(
            license_id=f"lic-{builder}-{plot_id}",
            license_type="builder_license",
            holder_agent_id=builder,
            fee_paid_sim=500,
            issued_by_agent_id="genesis",
            issued_tick=0,
        )
    )
    world.plots[plot_id] = Plot(
        plot_id=plot_id,
        owner_agent_id=builder,
        location_id=plot_id,
        footprint=frozenset({(0, 0), (1, 0), (0, 1)}),
        permitted=True,
    )
    world.inventory.credit(builder, "timber", 1_000)
    world.inventory.credit(builder, "iron_ore", 1_000)

    result = await registry.execute(
        world,
        builder,
        "build_structure",
        {
            "plot_id": plot_id,
            "footprint": [[0, 0], [1, 0], [0, 1]],
            "purpose": "smithy",
            "metal_kind": "iron",
        },
    )
    return result.result["building_id"]


@pytest.mark.asyncio
async def test_an_invented_site_id_is_told_which_sites_are_open(world, registry) -> None:
    """The election30 case: `whitlock_repair`, which was never a building."""
    site = await _open_a_site(world, registry, "plot-1")
    await spawn_agent(world, "n01", "plot-1")

    with pytest.raises(NotFoundError) as caught:
        await registry.execute(
            world, "n01", "work_site", {"building_id": "whitlock_repair"}
        )

    said = str(caught.value)
    assert site in said, (
        f"a resident that invented a site id was told nothing it could use: {said}"
    )


@pytest.mark.asyncio
async def test_a_finished_building_is_not_offered_as_a_site(world, registry) -> None:
    """Offering one would send the resident into the next refusal."""
    site = await _open_a_site(world, registry, "plot-1")

    assert sites_worth_naming(world) == [site]

    world.buildings[site].status = BuildingStatus.COMPLETE

    assert sites_worth_naming(world) == []


@pytest.mark.asyncio
async def test_a_site_with_no_labour_left_is_not_offered(world, registry) -> None:
    """`work_site` refuses these separately, so naming one is a dead end."""
    site = await _open_a_site(world, registry, "plot-1")
    building = world.buildings[site]
    building.labour_ticks_done = building.labour_ticks_required
    assert building.labour_remaining == 0

    assert sites_worth_naming(world) == []


@pytest.mark.asyncio
async def test_a_town_with_no_open_site_still_refuses_cleanly(world, registry) -> None:
    """The empty case stays a plain refusal rather than a crash - a town
    between jobs is the ordinary state, not an error."""
    await spawn_agent(world, "n02", "plot-1")

    assert sites_worth_naming(world) == []

    with pytest.raises(NotFoundError):
        await registry.execute(
            world, "n02", "work_site", {"building_id": "b_construction"}
        )


@pytest.mark.asyncio
async def test_two_open_sites_are_both_named(world, registry) -> None:
    """A crew choosing between jobs needs the list, not one of them."""
    first = await _open_a_site(world, registry, "plot-1", builder="b1")
    second = await _open_a_site(world, registry, "plot-2", builder="b2")

    assert sites_worth_naming(world) == sorted([first, second])
