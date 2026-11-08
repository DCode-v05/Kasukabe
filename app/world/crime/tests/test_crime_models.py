"""Tests for world/crime/models.py's own invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from world.crime.models import CaseOutcome, CrimeKind, OutcomeKind, WitnessEvent, hours_to_ticks, new_id


def test_hours_to_ticks_matches_design_ten_minute_tick() -> None:
    # DESIGN.md section 1: tick = 10 game-minutes -> 6 ticks/hour.
    assert hours_to_ticks(1.0) == 6
    assert hours_to_ticks(24.0) == 144  # one full game-day
    assert hours_to_ticks(0.5) == 3


def test_new_id_is_prefixed_and_unique() -> None:
    a = new_id("case")
    b = new_id("case")
    assert a.startswith("case-")
    assert a != b


def test_witness_event_cannot_have_witnesses_when_unwitnessed() -> None:
    with pytest.raises(ValidationError):
        WitnessEvent(
            kind=CrimeKind.THEFT, tick=0, location_id="park",
            perpetrator_agent_id="a", witnessed=False, witness_agent_ids=["b"],
        )


def test_witness_event_rejects_negative_amount() -> None:
    with pytest.raises(ValidationError):
        WitnessEvent(
            kind=CrimeKind.THEFT, tick=0, location_id="park",
            perpetrator_agent_id="a", amount_sim=-1,
        )


def test_fine_outcome_requires_positive_fine_and_convict() -> None:
    with pytest.raises(ValidationError):
        CaseOutcome(
            case_id="c1", warrant_id="w1", kind=OutcomeKind.FINE, convicted_agent_id="a",
            fine_sim=0, authorized_by_minister_agent_id="m", executed_by_officer_agent_id="o",
            decided_tick=0,
        )
    with pytest.raises(ValidationError):
        CaseOutcome(
            case_id="c1", warrant_id="w1", kind=OutcomeKind.FINE, convicted_agent_id=None,
            fine_sim=100, authorized_by_minister_agent_id="m", executed_by_officer_agent_id="o",
            decided_tick=0,
        )


def test_jail_outcome_requires_hours_and_location() -> None:
    with pytest.raises(ValidationError):
        CaseOutcome(
            case_id="c1", warrant_id="w1", kind=OutcomeKind.JAIL, convicted_agent_id="a",
            jail_hours=0, jail_location_id="cell", authorized_by_minister_agent_id="m",
            executed_by_officer_agent_id="o", decided_tick=0,
        )
    with pytest.raises(ValidationError):
        CaseOutcome(
            case_id="c1", warrant_id="w1", kind=OutcomeKind.JAIL, convicted_agent_id="a",
            jail_hours=4.0, jail_location_id="", authorized_by_minister_agent_id="m",
            executed_by_officer_agent_id="o", decided_tick=0,
        )


def test_punitive_outcomes_require_a_warrant_id() -> None:
    with pytest.raises(ValidationError):
        CaseOutcome(
            case_id="c1", warrant_id=None, kind=OutcomeKind.FINE, convicted_agent_id="a",
            fine_sim=100, authorized_by_minister_agent_id="m", executed_by_officer_agent_id="o",
            decided_tick=0,
        )


def test_dismissed_outcome_needs_no_warrant_or_convict() -> None:
    outcome = CaseOutcome(
        case_id="c1", warrant_id=None, kind=OutcomeKind.DISMISSED, convicted_agent_id=None,
        authorized_by_minister_agent_id=None, executed_by_officer_agent_id="o", decided_tick=0,
    )
    assert outcome.warrant_id is None
