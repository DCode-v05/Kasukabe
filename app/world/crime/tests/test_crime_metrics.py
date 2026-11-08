"""Tests for world/crime/metrics.py: the false-conviction rate."""

from __future__ import annotations

from world.crime.metrics import compute_false_conviction_stats, crime_actually_occurred
from world.crime.models import CaseOutcome, CrimeKind, OutcomeKind, WitnessEvent


def make_theft_event(event_id: str, perpetrator: str, succeeded: bool = True) -> WitnessEvent:
    return WitnessEvent(
        witness_event_id=event_id,
        kind=CrimeKind.THEFT,
        tick=0,
        location_id="park",
        perpetrator_agent_id=perpetrator,
        victim_agent_id="victim",
        amount_sim=100,
        theft_succeeded=succeeded,
    )


def make_fine_outcome(case_id: str, warrant_id: str, convicted: str) -> CaseOutcome:
    return CaseOutcome(
        case_id=case_id,
        warrant_id=warrant_id,
        kind=OutcomeKind.FINE,
        convicted_agent_id=convicted,
        fine_sim=100,
        authorized_by_minister_agent_id="minister_m",
        executed_by_officer_agent_id="officer_a",
        decided_tick=5,
    )


def test_true_conviction_when_convicted_matches_ground_truth() -> None:
    event = make_theft_event("witness-1", perpetrator="real-thief")
    outcome = make_fine_outcome("case-1", "warrant-1", convicted="real-thief")
    stats = compute_false_conviction_stats([event], [outcome], {"case-1": "witness-1"})

    assert stats.total_convictions == 1
    assert stats.true_convictions == 1
    assert stats.false_convictions == 0
    assert stats.false_conviction_rate == 0.0


def test_false_conviction_when_convicted_agent_is_the_wrong_one() -> None:
    event = make_theft_event("witness-1", perpetrator="real-thief")
    outcome = make_fine_outcome("case-1", "warrant-1", convicted="innocent-bystander")
    stats = compute_false_conviction_stats([event], [outcome], {"case-1": "witness-1"})

    assert stats.total_convictions == 1
    assert stats.true_convictions == 0
    assert stats.false_convictions == 1
    assert stats.false_conviction_rate == 1.0


def test_conviction_with_no_ground_truth_link_counts_as_false() -> None:
    """A case with no recorded link to any WitnessEvent cannot be proven
    correct, and the metric errs conservative (see metrics.py docstring)."""
    outcome = make_fine_outcome("case-1", "warrant-1", convicted="someone")
    stats = compute_false_conviction_stats([], [outcome], {})

    assert stats.false_convictions == 1
    assert stats.true_convictions == 0


def test_dismissed_outcomes_are_not_counted_as_convictions() -> None:
    dismissed = CaseOutcome(
        case_id="case-1",
        warrant_id=None,
        kind=OutcomeKind.DISMISSED,
        convicted_agent_id=None,
        authorized_by_minister_agent_id=None,
        executed_by_officer_agent_id="officer_a",
        decided_tick=1,
    )
    stats = compute_false_conviction_stats([], [dismissed], {})

    assert stats.total_convictions == 0
    assert stats.false_conviction_rate == 0.0


def test_undetected_crime_counted_when_no_case_ever_linked_to_it() -> None:
    successful_theft = make_theft_event("witness-1", perpetrator="uncaught-thief")
    failed_theft = make_theft_event("witness-2", perpetrator="unlucky-thief", succeeded=False)
    assault = WitnessEvent(
        witness_event_id="witness-3", kind=CrimeKind.ASSAULT, tick=0, location_id="park",
        perpetrator_agent_id="brawler", victim_agent_id="victim",
    )

    stats = compute_false_conviction_stats([successful_theft, failed_theft, assault], [], {})

    # The failed theft caused no loss and is not counted as a real crime;
    # the successful theft and the assault both are.
    assert stats.undetected_crimes == 2


def test_linked_crime_is_not_double_counted_as_undetected() -> None:
    event = make_theft_event("witness-1", perpetrator="real-thief")
    outcome = make_fine_outcome("case-1", "warrant-1", convicted="real-thief")
    stats = compute_false_conviction_stats([event], [outcome], {"case-1": "witness-1"})

    assert stats.undetected_crimes == 0


def test_crime_actually_occurred_helper() -> None:
    assert crime_actually_occurred(make_theft_event("w1", "x", succeeded=True)) is True
    assert crime_actually_occurred(make_theft_event("w2", "x", succeeded=False)) is False
    assault = WitnessEvent(
        witness_event_id="w3", kind=CrimeKind.ASSAULT, tick=0, location_id="park",
        perpetrator_agent_id="a", victim_agent_id="b",
    )
    assert crime_actually_occurred(assault) is True


def test_no_convictions_yields_zero_rate_not_a_division_error() -> None:
    stats = compute_false_conviction_stats([], [], {})
    assert stats.total_convictions == 0
    assert stats.false_conviction_rate == 0.0
