"""The town has to be able to tell its new ground from its old ground.

`GROW-20`. `TileOwnership.from_expansion` has existed since section 31.2
asked `auction_land` to scope itself to "newly petitioned/expanded land",
and **nothing ever set it**. Every tile in the town, however it arrived,
reported `from_expansion=False`, so the distinction the auction is defined
in terms of did not exist in the data.

It was not an oversight so much as a homeless fact: which tiles a petition
added is knowledge the MAP has at approval time, and the flag lives on a
tile record that is materialised lazily, one tile at a time, whenever
somebody first asks about it - long after the strip was appended. There
was nowhere for the answer to be written down between the two.

`WorldState.expanded_rects` is that place, and it is owned state, so a
resumed town still knows which of its land is new.
"""
from __future__ import annotations

from world.tools.primitives.property import require_tile


class TestBeforeAnyExpansion:
    def test_genesis_ground_is_not_new(self, world) -> None:
        assert require_tile(world, 5, 5).from_expansion is False

    def test_the_world_starts_with_no_recorded_strips(self, world) -> None:
        assert world.expanded_rects == []


class TestAfterOne:
    def test_a_tile_inside_the_strip_is_new(self, world) -> None:
        world.expanded_rects.append((0, 40, 60, 43))
        assert require_tile(world, 10, 41).from_expansion is True

    def test_a_tile_outside_it_is_not(self, world) -> None:
        world.expanded_rects.append((0, 40, 60, 43))
        assert require_tile(world, 10, 39).from_expansion is False

    def test_the_rect_is_half_open(self, world) -> None:
        """The module's own `Rect` convention, so a boundary tile is not
        counted twice when two strips meet."""
        world.expanded_rects.append((0, 40, 60, 43))
        assert require_tile(world, 10, 40).from_expansion is True
        assert require_tile(world, 10, 43).from_expansion is False

    def test_several_strips_all_count(self, world) -> None:
        world.expanded_rects.extend([(0, 40, 60, 43), (60, 0, 64, 43)])
        assert require_tile(world, 61, 5).from_expansion is True
        assert require_tile(world, 10, 41).from_expansion is True
        assert require_tile(world, 10, 5).from_expansion is False


def test_an_already_owned_tile_keeps_its_own_record(world) -> None:
    """`require_tile` returns a stored row untouched when there is one -
    the default is only for ground nobody has traded yet."""
    from world.tools.models import OwnerClass, TileOwnership
    from world.tools.state import tile_key

    world.expanded_rects.append((0, 0, 100, 100))
    world.tile_owners[tile_key(3, 3)] = TileOwnership(
        tile_x=3, tile_y=3, owner_id="n01", owner_class=OwnerClass.PRIVATE,
        from_expansion=False,
    )
    assert require_tile(world, 3, 3).owner_id == "n01"
    assert require_tile(world, 3, 3).from_expansion is False
