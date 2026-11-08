"""Tests for world.life.health: daily incidence rolls, recovery, treatment."""

from __future__ import annotations

import random

import pytest

from world.life.health import (
    TreatmentUnavailableError,
    advance_recovery,
    is_recovered,
    roll_daily_health,
    treat_at_pharmacy,
    work_capacity,
)
from world.life.models import AgentLifeRecord, Ailment, AilmentKind, HealthState, LifeEvent, Sex
from world.life.ports import InsufficientFundsError
from world.life.tests.conftest import InMemoryEconomy


class ScriptedRandom:
    """A random.Random stand-in whose first .random() draw is scripted.

    Everything after the first call to .random(), plus .randint() and
    .choice(), delegate to a real seeded Random so onset_ailment's
    duration/death-window rolls stay valid without needing to be
    individually scripted too.
    """

    def __init__(self, first_draw: float, seed: int = 0) -> None:
        self._first_draw = first_draw
        self._used = False
        self._real = random.Random(seed)

    def random(self) -> float:
        if not self._used:
            self._used = True
            return self._first_draw
        return self._real.random()

    def randint(self, a: int, b: int) -> int:
        return self._real.randint(a, b)

    def choice(self, seq):  # type: ignore[no-untyped-def]
        return self._real.choice(seq)


# Cumulative incidence bands, rarest first: terminal [0, .0008), long
# [.0008, .0033), medium [.0033, .0133), short [.0133, .0533), healthy
# [.0533, 1).
def test_roll_daily_health_terminal_band_onsets_terminal() -> None:
    health, events = roll_daily_health(ScriptedRandom(0.0001), "a1", HealthState(), day=10)

    assert health.current_ailment is not None
    assert health.current_ailment.kind is AilmentKind.TERMINAL
    assert health.current_ailment.scheduled_death_day is not None
    assert 13 <= health.current_ailment.scheduled_death_day <= 20
    assert events == [
        LifeEvent(
            type="health.ailment.onset",
            agent_id="a1",
            payload={"kind": "terminal", "onset_day": 10},
        )
    ]


def test_roll_daily_health_short_band_onsets_short_with_one_day_duration() -> None:
    health, events = roll_daily_health(ScriptedRandom(0.02), "a1", HealthState(), day=3)

    assert health.current_ailment is not None
    assert health.current_ailment.kind is AilmentKind.SHORT
    assert health.current_ailment.duration_days == 1
    assert len(events) == 1
    assert events[0].type == "health.ailment.onset"


def test_roll_daily_health_medium_band_onsets_medium_with_bounded_duration() -> None:
    health, _ = roll_daily_health(ScriptedRandom(0.005), "a1", HealthState(), day=0)

    assert health.current_ailment is not None
    assert health.current_ailment.kind is AilmentKind.MEDIUM
    assert 3 <= health.current_ailment.duration_days <= 7


def test_roll_daily_health_long_band_onsets_long_with_twenty_day_duration() -> None:
    health, _ = roll_daily_health(ScriptedRandom(0.001), "a1", HealthState(), day=0)

    assert health.current_ailment is not None
    assert health.current_ailment.kind is AilmentKind.LONG
    assert health.current_ailment.duration_days == 20


def test_roll_daily_health_above_all_bands_stays_healthy() -> None:
    health, events = roll_daily_health(ScriptedRandom(0.9), "a1", HealthState(), day=0)

    assert health.current_ailment is None
    assert events == []


def test_roll_daily_health_is_noop_for_already_sick_agent() -> None:
    sick = HealthState(
        current_ailment=Ailment(kind=AilmentKind.SHORT, onset_day=1, duration_days=1)
    )

    health, events = roll_daily_health(ScriptedRandom(0.0001), "a1", sick, day=2)

    assert health is sick
    assert events == []


def test_work_capacity_is_full_when_healthy() -> None:
    assert work_capacity(HealthState()) == 1.0


@pytest.mark.parametrize(
    "kind,expected",
    [
        (AilmentKind.SHORT, 0.5),
        (AilmentKind.MEDIUM, 0.3),
        (AilmentKind.LONG, 0.15),
        (AilmentKind.TERMINAL, 0.0),
    ],
)
def test_work_capacity_drops_by_ailment_severity(kind: AilmentKind, expected: float) -> None:
    health = HealthState(current_ailment=Ailment(kind=kind, onset_day=0, duration_days=1))
    assert work_capacity(health) == expected


def test_is_recovered_true_once_duration_elapses() -> None:
    ailment = Ailment(kind=AilmentKind.SHORT, onset_day=5, duration_days=1)
    assert is_recovered(HealthState(current_ailment=ailment), day=6) is True


def test_is_recovered_false_before_duration_elapses() -> None:
    ailment = Ailment(kind=AilmentKind.MEDIUM, onset_day=5, duration_days=4)
    assert is_recovered(HealthState(current_ailment=ailment), day=7) is False


def test_is_recovered_false_for_terminal_ailment_regardless_of_day() -> None:
    ailment = Ailment(kind=AilmentKind.TERMINAL, onset_day=0, scheduled_death_day=5)
    assert is_recovered(HealthState(current_ailment=ailment), day=1000) is False


def test_advance_recovery_clears_ailment_once_recovered() -> None:
    ailment = Ailment(kind=AilmentKind.SHORT, onset_day=1, duration_days=1)
    health, events = advance_recovery("a1", HealthState(current_ailment=ailment), day=2)

    assert health.current_ailment is None
    assert events[0].type == "health.ailment.recovered"


def test_advance_recovery_noop_when_not_yet_recovered() -> None:
    ailment = Ailment(kind=AilmentKind.LONG, onset_day=1, duration_days=20)
    health, events = advance_recovery("a1", HealthState(current_ailment=ailment), day=2)

    assert health.current_ailment is ailment
    assert events == []


def patient(ailment: Ailment | None, **overrides: object) -> AgentLifeRecord:
    """One resident, ill or not. `treat_at_pharmacy` takes the whole
    record rather than a bare HealthState (ECON-12) so it can refuse a
    dead one before charging it."""
    base: dict[str, object] = {
        "agent_id": "a1",
        "age_years": 30.0,
        "sex": Sex.MALE,
        "health": HealthState(current_ailment=ailment),
    }
    base.update(overrides)
    return AgentLifeRecord(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_treat_at_pharmacy_cures_short_ailment_and_debits_sim(
    economy: InMemoryEconomy,
) -> None:
    economy.sim_balances["a1"] = 1_000
    ailment = Ailment(kind=AilmentKind.SHORT, onset_day=0, duration_days=1)

    treated, events = await treat_at_pharmacy(patient(ailment), day=0, economy=economy)

    assert treated.health.current_ailment is None
    assert economy.sim_balances["a1"] == 500  # 1000 - 500 pharmacy fee
    assert economy.debits == [("a1", 500, "pharmacy_treatment")]
    assert events[0].type == "health.ailment.treated"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [AilmentKind.MEDIUM, AilmentKind.LONG, AilmentKind.TERMINAL])
async def test_treat_at_pharmacy_rejects_non_short_ailments(
    kind: AilmentKind, economy: InMemoryEconomy
) -> None:
    economy.sim_balances["a1"] = 10_000
    ailment = Ailment(kind=kind, onset_day=0, duration_days=5)

    with pytest.raises(TreatmentUnavailableError):
        await treat_at_pharmacy(patient(ailment), day=0, economy=economy)

    assert economy.debits == []  # no charge for an unavailable treatment


@pytest.mark.asyncio
async def test_treat_at_pharmacy_raises_when_agent_cannot_afford_it(
    economy: InMemoryEconomy,
) -> None:
    economy.sim_balances["a1"] = 10
    ailment = Ailment(kind=AilmentKind.SHORT, onset_day=0, duration_days=1)

    with pytest.raises(InsufficientFundsError):
        await treat_at_pharmacy(patient(ailment), day=0, economy=economy)


@pytest.mark.asyncio
async def test_treat_at_pharmacy_raises_when_no_ailment(economy: InMemoryEconomy) -> None:
    with pytest.raises(ValueError):
        await treat_at_pharmacy(patient(None), day=0, economy=economy)
