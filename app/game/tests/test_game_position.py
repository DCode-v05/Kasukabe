"""Pure presentation math: sprite roster assignment, day/night tint,
fallback tile placement. No asyncio, no server - see position.py's
module docstring for why these stay plain functions."""

from __future__ import annotations

import pytest

from game.position import (
    DAY_START_HOUR,
    NIGHT_START_HOUR,
    SPRITE_ROSTER,
    day_night_tint,
    fallback_tile_for,
    game_day_and_time,
    is_daytime,
    resolve_tile,
    sprite_key_for,
    stable_hash,
)


def test_sprite_key_for_is_deterministic() -> None:
    assert sprite_key_for("agent-1") == sprite_key_for("agent-1")


def test_sprite_key_for_is_always_in_roster() -> None:
    for i in range(500):
        assert sprite_key_for(f"agent-{i}") in SPRITE_ROSTER


def test_sprite_key_for_distributes_across_roster() -> None:
    assigned = {sprite_key_for(f"agent-{i:03d}") for i in range(250)}
    assert len(assigned) > len(SPRITE_ROSTER) // 2


def test_sprite_key_for_rejects_empty_agent_id() -> None:
    with pytest.raises(ValueError):
        sprite_key_for("")


def test_stable_hash_is_deterministic_across_calls() -> None:
    assert stable_hash("hobbs-cafe") == stable_hash("hobbs-cafe")


def test_stable_hash_differs_for_different_keys() -> None:
    assert stable_hash("a") != stable_hash("b")


@pytest.mark.parametrize(
    "tick,expected_alpha_zero",
    [
        (0, False),
        (12 * 6, True),
    ],
)
def test_day_night_tint_bounds(tick: int, expected_alpha_zero: bool) -> None:
    _, alpha = day_night_tint(tick)
    if expected_alpha_zero:
        assert alpha == 0.0
    else:
        assert alpha > 0.0


def test_day_night_tint_alpha_never_exceeds_cap() -> None:
    for tick in range(144):
        _, alpha = day_night_tint(tick)
        assert 0.0 <= alpha <= 0.72


def test_day_night_tint_rejects_negative_tick() -> None:
    with pytest.raises(ValueError):
        day_night_tint(-1)


def test_is_daytime_matches_hour_window() -> None:
    day_tick = (DAY_START_HOUR * 60) // 10
    night_tick = (NIGHT_START_HOUR * 60) // 10
    assert is_daytime(day_tick) is True
    assert is_daytime(night_tick) is False
    assert is_daytime(0) is False


def test_game_day_and_time_advances_correctly() -> None:
    assert game_day_and_time(0) == (0, 0, 0)
    assert game_day_and_time(1) == (0, 0, 10)
    assert game_day_and_time(144) == (1, 0, 0)


def test_game_day_and_time_rejects_negative_tick() -> None:
    with pytest.raises(ValueError):
        game_day_and_time(-1)


def test_fallback_tile_for_is_deterministic() -> None:
    assert fallback_tile_for("Hobbs Cafe") == fallback_tile_for("Hobbs Cafe")


def test_fallback_tile_for_differs_across_locations() -> None:
    a = fallback_tile_for("Hobbs Cafe")
    b = fallback_tile_for("The Rose and Crown Pub")
    assert a != b


def test_fallback_tile_for_stays_in_bounds() -> None:
    for name in ["Hobbs Cafe", "Kasukabe Temple", "Oak Hill College", "x", "y" * 40]:
        x, y = fallback_tile_for(name)
        assert 0 <= x < 140
        assert 0 <= y < 100


def test_fallback_tile_for_rejects_empty_location() -> None:
    with pytest.raises(ValueError):
        fallback_tile_for("")


def test_resolve_tile_prefers_explicit_coordinates() -> None:
    assert resolve_tile("Hobbs Cafe", 5, 6) == (5, 6)


def test_resolve_tile_falls_back_to_location_id() -> None:
    assert resolve_tile("Hobbs Cafe", None, None) == fallback_tile_for("Hobbs Cafe")


def test_resolve_tile_raises_with_nothing_to_go_on() -> None:
    with pytest.raises(ValueError):
        resolve_tile(None, None, None)
