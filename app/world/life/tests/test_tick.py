"""Integration tests for world.life.tick: health.py + death.py + aging.py +
population.py wired together against one AgentLifeRecord, the shape
world's (not-yet-built) tick engine will actually call these with.
"""

from __future__ import annotations

import random

import pytest

from world.life.clock import GameClock
from world.life.models import (
    AgentLifeRecord,
    Ailment,
    AilmentKind,
    DeathCause,
    DormancyState,
    HealthState,
    PopulationDials,
    Sex,
)
from world.life.tick import run_daily_health_tick, run_hourly_life_tick


class AlwaysDies(random.Random):
    def random(self) -> float:
        return 0.0


def make_agent(**overrides: object) -> AgentLifeRecord:
    base: dict[str, object] = {"agent_id": "a1", "age_years": 30.0, "sex": Sex.MALE}
    base.update(overrides)
    return AgentLifeRecord(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_run_daily_health_tick_recovers_before_rolling_a_new_illness() -> None:
    ailment = Ailment(kind=AilmentKind.SHORT, onset_day=1, duration_days=1)
    agent = make_agent(health=HealthState(current_ailment=ailment))

    # A rng whose first draw (recovery has no draw) lands well above every
    # incidence band, so the only thing that happens this tick is recovery.
    updated, events = await run_daily_health_tick(random.Random(0), agent, day=2, dials=PopulationDials())

    assert updated.alive is True
    event_types = [e.type for e in events]
    assert "health.ailment.recovered" in event_types


@pytest.mark.asyncio
async def test_run_daily_health_tick_kills_on_scheduled_terminal_day() -> None:
    ailment = Ailment(kind=AilmentKind.TERMINAL, onset_day=0, scheduled_death_day=5)
    agent = make_agent(health=HealthState(current_ailment=ailment))

    updated, events = await run_daily_health_tick(AlwaysDies(), agent, day=5, dials=PopulationDials())

    assert updated.alive is False
    assert updated.death_cause is DeathCause.TERMINAL
    event_types = [e.type for e in events]
    assert "health.agent.died" in event_types
    assert "agent.died" in event_types


@pytest.mark.asyncio
async def test_run_daily_health_tick_kills_via_elderly_hazard() -> None:
    agent = make_agent(age_years=90.0)

    updated, _ = await run_daily_health_tick(AlwaysDies(), agent, day=0, dials=PopulationDials())

    assert updated.alive is False
    assert updated.death_cause is DeathCause.ELDERLY_HAZARD


@pytest.mark.asyncio
async def test_run_daily_health_tick_is_noop_for_a_dead_agent() -> None:
    agent = make_agent(alive=False, death_cause=DeathCause.TERMINAL)

    updated, events = await run_daily_health_tick(AlwaysDies(), agent, day=0, dials=PopulationDials())

    assert updated is agent
    assert events == []


@pytest.mark.asyncio
async def test_run_daily_health_tick_hazard_multiplier_zero_keeps_terminal_agent_alive() -> None:
    ailment = Ailment(kind=AilmentKind.TERMINAL, onset_day=0, scheduled_death_day=1)
    agent = make_agent(health=HealthState(current_ailment=ailment))
    dials = PopulationDials(death_hazard_multiplier=0.0)

    updated, _ = await run_daily_health_tick(AlwaysDies(), agent, day=99, dials=dials)

    assert updated.alive is True


@pytest.mark.asyncio
async def test_run_hourly_life_tick_ages_a_minor() -> None:
    agent = make_agent(age_years=10.0)
    updated, events = await run_hourly_life_tick(
        random.Random(0),
        agent,
        now=GameClock(total_hours=5.0),
        elapsed_hours=2.0,
        dials=PopulationDials(),
    )
    assert updated.age_years == 12.0
    assert events == []


@pytest.mark.asyncio
async def test_run_hourly_life_tick_kills_on_dormancy_timeout() -> None:
    dormancy = DormancyState(is_dormant=True, dormant_since_hours=0.0)
    agent = make_agent(dormancy=dormancy)

    updated, events = await run_hourly_life_tick(
        AlwaysDies(),
        agent,
        now=GameClock(total_hours=24.0),
        elapsed_hours=1.0,
        dials=PopulationDials(),
    )

    assert updated.alive is False
    assert updated.death_cause is DeathCause.USD_ZERO_UNREVIVED
    assert any(e.type == "agent.died" for e in events)


@pytest.mark.asyncio
async def test_run_hourly_life_tick_is_noop_for_a_dead_agent() -> None:
    agent = make_agent(alive=False)
    updated, events = await run_hourly_life_tick(
        AlwaysDies(), agent, now=GameClock(), elapsed_hours=1.0, dials=PopulationDials()
    )
    assert updated is agent
    assert events == []
