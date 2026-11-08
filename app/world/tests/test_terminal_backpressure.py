"""A run must stop the moment it cannot succeed, not twenty ticks later.

Build Spec v2 objective 3.6, defect D6, threshold 9.7 (at most 20
consecutive identical refusals before dispatch stops).

WHAT THE BARREN-TICK GUARD ALREADY DOES, AND WHAT IT CANNOT. The existing
guard counts consecutive ticks in which every dispatch failed, and it works:
the verification run stopped itself at tick 123 with a clean final
checkpoint. But it counts TICKS, and a tick is ten agents. Twenty barren
ticks is sixty-nine refused calls, measured, against a threshold of twenty.

The distinction that fixes it is between two kinds of failure that the
guard currently treats identically:

  TRANSIENT - a throttle, a network blip, an engine restarting. Ticking
  again is exactly the right response, and the barren-tick guard is the
  right instrument. Nothing here changes that.

  TERMINAL - the spend cap is reached. No amount of waiting makes money
  reappear. Every further dispatch is guaranteed to be refused, and the
  only thing continuing buys is a larger bill for being told no. Round 1
  paid for this lesson 5,155 times in one run.

A terminal condition is knowable: the broker publishes it on
GET /admin/spend. This module is the decision that reads it.

The default is deliberately asymmetric. An unreachable broker, a malformed
payload and an unrecognised state all return None, because "I could not
tell" must never stop a healthy run. Only an explicit halt stops it.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from world.run_genesis import GenesisRunner, terminal_reason_from_spend


# ── The decision itself ──────────────────────────────────────────────


def test_a_halted_broker_is_terminal() -> None:
    reason = terminal_reason_from_spend(
        {"state": "halted", "spent_usd": 5.02, "cap_usd": 5.0, "fraction_used": 1.004}
    )
    assert reason is not None


def test_the_reason_names_the_hop_and_the_numbers() -> None:
    """Gate 3: failing loudly means saying what broke and what to do,
    not printing that something went wrong. An operator reading this line
    should not have to open a database to know whether to raise the cap
    or go looking for a bug."""
    reason = terminal_reason_from_spend(
        {"state": "halted", "spent_usd": 5.02, "cap_usd": 5.0}
    )
    assert "broker" in reason.lower()
    assert "5.02" in reason and "5.00" in reason
    assert "SPEND_CAP_USD" in reason


def test_a_warning_state_does_not_stop_the_run() -> None:
    """80% of the cap is the point of the warning: it exists so a run can
    be watched, not ended."""
    assert terminal_reason_from_spend({"state": "warning", "fraction_used": 0.85}) is None


def test_a_healthy_broker_does_not_stop_the_run() -> None:
    assert terminal_reason_from_spend({"state": "ok", "fraction_used": 0.1}) is None


def test_an_unreachable_broker_is_not_treated_as_terminal() -> None:
    """None is what probe failure looks like. A broker that cannot be
    reached is the barren-tick guard's business - it may be restarting,
    and killing a run over a dropped connection would be worse than the
    defect this module fixes."""
    assert terminal_reason_from_spend(None) is None


def test_a_malformed_payload_is_not_treated_as_terminal() -> None:
    for payload in ({}, {"state": None}, {"state": 7}, [], "halted", {"spent_usd": 1.0}):
        assert terminal_reason_from_spend(payload) is None


def test_an_unknown_state_is_not_treated_as_terminal() -> None:
    """A future broker state this runner has never heard of must not be
    guessed at in the direction of stopping."""
    assert terminal_reason_from_spend({"state": "draining"}) is None


def test_the_check_is_case_insensitive_about_the_state() -> None:
    assert terminal_reason_from_spend({"state": "HALTED", "spent_usd": 1, "cap_usd": 1}) is not None


# ── Gate 1: the decision is reached by the run loop ──────────────────


def run_loop_source() -> str:
    return inspect.getsource(GenesisRunner.run_continuous)


def test_the_run_loop_consults_the_terminal_check() -> None:
    """Severing the wire fails this test.

    The same shape as world/tests/test_track_wiring.py's assertion that
    both composition roots hand movement to the tick engine: a decision
    function nothing calls is the defect, not the fix.
    """
    tree = ast.parse(inspect.getsource(GenesisRunner.run_continuous).strip())
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "broker_terminal_reason" in called, (
        "run_continuous does not consult the terminal-failure check, so a "
        "reached spend cap is still only caught by the barren-tick guard "
        "twenty ticks and sixty-nine refusals later"
    )


def test_the_runner_exposes_the_probe() -> None:
    assert hasattr(GenesisRunner, "broker_terminal_reason")


def test_the_terminal_check_runs_before_the_barren_guard_gives_up() -> None:
    """Ordering is the whole point. A terminal stop that fired after the
    barren guard would save nothing, because the barren guard is what
    currently costs sixty-nine refusals.
    """
    source = run_loop_source()
    assert "broker_terminal_reason" in source
    assert source.index("broker_terminal_reason") < source.index("BARREN_TICKS_BEFORE_STOP"), (
        "the terminal check must be consulted before the barren-tick "
        "threshold, or it cannot reduce the refusal count"
    )


def test_the_guard_is_documented_where_the_threshold_lives() -> None:
    """Spec 9.7 is a number someone will want to change. It must be
    findable from the code that enforces it."""
    text = (Path("world") / "run_genesis.py").read_text(encoding="utf-8")
    assert "terminal_reason_from_spend" in text
