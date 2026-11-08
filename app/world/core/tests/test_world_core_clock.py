"""Tests for world.core.clock - tick/calendar math and GameClock state."""

from __future__ import annotations

import pytest

from world.core.clock import (
    DAY_START_HOUR,
    MINUTES_PER_DAY,
    NIGHT_START_HOUR,
    TICK_MINUTES,
    TICKS_PER_DAY,
    GameClock,
    is_day_boundary,
    timestamp_for,
)


def test_constants_match_design_doc() -> None:
    assert TICK_MINUTES == 10
    assert TICKS_PER_DAY == 144
    assert MINUTES_PER_DAY == 24 * 60
    assert TICKS_PER_DAY * TICK_MINUTES == MINUTES_PER_DAY


def test_tick_zero_is_midnight_day_zero_and_night() -> None:
    ts = timestamp_for(0)
    assert ts.tick_index == 0
    assert ts.game_day == 0
    assert ts.day_tick == 0
    assert ts.hour == 0
    assert ts.minute == 0
    assert ts.is_daytime is False


def test_one_tick_advances_ten_minutes() -> None:
    ts = timestamp_for(1)
    assert ts.hour == 0
    assert ts.minute == 10
    assert ts.day_tick == 1


def test_144_ticks_completes_exactly_one_day() -> None:
    ts = timestamp_for(TICKS_PER_DAY)
    assert ts.game_day == 1
    assert ts.day_tick == 0
    assert ts.hour == 0
    assert ts.minute == 0


def test_day_boundary_only_fires_on_multiples_of_144_and_never_at_zero() -> None:
    assert is_day_boundary(0) is False
    assert is_day_boundary(1) is False
    assert is_day_boundary(TICKS_PER_DAY - 1) is False
    assert is_day_boundary(TICKS_PER_DAY) is True
    assert is_day_boundary(TICKS_PER_DAY * 2) is True
    assert is_day_boundary(TICKS_PER_DAY * 2 + 1) is False


@pytest.mark.parametrize(
    "hour,expected_daytime",
    [
        (0, False),
        (5, False),
        (DAY_START_HOUR - 1, False),
        (DAY_START_HOUR, True),
        (12, True),
        (NIGHT_START_HOUR - 1, True),
        (NIGHT_START_HOUR, False),
        (23, False),
    ],
)
def test_day_night_boundary(hour: int, expected_daytime: bool) -> None:
    tick_index = (hour * 60) // TICK_MINUTES
    ts = timestamp_for(tick_index)
    assert ts.hour == hour
    assert ts.is_daytime is expected_daytime


def test_night_span_is_eight_hours() -> None:
    night_hours = [h for h in range(24) if not (DAY_START_HOUR <= h < NIGHT_START_HOUR)]
    assert len(night_hours) == 8


def test_timestamp_for_rejects_negative_tick() -> None:
    with pytest.raises(ValueError):
        timestamp_for(-1)


def test_game_clock_starts_at_zero_by_default() -> None:
    clock = GameClock()
    assert clock.tick_index == 0
    assert clock.now().tick_index == 0


def test_game_clock_advance_mutates_and_returns_same_timestamp() -> None:
    clock = GameClock()
    ts = clock.advance()
    assert clock.tick_index == 1
    assert ts == clock.now()


def test_game_clock_rejects_negative_start() -> None:
    with pytest.raises(ValueError):
        GameClock(-1)


def test_game_clock_snapshot_restore_round_trip() -> None:
    clock = GameClock()
    for _ in range(37):
        clock.advance()
    data = clock.snapshot()

    restored = GameClock()
    restored.restore(data)
    assert restored.tick_index == clock.tick_index == 37


def test_game_clock_restore_rejects_bad_payload() -> None:
    clock = GameClock()
    with pytest.raises(ValueError):
        clock.restore({"tick_index": -5})
    with pytest.raises(ValueError):
        clock.restore({"tick_index": "37"})
