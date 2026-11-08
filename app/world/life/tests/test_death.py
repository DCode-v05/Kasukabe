"""Tests for world.life.death: the four death paths and revival."""

from __future__ import annotations

import random

import pytest

from world.life.constants import REVIVAL_COST_SIM
from world.life.clock import GameClock
from world.life.death import (
    check_chronic_death,
    check_dormancy_death,
    check_elderly_death,
    check_terminal_death,
    mark_dormant,
    revive,
)
from world.life.models import Ailment, AilmentKind, DormancyState, HealthState, PopulationDials
from world.life.ports import InsufficientFundsError
from world.life.tests.conftest import InMemoryEconomy


class AlwaysDies(random.Random):
    def random(self) -> float:
        return 0.0


class NeverDies(random.Random):
    def random(self) -> float:
        return 0.999999


def test_check_terminal_death_false_before_scheduled_day() -> None:
    health = HealthState(
        current_ailment=Ailment(kind=AilmentKind.TERMINAL, onset_day=0, scheduled_death_day=5)
    )
    assert check_terminal_death(AlwaysDies(), health, day=4, dials=PopulationDials()) is False


def test_check_terminal_death_true_on_scheduled_day_with_default_hazard() -> None:
    health = HealthState(
        current_ailment=Ailment(kind=AilmentKind.TERMINAL, onset_day=0, scheduled_death_day=5)
    )
    assert check_terminal_death(AlwaysDies(), health, day=5, dials=PopulationDials()) is True


def test_check_terminal_death_false_when_hazard_multiplier_zero() -> None:
    health = HealthState(
        current_ailment=Ailment(kind=AilmentKind.TERMINAL, onset_day=0, scheduled_death_day=5)
    )
    dials = PopulationDials(death_hazard_multiplier=0.0)
    assert check_terminal_death(AlwaysDies(), health, day=5, dials=dials) is False


def test_check_terminal_death_false_for_non_terminal_ailment() -> None:
    health = HealthState(
        current_ailment=Ailment(kind=AilmentKind.SHORT, onset_day=0, duration_days=1)
    )
    assert check_terminal_death(AlwaysDies(), health, day=5, dials=PopulationDials()) is False


def test_check_chronic_death_fires_for_untreated_medium_ailment() -> None:
    health = HealthState(
        current_ailment=Ailment(kind=AilmentKind.MEDIUM, onset_day=0, duration_days=5)
    )
    assert check_chronic_death(AlwaysDies(), health, dials=PopulationDials()) is True


def test_check_chronic_death_never_fires_for_short_ailment() -> None:
    health = HealthState(
        current_ailment=Ailment(kind=AilmentKind.SHORT, onset_day=0, duration_days=1)
    )
    assert check_chronic_death(AlwaysDies(), health, dials=PopulationDials()) is False


def test_check_chronic_death_never_fires_once_treated() -> None:
    health = HealthState(
        current_ailment=Ailment(kind=AilmentKind.LONG, onset_day=0, duration_days=20, treated=True)
    )
    assert check_chronic_death(AlwaysDies(), health, dials=PopulationDials()) is False


def test_check_elderly_death_false_under_85() -> None:
    assert check_elderly_death(AlwaysDies(), age_years=84.9, dials=PopulationDials()) is False


def test_check_elderly_death_true_at_85_with_default_hazard() -> None:
    assert check_elderly_death(AlwaysDies(), age_years=85.0, dials=PopulationDials()) is True


def test_check_elderly_death_false_when_hazard_multiplier_zero() -> None:
    dials = PopulationDials(death_hazard_multiplier=0.0)
    assert check_elderly_death(AlwaysDies(), age_years=99.0, dials=dials) is False


def test_zero_hazard_multiplier_disables_every_death_path() -> None:
    """DESIGN.md section 5, verbatim: "death-hazard multiplier (0 disables death)"."""
    dials = PopulationDials(death_hazard_multiplier=0.0)
    terminal_health = HealthState(
        current_ailment=Ailment(kind=AilmentKind.TERMINAL, onset_day=0, scheduled_death_day=0)
    )
    chronic_health = HealthState(
        current_ailment=Ailment(kind=AilmentKind.LONG, onset_day=0, duration_days=20)
    )

    assert check_terminal_death(AlwaysDies(), terminal_health, day=99, dials=dials) is False
    assert check_chronic_death(AlwaysDies(), chronic_health, dials=dials) is False
    assert check_elderly_death(AlwaysDies(), age_years=120.0, dials=dials) is False


def test_mark_dormant_sets_timestamp_once() -> None:
    now = GameClock(total_hours=100.0)
    dormancy, events = mark_dormant(DormancyState(), now)

    assert dormancy.is_dormant is True
    assert dormancy.dormant_since_hours == 100.0
    assert events[0].type == "health.agent.dormant"


def test_mark_dormant_does_not_reset_an_already_dormant_timestamp() -> None:
    already = DormancyState(is_dormant=True, dormant_since_hours=10.0)
    dormancy, events = mark_dormant(already, GameClock(total_hours=50.0))

    assert dormancy.dormant_since_hours == 10.0
    assert events == []


def test_check_dormancy_death_false_before_24_hours_elapsed() -> None:
    dormancy = DormancyState(is_dormant=True, dormant_since_hours=0.0)
    now = GameClock(total_hours=23.9)
    assert check_dormancy_death(AlwaysDies(), dormancy, now, PopulationDials()) is False


def test_check_dormancy_death_true_at_24_hours_elapsed() -> None:
    dormancy = DormancyState(is_dormant=True, dormant_since_hours=0.0)
    now = GameClock(total_hours=24.0)
    assert check_dormancy_death(AlwaysDies(), dormancy, now, PopulationDials()) is True


def test_check_dormancy_death_false_when_not_dormant() -> None:
    now = GameClock(total_hours=1000.0)
    assert check_dormancy_death(AlwaysDies(), DormancyState(), now, PopulationDials()) is False


@pytest.mark.asyncio
async def test_revive_debits_the_rate_in_sim_and_credits_target_one_dollar(
    economy: InMemoryEconomy,
) -> None:
    economy.sim_balances["payer"] = 15_000
    dormancy = DormancyState(is_dormant=True, dormant_since_hours=5.0)

    updated, events = await revive(
        "payer", "target", dormancy, economy, GameClock(total_hours=10.0)
    )

    assert updated.is_dormant is False
    assert updated.dormant_since_hours is None
    assert economy.sim_balances["payer"] == 15_000 - REVIVAL_COST_SIM
    assert economy.debits == [("payer", REVIVAL_COST_SIM, "revival_payment")]
    assert economy.usd_credits == [("target", 100, "revival_credit")]
    assert events[0].type == "health.agent.revived"


@pytest.mark.asyncio
async def test_revive_raises_when_payer_cannot_afford_it(economy: InMemoryEconomy) -> None:
    economy.sim_balances["payer"] = 500
    dormancy = DormancyState(is_dormant=True, dormant_since_hours=0.0)

    with pytest.raises(InsufficientFundsError):
        await revive("payer", "target", dormancy, economy, GameClock(total_hours=1.0))

    assert economy.usd_credits == []


@pytest.mark.asyncio
async def test_revive_raises_when_target_not_dormant(economy: InMemoryEconomy) -> None:
    economy.sim_balances["payer"] = 200_000
    with pytest.raises(ValueError):
        await revive("payer", "target", DormancyState(), economy, GameClock(total_hours=1.0))
