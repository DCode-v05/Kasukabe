"""Tests for world.life.clock.GameClock."""

from __future__ import annotations

import pytest

from world.life.clock import GameClock


def test_day_and_hour_of_day_derive_from_total_hours() -> None:
    clock = GameClock(total_hours=50.0)
    assert clock.day == 2
    assert clock.hour_of_day == 2.0


def test_advanced_by_returns_a_new_clock() -> None:
    clock = GameClock(total_hours=10.0)
    advanced = clock.advanced_by(5.0)

    assert advanced.total_hours == 15.0
    assert clock.total_hours == 10.0


def test_advanced_by_rejects_negative_hours() -> None:
    with pytest.raises(ValueError):
        GameClock().advanced_by(-1.0)


def test_at_day_builds_the_clock_at_a_days_start() -> None:
    assert GameClock.at_day(3).total_hours == 72.0
