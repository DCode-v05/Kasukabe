"""Unit tests for the pure grid-neighbor helpers in world.map.walkability."""

from __future__ import annotations

from world.map.walkability import in_bounds_xy, neighbors4, neighbors8


def test_neighbors4_returns_the_four_orthogonal_points() -> None:
    assert set(neighbors4(2, 2)) == {(3, 2), (1, 2), (2, 3), (2, 1)}


def test_neighbors8_includes_the_four_diagonals_too() -> None:
    result = set(neighbors8(2, 2))
    assert {(3, 2), (1, 2), (2, 3), (2, 1)} <= result
    assert {(3, 3), (3, 1), (1, 3), (1, 1)} <= result
    assert len(result) == 8


def test_in_bounds_xy_true_inside_grid() -> None:
    assert in_bounds_xy(5, 5, 0, 0)
    assert in_bounds_xy(5, 5, 4, 4)


def test_in_bounds_xy_false_outside_grid() -> None:
    assert not in_bounds_xy(5, 5, -1, 0)
    assert not in_bounds_xy(5, 5, 0, -1)
    assert not in_bounds_xy(5, 5, 5, 0)
    assert not in_bounds_xy(5, 5, 0, 5)
