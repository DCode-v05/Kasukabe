"""A building the town raised is a place a resident can walk to.

`GROW-16`. `is_real_destination` had a loop over `world.buildings` that
read `getattr(building, "location_id", None)`. `Building`
(`tools/models.py:314`) has no `location_id` - it has `plot_id` and
`footprint`. So the default answered `None` every iteration,
`None == location_id` was never true, and the branch was dead code: the
town could survey a plot, pay for timber and metal, run a construction
countdown to completion, and the result was still not somewhere anybody
could stand.

The three-argument getattr is what hid it, and it sat two lines below a
comment explaining why the line above it must not use one. This is the
same defect `test_no_silent_defaults` exists to catch, in the one shape
that test cannot see: the default is not wrong at the call site, it is
wrong about the MODEL.

The fix is the answer `_a_finished_building_provides` already uses for
the gate question one function up - a building's ground is the plot it
was raised on.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from world.tools.models import Building, BuildingStatus
from world.tools.place_aliases import is_real_destination


class _Plot:
    """Only the two fields the branch reads off a plot."""

    def __init__(self, plot_id: str, location_id: str | None) -> None:
        self.plot_id = plot_id
        self.location_id = location_id


def _building(building_id: str, plot_id: str, status=BuildingStatus.COMPLETE) -> Building:
    return Building(
        building_id=building_id, plot_id=plot_id, builder_agent_id="a1",
        purpose="smithy", footprint=frozenset({(2, 3), (3, 3)}),
        timber_used=4, metal_used=2, started_tick=1, completion_tick=9,
        status=status,
    )


@pytest_asyncio.fixture
async def world():
    """The REAL world the other place_aliases tests use.

    A hand-rolled double was the first draft and it was the wrong
    instrument: `is_real_destination` calls `known_places(world)`, which
    reads `world.movement`, so the double only proved that my idea of a
    world was incomplete. Building a real one also means the negative
    cases below are negative against the actual town rather than against
    an empty dict.
    """
    from world.tools.bootstrap import build_world

    built, _ = await build_world()
    return built


def test_the_model_still_has_no_location_id() -> None:
    """The premise. If somebody adds the field, this test should be
    reconsidered rather than left asserting a shape that moved."""
    assert not hasattr(_building("b1", "p1"), "location_id"), (
        "GROW-16: Building now HAS a location_id. The plot lookup in "
        "is_real_destination may be redundant - decide which is the "
        "building's ground and keep one answer."
    )


@pytest.mark.asyncio
async def test_a_finished_building_is_a_destination(world) -> None:
    """The finding. Before this, the loop compared None to the location
    and the answer was always False."""
    assert is_real_destination(world, "kasukabe-smithy-row") is False, (
        "premise: this place must not already be real by another route, "
        "or the assertion below proves nothing"
    )
    world.plots["p1"] = _Plot("p1", "kasukabe-smithy-row")
    world.buildings["b1"] = _building("b1", "p1")
    assert is_real_destination(world, "kasukabe-smithy-row") is True, (
        "GROW-16: a building the town raised, paid for and finished is "
        "still not somewhere a resident can walk to"
    )


@pytest.mark.asyncio
async def test_a_building_on_an_unnamed_plot_is_not_a_destination(world) -> None:
    """A plot with no location_id names nowhere. Returning True here
    would make every unnamed plot a destination for every query."""
    world.plots["p1"] = _Plot("p1", None)
    world.buildings["b1"] = _building("b1", "p1")
    assert is_real_destination(world, "kasukabe-smithy-row") is False


@pytest.mark.asyncio
async def test_a_building_whose_plot_is_gone_does_not_raise(world) -> None:
    """Checkpoint restore can land a building whose plot did not survive.
    A KeyError here would take down every movement check in the town."""
    world.buildings["b1"] = _building("b1", "no-such-plot")
    assert is_real_destination(world, "somewhere-a-model-invented") is False


@pytest.mark.asyncio
async def test_a_sentinel_in_the_buildings_dict_is_skipped(world) -> None:
    """`world.buildings` legitimately holds non-Building rows in the
    suite - `test_not_found_names_the_options` depends on it."""
    world.buildings["junk"] = object()
    world.plots["p1"] = _Plot("p1", "kasukabe-smithy-row")
    world.buildings["b1"] = _building("b1", "p1")
    assert is_real_destination(world, "kasukabe-smithy-row") is True


@pytest.mark.asyncio
async def test_a_different_place_is_still_not_a_destination(world) -> None:
    """The control. A branch that returned True for everything would
    pass every assertion above."""
    world.plots["p1"] = _Plot("p1", "kasukabe-smithy-row")
    world.buildings["b1"] = _building("b1", "p1")
    assert is_real_destination(world, "somewhere-a-model-invented") is False


def test_the_branch_does_not_read_a_field_the_model_lacks() -> None:
    """The guard against the exact regression. A defaulting getattr over
    a field that does not exist is a branch that cannot fire, and it
    looks correct in every code review."""
    import inspect

    from world.tools import place_aliases

    source = inspect.getsource(place_aliases.is_real_destination)
    assert 'getattr(building, "location_id"' not in source, (
        "GROW-16: is_real_destination reads Building.location_id again. "
        "That field does not exist, so the default answers None and the "
        "branch is dead."
    )
    assert "world.plots.get(building.plot_id)" in source
