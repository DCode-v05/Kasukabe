"""Tests for ActivityManager session lifecycle tracking.

Covers:
- State machine transitions: active→idle→away→disconnected
- Reconnect from idle/away/disconnected
- Tool call and error recording
- ReconnectSummary contents
- ActivityStateChanged event emission
- Configurable thresholds
- Bootstrap integration (ActivityManager in EngineState)
- Session stats accumulation

All tests use real asyncio and a real EventEmitter; no mocks, no stubs.
"""

from __future__ import annotations

import asyncio
import pytest

from src.utils.activity_manager import (
    ActivityManager,
    ActivityState,
    ReconnectSummary,
    get_activity_manager,
)
from src.engine_core.events import ActivityStateChanged, EventEmitter


def make_clock(start: float = 0.0):
    """Returns a mutable fake clock for injecting controlled time."""
    t = [start]

    def get_now() -> float:
        return t[0]

    def advance(seconds: float) -> None:
        t[0] += seconds

    return get_now, advance


def make_manager(
    idle_s: float = 300.0,
    away_s: float = 1800.0,
    disconnect_s: float = 3600.0,
    emitter: EventEmitter | None = None,
) -> tuple[ActivityManager, callable]:
    """Create an ActivityManager with an injectable clock."""
    get_now, advance = make_clock()
    am = ActivityManager(
        emitter=emitter,
        idle_timeout_s=idle_s,
        away_timeout_s=away_s,
        disconnect_timeout_s=disconnect_s,
        poll_interval_s=9999.0,
        get_now=get_now,
    )
    return am, advance


def test_initial_state_is_active():
    am, _ = make_manager()
    assert am.state == ActivityState.ACTIVE


def test_no_transition_before_idle_threshold():
    am, advance = make_manager(idle_s=300.0)
    advance(299.0)
    am.check_transitions()
    assert am.state == ActivityState.ACTIVE


def test_active_to_idle_at_threshold():
    am, advance = make_manager(idle_s=300.0)
    advance(300.0)
    am.check_transitions()
    assert am.state == ActivityState.IDLE


def test_idle_to_away_at_threshold():
    am, advance = make_manager(idle_s=300.0, away_s=1800.0)
    advance(300.0)
    am.check_transitions()
    assert am.state == ActivityState.IDLE
    advance(1500.0)
    am.check_transitions()
    assert am.state == ActivityState.AWAY


def test_away_to_disconnected_at_threshold():
    am, advance = make_manager(idle_s=300.0, away_s=1800.0, disconnect_s=3600.0)
    advance(3600.0)
    am.check_transitions()
    assert am.state == ActivityState.IDLE
    am.check_transitions()
    assert am.state == ActivityState.AWAY
    am.check_transitions()
    assert am.state == ActivityState.DISCONNECTED


def test_no_skip_states_on_single_advance():
    """Going directly from active to away threshold skips idle but does hit IDLE
    on the first poll, then AWAY is reached only after another poll at the right
    time — each check_transitions call only steps one level."""
    am, advance = make_manager(idle_s=300.0, away_s=1800.0)
    advance(1800.0)
    am.check_transitions()
    assert am.state == ActivityState.IDLE
    am.check_transitions()
    assert am.state == ActivityState.AWAY


def test_record_user_activity_resets_to_active_from_idle():
    am, advance = make_manager(idle_s=300.0)
    advance(300.0)
    am.check_transitions()
    assert am.state == ActivityState.IDLE

    result = am.record_user_activity()
    assert result is None
    assert am.state == ActivityState.ACTIVE


def test_record_user_activity_returns_summary_from_away():
    am, advance = make_manager(idle_s=300.0, away_s=1800.0)
    advance(1800.0)
    am.check_transitions()
    am.check_transitions()
    assert am.state == ActivityState.AWAY

    result = am.record_user_activity()
    assert isinstance(result, ReconnectSummary)
    assert result.previous_state == ActivityState.AWAY
    assert result.idle_seconds >= 1800.0
    assert am.state == ActivityState.ACTIVE


def test_record_user_activity_returns_summary_from_disconnected():
    am, advance = make_manager(idle_s=300.0, away_s=1800.0, disconnect_s=3600.0)
    advance(3600.0)
    am.check_transitions()
    am.check_transitions()
    am.check_transitions()
    assert am.state == ActivityState.DISCONNECTED

    result = am.record_user_activity()
    assert isinstance(result, ReconnectSummary)
    assert result.previous_state == ActivityState.DISCONNECTED
    assert am.state == ActivityState.ACTIVE


def test_record_user_activity_active_no_summary():
    am, advance = make_manager()
    advance(10.0)
    result = am.record_user_activity()
    assert result is None
    assert am.state == ActivityState.ACTIVE


def test_tool_calls_accumulate():
    am, _ = make_manager()
    am.record_tool_call("bash")
    am.record_tool_call("file_read")
    am.record_tool_call("bash")
    stats = am.snapshot_stats()
    assert stats.tool_calls == 3


def test_errors_accumulate():
    am, _ = make_manager()
    am.record_error()
    am.record_error()
    stats = am.snapshot_stats()
    assert stats.errors == 2


def test_stats_reset_on_reconnect_from_away():
    am, advance = make_manager(idle_s=300.0, away_s=1800.0)
    am.record_tool_call("bash")
    am.record_error()
    advance(1800.0)
    am.check_transitions()
    am.check_transitions()
    assert am.state == ActivityState.AWAY

    summary = am.record_user_activity()
    assert summary is not None
    assert summary.stats.tool_calls == 1
    assert summary.stats.errors == 1
    fresh = am.snapshot_stats()
    assert fresh.tool_calls == 0
    assert fresh.errors == 0


def test_record_tool_call_start_end_accumulates_duration():
    am, advance = make_manager()
    am.record_tool_call_start("op-1")
    advance(5.0)
    am.record_tool_call_end("op-1")
    stats = am.snapshot_stats()
    assert stats.engine_active_seconds == pytest.approx(5.0, abs=0.1)


def test_record_tool_call_end_unknown_op_is_noop():
    am, _ = make_manager()
    am.record_tool_call_end("not-started")
    stats = am.snapshot_stats()
    assert stats.engine_active_seconds == 0.0


def test_record_shutdown_transitions_to_disconnected():
    am, _ = make_manager()
    am.record_shutdown()
    assert am.state == ActivityState.DISCONNECTED


def test_idle_seconds_measures_since_last_activity():
    am, advance = make_manager()
    am.record_user_activity()
    advance(123.0)
    assert am.idle_seconds() == pytest.approx(123.0, abs=0.1)


@pytest.mark.asyncio
async def test_activity_state_changed_event_emitted_on_idle():
    emitter = EventEmitter()
    collected: list[ActivityStateChanged] = []

    async def sink(event):
        if isinstance(event, ActivityStateChanged):
            collected.append(event)

    emitter.on(sink)

    am, advance = make_manager(idle_s=300.0, emitter=emitter)
    advance(300.0)
    await am.emit_transition(ActivityState.ACTIVE, ActivityState.IDLE, am.get_now())

    assert len(collected) == 1
    ev = collected[0]
    assert ev.old_state == "active"
    assert ev.new_state == "idle"


@pytest.mark.asyncio
async def test_activity_state_changed_event_emitted_on_reconnect():
    emitter = EventEmitter()
    collected: list[ActivityStateChanged] = []

    async def sink(event):
        if isinstance(event, ActivityStateChanged):
            collected.append(event)

    emitter.on(sink)
    am, advance = make_manager(idle_s=300.0, away_s=1800.0, emitter=emitter)

    await am.emit_transition(ActivityState.ACTIVE, ActivityState.IDLE, am.get_now())
    await am.emit_transition(ActivityState.IDLE, ActivityState.AWAY, am.get_now())
    await am.emit_transition(ActivityState.AWAY, ActivityState.ACTIVE, am.get_now())

    states = [(e.old_state, e.new_state) for e in collected]
    assert ("active", "idle") in states
    assert ("idle", "away") in states
    assert ("away", "active") in states


def test_get_instance_returns_singleton():
    ActivityManager.reset_instance()
    a = get_activity_manager()
    b = get_activity_manager()
    assert a is b
    ActivityManager.reset_instance()


def test_set_instance_replaces_singleton():
    ActivityManager.reset_instance()
    custom, _ = make_manager()
    ActivityManager.set_instance(custom)
    assert get_activity_manager() is custom
    ActivityManager.reset_instance()


def test_reset_instance_clears_singleton():
    _ = get_activity_manager()
    ActivityManager.reset_instance()
    assert ActivityManager.instance is None


def test_custom_idle_threshold():
    am, advance = make_manager(idle_s=60.0)
    advance(59.0)
    am.check_transitions()
    assert am.state == ActivityState.ACTIVE
    advance(1.0)
    am.check_transitions()
    assert am.state == ActivityState.IDLE


def test_custom_away_threshold():
    am, advance = make_manager(idle_s=60.0, away_s=120.0)
    advance(120.0)
    am.check_transitions()
    am.check_transitions()
    assert am.state == ActivityState.AWAY


@pytest.mark.asyncio
async def test_start_and_stop_does_not_raise():
    am, _ = make_manager()
    am.poll_interval_s = 0.01
    await am.start()
    await asyncio.sleep(0.05)
    await am.stop()
    assert am.bg_task is None or am.bg_task.done()


@pytest.mark.asyncio
async def test_double_start_is_idempotent():
    am, _ = make_manager()
    am.poll_interval_s = 9999.0
    await am.start()
    first_task = am.bg_task
    await am.start()
    assert am.bg_task is first_task
    await am.stop()


def test_get_reconnect_summary_reflects_current_state():
    am, advance = make_manager(idle_s=300.0, away_s=1800.0)
    am.record_tool_call("bash")
    advance(1800.0)
    am.check_transitions()
    am.check_transitions()

    summary = am.get_reconnect_summary()
    assert summary.previous_state == ActivityState.AWAY
    assert summary.stats.tool_calls == 1
    assert summary.idle_seconds >= 1800.0


def test_activity_state_changed_is_valid_event():
    """Verify ActivityStateChanged is part of the Event union (import resolves)."""
    from src.engine_core.events import ActivityStateChanged as ev_cls

    ev = ev_cls(old_state="active", new_state="idle", idle_seconds=300.0)
    assert ev.old_state == "active"
    assert ev.new_state == "idle"
    assert ev.idle_seconds == 300.0
    assert ev.tool_calls == 0
    assert ev.errors == 0
    assert ev.user_active_seconds == 0.0
    assert ev.engine_active_seconds == 0.0


def test_user_active_seconds_accumulates_bursty_input():
    am, advance = make_manager()
    am.record_user_activity()
    advance(2.0)
    am.record_user_activity()
    advance(3.0)
    am.record_user_activity()
    stats = am.snapshot_stats()
    assert stats.user_active_seconds == pytest.approx(5.0, abs=0.1)


def test_user_active_seconds_skips_gaps_beyond_window():
    am, advance = make_manager()
    am.record_user_activity()
    advance(10.0)
    am.record_user_activity()
    advance(2.0)
    am.record_user_activity()
    stats = am.snapshot_stats()
    assert stats.user_active_seconds == pytest.approx(2.0, abs=0.1)


def test_engine_active_seconds_sums_tool_durations():
    am, advance = make_manager()
    am.record_tool_call_start("op-1")
    advance(1.5)
    am.record_tool_call_end("op-1")
    am.record_tool_call_start("op-2")
    advance(2.5)
    am.record_tool_call_end("op-2")
    stats = am.snapshot_stats()
    assert stats.engine_active_seconds == pytest.approx(4.0, abs=0.1)


def test_dual_buckets_reset_on_reconnect_from_away():
    am, advance = make_manager(idle_s=1.0, away_s=2.0)
    am.record_user_activity()
    advance(1.0)
    am.record_user_activity()
    am.record_tool_call_start("op")
    advance(2.0)
    am.check_transitions()
    am.check_transitions()
    am.record_tool_call_end("op")
    assert am.state == ActivityState.AWAY
    summary = am.record_user_activity()
    assert summary is not None
    assert summary.stats.user_active_seconds > 0
    assert summary.stats.engine_active_seconds > 0
    fresh = am.snapshot_stats()
    assert fresh.user_active_seconds == 0.0
    assert fresh.engine_active_seconds == 0.0


@pytest.mark.asyncio
async def test_reconnect_transition_event_carries_real_counters():
    """Regression: ActivityStateChanged for away→active must carry the
    pre-reset counters, not the post-reset zeros.  Raven subscribes to
    this event and would otherwise record blank counters for the most
    observability-relevant transition.
    """
    emitter = EventEmitter()
    captured: list = []

    async def sink(event):
        if isinstance(event, ActivityStateChanged):
            captured.append(event)

    emitter.on(sink)

    am, advance = make_manager(idle_s=1.0, away_s=2.0, emitter=emitter)
    am.record_tool_call("bash")
    am.record_tool_call("grep")
    am.record_error()
    am.record_tool_call_start("op")
    advance(2.5)
    am.record_tool_call_end("op")
    am.check_transitions()
    am.check_transitions()
    await asyncio.sleep(0.05)
    captured.clear()
    assert am.state == ActivityState.AWAY

    summary = am.record_user_activity()
    assert summary is not None
    await asyncio.sleep(0.05)

    reconnect = next(e for e in captured if e.new_state == "active")
    assert reconnect.tool_calls == 3, f"got {reconnect.tool_calls}"
    assert reconnect.errors == 1, f"got {reconnect.errors}"
    assert reconnect.engine_active_seconds == pytest.approx(2.5, abs=0.1)
    assert reconnect.user_active_seconds == pytest.approx(2.5, abs=0.1)
    assert reconnect.tool_calls == summary.stats.tool_calls
    assert reconnect.errors == summary.stats.errors
    assert reconnect.engine_active_seconds == summary.stats.engine_active_seconds


def test_format_recap_from_summary():
    am, advance = make_manager(idle_s=5.0, away_s=10.0)
    am.record_tool_call("bash")
    am.record_tool_call("file_read")
    am.record_error()
    advance(10.0)
    am.check_transitions()
    am.check_transitions()
    assert am.state == ActivityState.AWAY
    summary = am.record_user_activity()
    assert summary is not None
    recap = summary.format_recap()
    assert "While you were away" in recap
    assert "2 tool calls" in recap
    assert "1 error" in recap
