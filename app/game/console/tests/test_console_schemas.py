"""Validation edge cases for schemas.py's pydantic models - the ones with
non-trivial invariants (scope targeting, dial ordering, the silent-deny
visibility contract)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from game.console.schemas import (
    ApprovePetitionRequest,
    DisasterScope,
    DisasterScopeType,
    PopulationDialsUpdate,
    PrayerDecisionView,
    SpendStatusView,
)


def test_disaster_scope_town_needs_no_target() -> None:
    scope = DisasterScope(scope_type=DisasterScopeType.TOWN)
    assert scope.target is None


def test_disaster_scope_district_requires_a_target() -> None:
    with pytest.raises(ValidationError):
        DisasterScope(scope_type=DisasterScopeType.DISTRICT)


def test_disaster_scope_zone_requires_a_target() -> None:
    with pytest.raises(ValidationError):
        DisasterScope(scope_type=DisasterScopeType.ZONE, target="")


def test_disaster_scope_district_with_target_is_valid() -> None:
    scope = DisasterScope(scope_type=DisasterScopeType.DISTRICT, target="farmland-3")
    assert scope.target == "farmland-3"


def test_approve_petition_requires_positive_depth() -> None:
    with pytest.raises(ValidationError):
        ApprovePetitionRequest(edge="south", depth=0)


def test_approve_petition_accepts_south_and_east_only() -> None:
    ApprovePetitionRequest(edge="south", depth=5)
    ApprovePetitionRequest(edge="east", depth=5)
    with pytest.raises(ValidationError):
        ApprovePetitionRequest(edge="north", depth=5)  # type: ignore[arg-type]


def test_population_dials_update_rejects_min_above_max() -> None:
    with pytest.raises(ValidationError):
        PopulationDialsUpdate(target_band_min=400, target_band_max=300)


def test_population_dials_update_allows_partial_fields() -> None:
    update = PopulationDialsUpdate(death_hazard_multiplier=0.0)
    assert update.target_band_min is None
    assert update.death_hazard_multiplier == 0.0


def test_population_dials_update_rejects_out_of_range_throttle() -> None:
    with pytest.raises(ValidationError):
        PopulationDialsUpdate(blessing_throttle=1.5)


def test_prayer_decision_granted_must_be_visible() -> None:
    with pytest.raises(ValidationError):
        PrayerDecisionView(
            prayer_id="p1", decision="granted", visible_to_agent=False, decided_tick=1
        )


def test_prayer_decision_silent_deny_must_not_be_visible() -> None:
    with pytest.raises(ValidationError):
        PrayerDecisionView(
            prayer_id="p1", decision="silently_denied", visible_to_agent=True, decided_tick=1
        )


def test_spend_status_accepts_pools_that_sum_to_budget() -> None:
    status = SpendStatusView(
        burned_usd_cents=100,
        treasury_usd_cents=49_900,
        wallets_usd_cents=50_000,
        budget_total_usd_cents=100_000,
        tick_index=1,
        sampled_at="2026-07-31T00:00:00+00:00",
    )
    assert status.burned_usd_cents == 100


def test_spend_status_rejects_pools_that_do_not_sum_to_budget() -> None:
    with pytest.raises(ValidationError):
        SpendStatusView(
            burned_usd_cents=100,
            treasury_usd_cents=49_000,
            wallets_usd_cents=50_000,
            budget_total_usd_cents=100_000,
            tick_index=1,
            sampled_at="2026-07-31T00:00:00+00:00",
        )


def test_prayer_decision_consistent_pairs_are_valid() -> None:
    granted = PrayerDecisionView(
        prayer_id="p1", decision="granted", visible_to_agent=True, decided_tick=1
    )
    denied = PrayerDecisionView(
        prayer_id="p2", decision="silently_denied", visible_to_agent=False, decided_tick=1
    )
    assert granted.visible_to_agent is True
    assert denied.visible_to_agent is False
