"""Tests for world.life.aging: minors age 1 year/game-hour until 18, adults static."""

from __future__ import annotations

import pytest

from world.life.aging import advance_age
from world.life.models import AgentLifeRecord, Sex


def make_agent(age_years: float, **overrides: object) -> AgentLifeRecord:
    return AgentLifeRecord(agent_id="a1", age_years=age_years, sex=Sex.MALE, **overrides)  # type: ignore[arg-type]


def test_advance_age_ages_a_minor_one_year_per_game_hour() -> None:
    agent, events = advance_age(make_agent(10.0), elapsed_hours=3.0)

    assert agent.age_years == 13.0
    assert events == []


def test_advance_age_is_a_noop_for_adults() -> None:
    agent, events = advance_age(make_agent(30.0), elapsed_hours=100.0)

    assert agent.age_years == 30.0
    assert events == []


def test_advance_age_caps_exactly_at_18_and_fires_adulthood_event() -> None:
    agent, events = advance_age(make_agent(17.5), elapsed_hours=1.0)

    assert agent.age_years == 18.0
    assert agent.is_minor is False
    assert events[0].type == "agent.aged_to_adult"


def test_advance_age_clears_sponsor_parent_on_reaching_adulthood() -> None:
    agent, _ = advance_age(make_agent(17.9, sponsor_parent_id="parent1"), elapsed_hours=1.0)

    assert agent.sponsor_parent_id is None


def test_advance_age_landing_exactly_on_18_transitions_to_adult() -> None:
    agent, events = advance_age(make_agent(15.0), elapsed_hours=3.0)

    assert agent.age_years == 18.0
    assert agent.is_minor is False
    assert len(events) == 1


def test_advance_age_rejects_negative_elapsed_hours() -> None:
    with pytest.raises(ValueError):
        advance_age(make_agent(10.0), elapsed_hours=-1.0)
