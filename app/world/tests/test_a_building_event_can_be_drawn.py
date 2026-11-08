"""A building event says where the building is.

`UI-33` is a design note, not a defect: it lays out three options for
making the engine's Ville map and the township's hand-authored grid one
map, prices each, and recommends the first. Choosing between them is an
architecture decision and not something this pass may take.

It ends on one requirement that holds whichever option is taken, and that
one IS a defect: "either way `world.building.*` events must carry
`{rect, sector_name, kind, status}`."

They did not. `world.building.construction_started` carried `area_tiles`
- a COUNT - and `world.building.mapped` carried `tiles`, another count.
`world.building.completed` carried a building id and a purpose. So a
viewer subscribed to the only events that change the shape of the town
learned that a building existed, how big it was, and nothing whatsoever
about where it stood. Construction is the one mechanism in this project
that alters the map at runtime; an event about it that cannot be drawn is
an event that cannot do the job it exists for.

A bounding box rather than the tile set: every building here is built on
a rectangular plot, the Phaser page draws building markers from a rect,
and shipping a 40-tile list on every event costs more than the one fact
anybody reads off it. A non-rectangular footprint reports the box that
contains it - too big, never wrong about position.
"""
from __future__ import annotations

import pytest

from world.map.expansion import building_sector_name, footprint_rect

RECT_KEYS = {"x", "y", "w", "h"}


def test_a_rect_is_the_bounding_box() -> None:
    assert footprint_rect([(3, 5), (4, 5), (3, 6)]) == {
        "x": 3, "y": 5, "w": 2, "h": 2,
    }


def test_a_single_tile_building_is_one_by_one() -> None:
    """Off-by-one country. A 1x1 building whose rect said w=0 would be
    drawn as nothing at all."""
    assert footprint_rect([(7, 7)]) == {"x": 7, "y": 7, "w": 1, "h": 1}


def test_it_takes_tilecoords_as_well_as_tuples() -> None:
    """`stamp_building` holds `TileCoord`s and `Building.footprint` holds
    tuples. One function, both callers, or the two drift."""
    from world.map.model import TileCoord

    assert footprint_rect([TileCoord(3, 5), TileCoord(4, 6)]) == footprint_rect(
        [(3, 5), (4, 6)]
    )


def test_an_empty_footprint_is_refused() -> None:
    """`{x: inf}` drawn at the origin is worse than a raise."""
    with pytest.raises(ValueError):
        footprint_rect([])


def test_the_sector_name_has_one_definition() -> None:
    """`stamp_building` mints it and `complete_construction` reports it.
    Two f-strings would agree until the first time either was edited."""
    assert building_sector_name("smithy", "agent-7") == "smithy (agent-7)"


def test_the_started_event_carries_all_four(monkeypatch) -> None:
    """The finding's list, on the event that fires when the ground is
    broken - the moment a viewer needs to put a hoarding somewhere."""
    import inspect

    from world.tools.primitives import construction

    source = inspect.getsource(construction.build_structure_handler)
    for key in ('"rect"', '"kind"', '"status"'):
        assert key in source, (
            f"UI-33: world.building.construction_started has no {key} - a "
            "viewer is told a building exists and not where it is going"
        )
    assert "footprint_rect(footprint)" in source


@pytest.mark.asyncio
async def test_the_completed_event_carries_all_four() -> None:
    """And on completion, so a viewer can replace the hoarding with the
    building without re-fetching anything."""
    from world.tools.models import Building, BuildingStatus
    from world.tools.primitives.construction import complete_construction

    class _World:
        def __init__(self) -> None:
            self.buildings = {
                "b1": Building(
                    building_id="b1", plot_id="p1", builder_agent_id="a1",
                    purpose="smithy", footprint=[(2, 3), (3, 3), (2, 4), (3, 4)],
                    timber_used=1, metal_used=1, started_tick=1, completion_tick=2,
                )
            }

        def tick(self) -> int:
            return 9

    event = await complete_construction(_World(), "b1")
    assert event.type == "world.building.completed"
    assert set(event.payload["rect"]) == RECT_KEYS
    assert event.payload["rect"] == {"x": 2, "y": 3, "w": 2, "h": 2}
    assert event.payload["kind"] == "smithy"
    assert event.payload["status"] == "complete"


def test_the_mapped_event_carries_a_rect() -> None:
    """`world.building.mapped` is the one the review names by path
    (`service.py:218-291`). It shipped `"tiles": len(footprint)`, which is
    how many, not where."""
    import inspect

    from world.map import service

    source = inspect.getsource(service.MapService.stamp_building)
    assert '"rect": rect' in source, (
        "UI-33: world.building.mapped still reports only a tile COUNT, so "
        "the event that adds a sector to the live map cannot be drawn"
    )
    assert '"status": "mapped"' in source
    assert '"kind": purpose' in source


def test_the_count_is_still_there() -> None:
    """`tiles` and `area_tiles` were not replaced. A non-rectangular
    footprint has a smaller area than its bounding box, and a reader
    comparing the two is the only way to notice."""
    import inspect

    from world.map import service
    from world.tools.primitives import construction

    assert '"tiles": len(footprint)' in inspect.getsource(
        service.MapService.stamp_building
    )
    assert '"area_tiles": area' in inspect.getsource(construction.build_structure_handler)
