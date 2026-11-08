"""Tests for world.map.parcel: bill-of-materials geometry, contiguity
checking, and PlotRegistry's claim validation/ownership tracking - all
against the tiny synthetic fixture's exterior street row (the only
walkable, sector-free land in that map, i.e. the only legitimately
claimable land under this module's own rules).
"""

from __future__ import annotations

import pytest

from world.map.model import TileCoord, WorldMap
from world.map.parcel import (
    BillOfMaterials,
    Plot,
    PlotError,
    PlotRegistry,
    compute_bill_of_materials,
    is_contiguous,
)


def test_single_tile_has_area_one_and_perimeter_four() -> None:
    bom = compute_bill_of_materials(frozenset({TileCoord(0, 0)}))
    assert bom == BillOfMaterials(timber=2, metal=4)


def test_two_by_two_square_has_area_four_and_perimeter_eight() -> None:
    footprint = frozenset(
        {TileCoord(0, 0), TileCoord(1, 0), TileCoord(0, 1), TileCoord(1, 1)}
    )
    bom = compute_bill_of_materials(footprint)
    assert bom.timber == 4 * 2
    assert bom.metal == 8 * 1


def test_bill_of_materials_rejects_empty_footprint() -> None:
    with pytest.raises(ValueError):
        compute_bill_of_materials(frozenset())


def test_single_tile_is_contiguous() -> None:
    assert is_contiguous(frozenset({TileCoord(0, 0)}))


def test_adjacent_tiles_are_contiguous() -> None:
    assert is_contiguous(frozenset({TileCoord(0, 0), TileCoord(1, 0), TileCoord(1, 1)}))


def test_disconnected_tiles_are_not_contiguous() -> None:
    assert not is_contiguous(frozenset({TileCoord(0, 0), TileCoord(5, 5)}))


def test_diagonally_touching_tiles_are_not_contiguous() -> None:
    assert not is_contiguous(frozenset({TileCoord(0, 0), TileCoord(1, 1)}))


def test_empty_footprint_is_not_contiguous() -> None:
    assert not is_contiguous(frozenset())


@pytest.fixture
def registry(tiny_world: WorldMap) -> PlotRegistry:
    return PlotRegistry(tiny_world)


def street_footprint() -> frozenset[TileCoord]:
    return frozenset({TileCoord(0, 4), TileCoord(1, 4)})


def test_valid_claim_on_street_land_has_no_errors(registry: PlotRegistry) -> None:
    assert registry.validate_claim(street_footprint()) == []


def test_register_succeeds_for_a_valid_claim(registry: PlotRegistry) -> None:
    plot = Plot(id="plot-1", footprint=street_footprint(), purpose="stall", owner="agent-1")
    registry.register(plot)
    assert registry.plots["plot-1"] is plot
    for tile in street_footprint():
        assert registry.claimed_by_tile[tile] == "plot-1"


def test_register_rejects_out_of_bounds_tile(registry: PlotRegistry) -> None:
    footprint = frozenset({TileCoord(999, 999)})
    plot = Plot(id="plot-oob", footprint=footprint, purpose="stall", owner="agent-1")
    with pytest.raises(PlotError):
        registry.register(plot)


def test_register_rejects_a_tile_inside_an_existing_sector(registry: PlotRegistry) -> None:
    footprint = frozenset({TileCoord(2, 2)})
    plot = Plot(id="plot-in-house", footprint=footprint, purpose="stall", owner="agent-1")
    with pytest.raises(PlotError):
        registry.register(plot)


def test_register_rejects_an_unwalkable_tile(registry: PlotRegistry) -> None:
    footprint = frozenset({TileCoord(0, 0)})
    plot = Plot(id="plot-wall", footprint=footprint, purpose="stall", owner="agent-1")
    with pytest.raises(PlotError):
        registry.register(plot)


def test_register_rejects_a_non_contiguous_footprint(registry: PlotRegistry) -> None:
    footprint = frozenset({TileCoord(0, 4), TileCoord(4, 4)})
    plot = Plot(id="plot-split", footprint=footprint, purpose="stall", owner="agent-1")
    with pytest.raises(PlotError):
        registry.register(plot)


def test_register_rejects_overlap_with_an_existing_plot(registry: PlotRegistry) -> None:
    first = Plot(id="plot-a", footprint=street_footprint(), purpose="stall", owner="agent-1")
    registry.register(first)
    second = Plot(
        id="plot-b",
        footprint=frozenset({TileCoord(1, 4), TileCoord(2, 4)}),
        purpose="stall",
        owner="agent-2",
    )
    with pytest.raises(PlotError) as exc_info:
        registry.register(second)
    assert any("already claimed" in message for message in exc_info.value.errors)


def test_register_rejects_duplicate_plot_id(registry: PlotRegistry) -> None:
    plot = Plot(id="plot-dup", footprint=frozenset({TileCoord(0, 4)}), purpose="stall", owner="a")
    registry.register(plot)
    other = Plot(id="plot-dup", footprint=frozenset({TileCoord(1, 4)}), purpose="stall", owner="b")
    with pytest.raises(PlotError):
        registry.register(other)


def test_validate_claim_aggregates_every_problem(registry: PlotRegistry) -> None:
    footprint = frozenset({TileCoord(0, 0), TileCoord(999, 999)})
    errors = registry.validate_claim(footprint)
    assert len(errors) >= 2


def test_transfer_changes_the_owner(registry: PlotRegistry) -> None:
    plot = Plot(id="plot-1", footprint=street_footprint(), purpose="stall", owner="agent-1")
    registry.register(plot)
    updated = registry.transfer("plot-1", "agent-2")
    assert updated.owner == "agent-2"
    assert registry.owner_of(TileCoord(0, 4)) == "agent-2"


def test_transfer_unknown_plot_raises(registry: PlotRegistry) -> None:
    with pytest.raises(PlotError):
        registry.transfer("no-such-plot", "agent-2")


def test_release_frees_the_tiles(registry: PlotRegistry) -> None:
    plot = Plot(id="plot-1", footprint=street_footprint(), purpose="stall", owner="agent-1")
    registry.register(plot)
    registry.release("plot-1")
    assert registry.plot_at(TileCoord(0, 4)) is None
    assert registry.validate_claim(street_footprint()) == []


def test_release_unknown_plot_raises(registry: PlotRegistry) -> None:
    with pytest.raises(PlotError):
        registry.release("no-such-plot")


def test_owner_of_unclaimed_tile_is_none(registry: PlotRegistry) -> None:
    assert registry.owner_of(TileCoord(0, 4)) is None


def test_plots_owned_by_filters_correctly(registry: PlotRegistry) -> None:
    registry.register(Plot(id="p1", footprint=frozenset({TileCoord(0, 4)}), purpose="a", owner="agent-1"))
    registry.register(Plot(id="p2", footprint=frozenset({TileCoord(1, 4)}), purpose="b", owner="agent-2"))
    registry.register(Plot(id="p3", footprint=frozenset({TileCoord(4, 4)}), purpose="c", owner="agent-1"))
    owned = {plot.id for plot in registry.plots_owned_by("agent-1")}
    assert owned == {"p1", "p3"}


def test_unowned_plot_is_registrable_with_owner_none(registry: PlotRegistry) -> None:
    plot = Plot(id="gov-plot", footprint=street_footprint(), purpose="market stall", owner=None)
    registry.register(plot)
    assert registry.owner_of(TileCoord(0, 4)) is None
    assert registry.plot_at(TileCoord(0, 4)) is plot
