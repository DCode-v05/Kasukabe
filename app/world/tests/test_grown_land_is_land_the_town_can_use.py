"""Land the town grows is reachable, owned, and what it was asked for.

`GROW-13`. `approve_land_petition` carved at most one sector and stopped.
It never:

  - joined the strip to the walkable graph. `open_walkable_corridors` had
    only ever run at genesis, and that function exists because the shipped
    map was 89% walkable across seventeen disconnected regions. Every
    strip the town grew went straight back into that state: land nobody
    could walk onto, which is not land the town has gained.
  - seeded a `ResourceZone`. `seed_resource_zones` runs once, at boot, so a
    strip petitioned as farmland grew nothing at all. A pipeline that
    reports success and delivers ground that yields nothing is worse than
    one that refuses.
  - recorded who held the ground. `require_tile` reads an unrecorded tile
    as government-owned, so this changed no answer - and that is exactly
    why it mattered: "nobody has said" and "the government holds it" were
    the same state, so `auction_land`, which section 31.2 scopes to "newly
    petitioned/expanded land", had no way to tell which was which.
    `from_expansion` existed as a field and had no writer.

The strip's rect (GROW-26) and `expanded_rects` (GROW-20) were already
done by earlier work - checked, not assumed.

`purpose` is optional and defaults to None, which grows bare ground
exactly as before. A required field would invalidate every existing
caller of the approve route, and defaulting it to something would put a
label on the record that nobody chose.
"""
from __future__ import annotations

import pytest

from world.map.expansion import Edge, LandExpansion


def test_the_expansion_record_carries_what_it_was_for() -> None:
    """Both new fields default to the old behaviour: no purpose, no
    corridors cut."""
    bare = LandExpansion(
        edge=Edge.SOUTH, depth=4,
        previous_width=10, previous_height=10, new_width=10, new_height=14,
    )
    assert bare.purpose is None
    assert bare.corridors_opened == 0


def test_expand_land_takes_a_purpose() -> None:
    import inspect

    from world.map.service import MapService

    params = inspect.signature(MapService.expand_land).parameters
    assert "purpose" in params, (
        "GROW-13: expansion cannot be told what the land is for, so a "
        "farmland petition and a residential one grow the same bare ground"
    )


def test_expand_land_opens_corridors_before_rebuilding_the_map() -> None:
    """Order matters and cannot be checked by calling it: the corridors
    are cut into the TILES, and `build_world_map_from_tiled` is what turns
    tiles into the graph `move` paths over. Cutting after the rebuild
    would leave the graph unchanged and the strip still an island."""
    import inspect

    from world.map.service import MapService

    source = inspect.getsource(MapService.expand_land)
    assert "open_walkable_corridors(tiled)" in source, (
        "GROW-13: the new strip is never joined to the walkable graph, so "
        "the town grew ground no resident can reach"
    )
    cut = source.index("open_walkable_corridors(tiled)")
    rebuild = source.index("build_world_map_from_tiled(tiled, blocks)")
    assert cut < rebuild, (
        "GROW-13: the corridors are cut after the map is rebuilt, so the "
        "graph never sees them and the strip stays an island"
    )


# ── ownership ────────────────────────────────────────────────────────────

class _Ground:
    """The three members `_record_the_new_ground` touches, and no more.

    A real `WorldState` needs a ledger, a charter, an event bus and a
    location table to construct, none of which this function reads. The
    double is narrow ON PURPOSE: if the function grows a dependency, this
    raises AttributeError and somebody has to decide whether that
    dependency belongs there - which a permissive fake would hide.
    """

    def __init__(self) -> None:
        self.tile_owners: dict = {}
        self.resource_zones: dict = {}

    def tick(self) -> int:
        return 42


def _world_with_tiles():
    return _Ground()


def test_the_strip_is_recorded_as_the_governments() -> None:
    from world.admin_api import _record_the_new_ground
    from world.tools.models import OwnerClass
    from world.tools.state import tile_key

    world = _world_with_tiles()
    expansion = LandExpansion(
        edge=Edge.SOUTH, depth=2,
        previous_width=3, previous_height=3, new_width=3, new_height=5,
        strip_x0=0, strip_y0=3, strip_x1=3, strip_y1=5,
    )
    recorded = _record_the_new_ground(world, expansion)

    assert recorded == 6, f"3x2 strip should record six tiles, got {recorded}"
    held = world.tile_owners[tile_key(1, 4)]
    assert held.owner_class is OwnerClass.GOVERNMENT
    assert held.from_expansion is True, (
        "GROW-13: the tile does not say it arrived by expansion, so "
        "auction_land cannot scope itself to newly petitioned land"
    )


def test_an_existing_owner_is_not_overwritten() -> None:
    """A second expansion over ground somebody already holds must not
    quietly hand it back to the government."""
    from world.admin_api import _record_the_new_ground
    from world.tools.models import OwnerClass, TileOwnership
    from world.tools.state import tile_key

    world = _world_with_tiles()
    world.tile_owners[tile_key(1, 4)] = TileOwnership(
        tile_x=1, tile_y=4, owner_id="a0007", owner_class=OwnerClass.PRIVATE
    )
    expansion = LandExpansion(
        edge=Edge.SOUTH, depth=2,
        previous_width=3, previous_height=3, new_width=3, new_height=5,
        strip_x0=0, strip_y0=3, strip_x1=3, strip_y1=5,
    )
    _record_the_new_ground(world, expansion)

    assert world.tile_owners[tile_key(1, 4)].owner_id == "a0007"


# ── what the strip is for ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "purpose,kind,item",
    [("farmland", "farmland", "wheat"), ("ore", "ore_outcrop", "iron_ore")],
)
def test_a_strip_grown_for_a_resource_has_that_resource(purpose, kind, item) -> None:
    from world.admin_api import _record_the_new_ground

    world = _world_with_tiles()
    expansion = LandExpansion(
        edge=Edge.SOUTH, depth=1,
        previous_width=2, previous_height=2, new_width=2, new_height=3,
        strip_x0=0, strip_y0=2, strip_x1=2, strip_y1=3,
        new_sector_names=["Kasukabe North Field"],
        purpose=purpose,
    )
    _record_the_new_ground(world, expansion)

    zone = world.resource_zones.get("Kasukabe North Field")
    assert zone is not None, (
        f"GROW-13: a strip petitioned as {purpose} grew nothing - "
        "seed_resource_zones only ever runs at boot"
    )
    assert zone.kind.value == kind
    assert zone.resource_item == item


def test_a_strip_grown_to_build_on_gets_no_resource_zone() -> None:
    """`residential`, `civic` and `industrial` are places to build.
    Seeding wheat on them would make every new street a field."""
    from world.admin_api import _record_the_new_ground

    world = _world_with_tiles()
    expansion = LandExpansion(
        edge=Edge.SOUTH, depth=1,
        previous_width=2, previous_height=2, new_width=2, new_height=3,
        strip_x0=0, strip_y0=2, strip_x1=2, strip_y1=3,
        new_sector_names=["Kasukabe North Row"],
        purpose="residential",
    )
    _record_the_new_ground(world, expansion)
    assert world.resource_zones == {}


def test_no_purpose_still_grows_bare_ground() -> None:
    """The old behaviour, kept. Every caller that does not pass a purpose
    gets exactly what it got before."""
    from world.admin_api import _record_the_new_ground

    world = _world_with_tiles()
    expansion = LandExpansion(
        edge=Edge.SOUTH, depth=1,
        previous_width=2, previous_height=2, new_width=2, new_height=3,
        strip_x0=0, strip_y0=2, strip_x1=2, strip_y1=3,
        new_sector_names=["Kasukabe North Row"],
    )
    recorded = _record_the_new_ground(world, expansion)

    assert recorded == 2, "the ground is still recorded"
    assert world.resource_zones == {}, "and nothing is invented on it"


def test_the_zone_is_keyed_on_a_place_a_resident_can_walk_to() -> None:
    """A zone keyed on a name this module minted would gate a verb on a
    place `move` cannot route to - the same defect one layer along."""
    from world.admin_api import _record_the_new_ground

    world = _world_with_tiles()
    expansion = LandExpansion(
        edge=Edge.SOUTH, depth=1,
        previous_width=2, previous_height=2, new_width=2, new_height=3,
        strip_x0=0, strip_y0=2, strip_x1=2, strip_y1=3,
        new_sector_names=["Kasukabe North Field"],
        purpose="farmland",
    )
    _record_the_new_ground(world, expansion)

    assert list(world.resource_zones) == ["Kasukabe North Field"], (
        "the zone must be keyed on the sector's own name, which is what "
        "move takes a resident to"
    )


def test_the_approve_route_passes_the_purpose_through() -> None:
    """The wiring. A `purpose` field the handler never forwards is a
    field that does nothing, which is this review's commonest defect."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "admin_api.py").read_text(
        encoding="utf-8"
    )
    assert "purpose=req.purpose" in source, (
        "GROW-13: the operator can state a purpose and it never reaches "
        "expand_land"
    )
    assert "_record_the_new_ground(deps.world, expansion)" in source, (
        "GROW-13: nothing records the new ground, so from_expansion still "
        "has no writer"
    )
