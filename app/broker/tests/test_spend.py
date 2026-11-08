"""SpendGovernor: process-wide hard USD cap computed from real usage
only - see broker/spend.py's module docstring for why ONLY the exact
post-response cost (never a pre-dispatch estimate) ever reaches
record_spend, and for why HALTED can only be discovered after the call
that crossed it has already been billed."""

from __future__ import annotations

import pytest

from broker.spend import NANO_PER_DOLLAR, SpendGovernor, SpendState


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_starts_in_normal_state_with_zero_spend() -> None:
    governor = SpendGovernor(cap_usd=400.0)
    snapshot = governor.snapshot()
    assert snapshot.state == SpendState.NORMAL
    assert snapshot.spent_usd == 0.0
    assert governor.should_block_dispatch() is False


def test_cap_nano_dollars_is_an_exact_integer_conversion() -> None:
    governor = SpendGovernor(cap_usd=400.0)
    assert governor.cap_nano_dollars == 400 * NANO_PER_DOLLAR


def test_record_spend_below_warn_fraction_stays_normal() -> None:
    governor = SpendGovernor(cap_usd=100.0, warn_fraction=0.8)
    state = governor.record_spend(50 * NANO_PER_DOLLAR)
    assert state == SpendState.NORMAL
    assert governor.should_block_dispatch() is False


def test_record_spend_crossing_warn_fraction_transitions_to_warning() -> None:
    governor = SpendGovernor(cap_usd=100.0, warn_fraction=0.8)
    state = governor.record_spend(81 * NANO_PER_DOLLAR)
    assert state == SpendState.WARNING
    assert governor.should_block_dispatch() is False  # warning still dispatches


def test_record_spend_reaching_cap_halts_and_blocks_dispatch() -> None:
    governor = SpendGovernor(cap_usd=100.0, warn_fraction=0.8)
    state = governor.record_spend(100 * NANO_PER_DOLLAR)
    assert state == SpendState.HALTED
    assert governor.should_block_dispatch() is True


def test_record_spend_accumulates_across_multiple_calls() -> None:
    governor = SpendGovernor(cap_usd=100.0)
    governor.record_spend(30 * NANO_PER_DOLLAR)
    governor.record_spend(30 * NANO_PER_DOLLAR)
    governor.record_spend(30 * NANO_PER_DOLLAR)
    snapshot = governor.snapshot()
    assert snapshot.spent_usd == pytest.approx(90.0)
    assert snapshot.state == SpendState.WARNING  # 90% >= 80% default warn_fraction


def test_record_spend_is_a_no_op_for_zero_or_negative_cost() -> None:
    governor = SpendGovernor(cap_usd=100.0)

    result = governor.record_spend(0)
    assert result == SpendState.NORMAL
    assert governor.snapshot().spent_usd == 0.0

    result = governor.record_spend(-5)
    assert result == SpendState.NORMAL
    assert governor.snapshot().spent_usd == 0.0


def test_once_halted_more_spend_cannot_undo_it() -> None:
    governor = SpendGovernor(cap_usd=100.0)
    governor.record_spend(100 * NANO_PER_DOLLAR)
    state = governor.record_spend(5 * NANO_PER_DOLLAR)
    assert state == SpendState.HALTED
    assert governor.should_block_dispatch() is True


def test_fraction_used_is_clamped_to_one_past_the_cap() -> None:
    governor = SpendGovernor(cap_usd=100.0)
    governor.record_spend(150 * NANO_PER_DOLLAR)
    assert governor.snapshot().fraction_used == 1.0


def test_burn_rate_reflects_spend_over_elapsed_time() -> None:
    clock = FakeClock()
    governor = SpendGovernor(cap_usd=400.0, clock=clock)
    clock.advance(3600.0)  # exactly one hour
    governor.record_spend(10 * NANO_PER_DOLLAR)

    assert governor.snapshot().burn_rate_usd_per_hr == pytest.approx(10.0, rel=1e-6)


def test_projected_exhaustion_extrapolates_from_the_current_burn_rate() -> None:
    clock = FakeClock()
    governor = SpendGovernor(cap_usd=100.0, clock=clock)
    clock.advance(3600.0)  # one hour elapsed
    governor.record_spend(10 * NANO_PER_DOLLAR)  # $10/hr burn rate, $90 remaining

    assert governor.snapshot().projected_exhaustion_s == pytest.approx(9 * 3600.0, rel=1e-6)


def test_projected_exhaustion_is_none_once_halted() -> None:
    governor = SpendGovernor(cap_usd=100.0)
    governor.record_spend(100 * NANO_PER_DOLLAR)
    assert governor.snapshot().projected_exhaustion_s is None


def test_projected_exhaustion_is_none_with_zero_burn_rate() -> None:
    governor = SpendGovernor(cap_usd=100.0)
    assert governor.snapshot().projected_exhaustion_s is None


def test_snapshot_cap_and_state_round_trip() -> None:
    governor = SpendGovernor(cap_usd=250.0, warn_fraction=0.5)
    snapshot = governor.snapshot()
    assert snapshot.cap_usd == 250.0
    assert snapshot.state is SpendState.NORMAL
