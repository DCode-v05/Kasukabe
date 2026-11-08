"""Tests for world/charter/models.py: Charter and GovernmentPolicy
invariants, and the small pure helpers.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from world.charter.models import (
    AMENDABLE_CHARTER_FIELDS,
    Charter,
    GovernmentPolicy,
    GovernmentState,
    days_to_ticks,
    new_id,
)


def test_genesis_charter_builds_with_defaults() -> None:
    charter = Charter()
    assert charter.election_interval_days == 3.0
    assert charter.pm_seats == 1
    assert charter.minister_seats == 4
    assert charter.total_seats == 5
    assert charter.term_limit_terms is None


def test_amendment_votes_required_is_ceiling_division() -> None:
    charter = Charter()
    assert charter.amendment_votes_required == 3


def test_amendment_votes_required_rounds_up_for_odd_seat_counts() -> None:
    charter = Charter(minister_seats=5)
    assert charter.amendment_votes_required == 4


def test_zero_total_seats_rejected() -> None:
    with pytest.raises(ValidationError):
        Charter(pm_seats=0, minister_seats=0)


def test_negative_seats_rejected() -> None:
    with pytest.raises(ValidationError):
        Charter(minister_seats=-1)


def test_amendment_threshold_numerator_cannot_exceed_denominator() -> None:
    with pytest.raises(ValidationError):
        Charter(amendment_threshold_numerator=6, amendment_threshold_denominator=5)


def test_election_windows_must_fit_inside_interval() -> None:
    with pytest.raises(ValidationError):
        Charter(
            election_interval_days=1.0,
            candidacy_window_days=1.0,
            campaign_period_days=1.0,
            voting_window_days=1.0,
        )


def test_multiple_invariant_violations_are_aggregated_in_one_error() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Charter(pm_seats=-1, minister_seats=-1, amendment_threshold_numerator=0)
    message = str(exc_info.value)
    assert "pm_seats" in message
    assert "minister_seats" in message
    assert "amendment_threshold_numerator" in message


def test_term_limit_terms_zero_is_rejected_use_none_for_unlimited() -> None:
    with pytest.raises(ValidationError):
        Charter(term_limit_terms=0)


def test_term_limit_terms_positive_is_valid() -> None:
    charter = Charter(term_limit_terms=2)
    assert charter.term_limit_terms == 2


def test_charter_is_frozen() -> None:
    charter = Charter()
    with pytest.raises(ValidationError):
        charter.pm_seats = 2  # type: ignore[misc]


def test_amendable_fields_excludes_bookkeeping() -> None:
    assert "version" not in AMENDABLE_CHARTER_FIELDS
    assert "genesis_tick" not in AMENDABLE_CHARTER_FIELDS
    assert "election_interval_days" in AMENDABLE_CHARTER_FIELDS
    assert "term_limit_terms" in AMENDABLE_CHARTER_FIELDS


def test_government_policy_defaults_match_design_doc() -> None:
    policy = GovernmentPolicy()
    assert policy.tax_rate_bps == 0
    assert policy.pension_private_pct == 25
    assert policy.pension_government_pct == 50
    assert policy.pension_eligibility_age == 70


def test_government_policy_rejects_out_of_range_tax_rate() -> None:
    with pytest.raises(ValidationError):
        GovernmentPolicy(tax_rate_bps=10_001)


def test_government_state_is_sitting() -> None:
    state = GovernmentState(pm_agent_id="agent-1", minister_agent_ids=["agent-2", "agent-3"])
    assert state.is_sitting("agent-1")
    assert state.is_sitting("agent-2")
    assert not state.is_sitting("agent-4")


def test_government_state_all_seat_holders_orders_pm_first() -> None:
    state = GovernmentState(pm_agent_id="agent-1", minister_agent_ids=["agent-2", "agent-3"])
    assert state.all_seat_holders() == ["agent-1", "agent-2", "agent-3"]


def test_government_state_vacant_seats_have_no_sitting_members() -> None:
    state = GovernmentState()
    assert not state.is_sitting("anyone")
    assert state.all_seat_holders() == []


def test_days_to_ticks_uses_144_ticks_per_game_day() -> None:
    assert days_to_ticks(1.0) == 144
    assert days_to_ticks(3.0) == 432


def test_days_to_ticks_rounds_fractional_days() -> None:
    assert days_to_ticks(0.5) == 72


def test_new_id_is_prefixed_and_unique() -> None:
    first = new_id("election")
    second = new_id("election")
    assert first.startswith("election-")
    assert first != second
