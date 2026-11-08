"""Tests for world.life.population: the god-tab dial gates."""

from __future__ import annotations

import random

from world.life.models import PopulationDials
from world.life.population import evaluate_birth_request, is_below_target_band, roll_death


class AlwaysDies(random.Random):
    def random(self) -> float:
        return 0.0


class NeverDies(random.Random):
    def random(self) -> float:
        return 0.999999


def test_roll_death_false_when_multiplier_is_zero() -> None:
    assert (
        roll_death(
            AlwaysDies(), base_probability=1.0, dials=PopulationDials(death_hazard_multiplier=0.0)
        )
        is False
    )


def test_roll_death_true_when_certain_and_multiplier_default() -> None:
    assert roll_death(AlwaysDies(), base_probability=1.0, dials=PopulationDials()) is True


def test_roll_death_false_when_draw_above_scaled_probability() -> None:
    assert roll_death(NeverDies(), base_probability=0.01, dials=PopulationDials()) is False


def test_roll_death_probability_is_capped_at_one() -> None:
    # base_probability * multiplier can exceed 1.0; must still clamp so a
    # too-eager multiplier never becomes a >100% chance.
    dials = PopulationDials(death_hazard_multiplier=5.0)
    assert roll_death(AlwaysDies(), base_probability=0.5, dials=dials) is True


def test_evaluate_birth_request_holds_at_target_band_ceiling() -> None:
    decision = evaluate_birth_request(
        current_population=300, dials=PopulationDials(target_band_max=300)
    )
    assert decision.auto_hold is True


def test_evaluate_birth_request_holds_when_throttle_is_zero() -> None:
    decision = evaluate_birth_request(
        current_population=10, dials=PopulationDials(blessing_throttle=0.0)
    )
    assert decision.auto_hold is True


def test_evaluate_birth_request_open_within_band_and_throttle() -> None:
    decision = evaluate_birth_request(current_population=260, dials=PopulationDials())
    assert decision.auto_hold is False


def test_is_below_target_band() -> None:
    dials = PopulationDials(target_band_min=250)
    assert is_below_target_band(200, dials) is True
    assert is_below_target_band(250, dials) is False
