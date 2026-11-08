"""Tests for CacheHitMonitor — per-turn cache ratio tracking and alerting.

Covers:
  - No alert when cache is healthy
  - RATIO_LOW alert: cache_ratio < 0.5 only from turn 2 onward
  - PREFIX_UNSTABLE alert: cache_read drops > 10% AND > 2000 tokens
  - Both alerts fire simultaneously when both conditions hold
  - Turn 1 never fires RATIO_LOW (guard)
  - Exactly-at-threshold values do not trigger alerts
  - Monitor resets correctly across successive observe_turn() calls
  - Zero-token turns handled safely
  - Absolute token floor: small relative drops below 2000 tokens don't fire
  - Compaction suppression: suppress_next_drop() skips one PREFIX_UNSTABLE
  - TTL-awareness: elapsed_ms classifies drops as 5min/1h TTL expiry
  - Cause attribution: model change, tool count change, or unknown
  - Feature flag: Settings.cache_hit_monitor_enabled default, env override
  - Through the real agent loop: alerts reach the emitter as CacheHitRatioAlert
    events, and the feature flag actually gates them
"""

from __future__ import annotations

import pytest

from src.config import Settings
from src.engine_core.cache_hit_monitor import (
    CACHE_TTL_1HOUR_MS,
    CACHE_TTL_5MIN_MS,
    CacheAlertType,
    CacheHitMonitor,
)
from src.engine_core.events import CacheHitRatioAlert
from src.herald.types import StopReason, StreamEvent, ToolCall, Usage


def healthy_turn(monitor: CacheHitMonitor, turn: int) -> None:
    """Feed a turn with a healthy 80% cache hit ratio — expects no alerts."""
    alerts = monitor.observe_turn(turn=turn, cache_read=8000, cache_create=1000, uncached=1000)
    assert alerts == [], f"Expected no alerts on healthy turn {turn}, got {alerts}"


class TestNoAlert:
    def test_first_turn_healthy_no_alert(self):
        m = CacheHitMonitor()
        alerts = m.observe_turn(turn=1, cache_read=8000, cache_create=1000, uncached=1000)
        assert alerts == []

    def test_ratio_above_threshold_no_ratio_low(self):
        """ratio >= 0.5 should never fire RATIO_LOW."""
        m = CacheHitMonitor()
        healthy_turn(m, turn=1)
        alerts = m.observe_turn(turn=2, cache_read=5000, cache_create=0, uncached=5000)
        ratio_low = [a for a in alerts if a.alert_type == CacheAlertType.RATIO_LOW]
        assert ratio_low == []

    def test_small_drop_below_threshold_no_prefix_alert(self):
        """A 5% drop is well below the 10% threshold — no PREFIX_UNSTABLE."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        alerts = m.observe_turn(turn=2, cache_read=9500, cache_create=0, uncached=0)
        prefix = [a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE]
        assert prefix == []

    def test_exactly_10_pct_drop_no_alert(self):
        """Exactly 10% drop should NOT trigger (threshold is strictly >10%)."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        alerts = m.observe_turn(turn=2, cache_read=9000, cache_create=0, uncached=0)
        prefix = [a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE]
        assert prefix == []


class TestRatioLow:
    def test_ratio_low_fires_from_turn_2(self):
        m = CacheHitMonitor()
        healthy_turn(m, turn=1)
        alerts = m.observe_turn(turn=2, cache_read=1000, cache_create=0, uncached=9000)
        types = [a.alert_type for a in alerts]
        assert CacheAlertType.RATIO_LOW in types

    def test_ratio_low_not_fired_on_turn_1(self):
        m = CacheHitMonitor()
        alerts = m.observe_turn(turn=1, cache_read=100, cache_create=0, uncached=9900)
        ratio_low = [a for a in alerts if a.alert_type == CacheAlertType.RATIO_LOW]
        assert ratio_low == []

    def test_ratio_low_fires_on_turn_3_and_beyond(self):
        m = CacheHitMonitor()
        healthy_turn(m, turn=1)
        healthy_turn(m, turn=2)
        alerts = m.observe_turn(turn=3, cache_read=100, cache_create=0, uncached=9900)
        types = [a.alert_type for a in alerts]
        assert CacheAlertType.RATIO_LOW in types

    def test_ratio_low_alert_has_correct_fields(self):
        m = CacheHitMonitor()
        healthy_turn(m, turn=1)
        alerts = m.observe_turn(turn=2, cache_read=1000, cache_create=500, uncached=8500)
        ratio_low = next(a for a in alerts if a.alert_type == CacheAlertType.RATIO_LOW)
        assert ratio_low.turn == 2
        assert ratio_low.cache_read == 1000
        assert ratio_low.cache_create == 500
        assert ratio_low.uncached == 8500
        assert ratio_low.message

    def test_ratio_low_not_fired_when_no_cached_tokens(self):
        """If neither cache_read nor cache_create > 0, skip the ratio alert
        (nothing was ever submitted for caching — no signal to act on)."""
        m = CacheHitMonitor()
        healthy_turn(m, turn=1)
        alerts = m.observe_turn(turn=2, cache_read=0, cache_create=0, uncached=10000)
        ratio_low = [a for a in alerts if a.alert_type == CacheAlertType.RATIO_LOW]
        assert ratio_low == []


class TestPrefixUnstable:
    def test_prefix_unstable_fires_on_large_drop(self):
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        alerts = m.observe_turn(turn=2, cache_read=0, cache_create=0, uncached=10000)
        types = [a.alert_type for a in alerts]
        assert CacheAlertType.PREFIX_UNSTABLE in types

    def test_prefix_unstable_fires_on_11_pct_drop_above_floor(self):
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=50000, cache_create=0, uncached=0)
        alerts = m.observe_turn(turn=2, cache_read=44000, cache_create=0, uncached=0)
        types = [a.alert_type for a in alerts]
        assert CacheAlertType.PREFIX_UNSTABLE in types

    def test_prefix_unstable_not_fired_on_first_turn(self):
        """No previous reading — can't compute a drop on turn 1."""
        m = CacheHitMonitor()
        alerts = m.observe_turn(turn=1, cache_read=0, cache_create=0, uncached=10000)
        prefix = [a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE]
        assert prefix == []

    def test_prefix_unstable_not_fired_when_prev_was_zero(self):
        """If previous cache_read was 0, drop% is undefined — no alert."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=0, cache_create=0, uncached=10000)
        alerts = m.observe_turn(turn=2, cache_read=0, cache_create=0, uncached=10000)
        prefix = [a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE]
        assert prefix == []

    def test_prefix_unstable_alert_carries_drop_pct(self):
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        alerts = m.observe_turn(turn=2, cache_read=5000, cache_create=0, uncached=0)
        prefix = next(a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE)
        assert prefix.prev_cache_read == 10000
        assert prefix.drop_pct == pytest.approx(0.5)

    def test_prefix_unstable_increase_never_triggers(self):
        """Cache reads increasing is good — no alert."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        alerts = m.observe_turn(turn=2, cache_read=20000, cache_create=0, uncached=0)
        prefix = [a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE]
        assert prefix == []


class TestBothAlerts:
    def test_both_fire_on_large_drop_with_low_ratio(self):
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        alerts = m.observe_turn(turn=2, cache_read=200, cache_create=0, uncached=9800)
        types = {a.alert_type for a in alerts}
        assert CacheAlertType.PREFIX_UNSTABLE in types
        assert CacheAlertType.RATIO_LOW in types
        assert len(alerts) == 2


class TestZeroTokens:
    def test_all_zeros_no_crash(self):
        m = CacheHitMonitor()
        alerts = m.observe_turn(turn=1, cache_read=0, cache_create=0, uncached=0)
        assert alerts == []

    def test_all_zeros_turn_2_no_crash(self):
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=0, cache_create=0, uncached=0)
        alerts = m.observe_turn(turn=2, cache_read=0, cache_create=0, uncached=0)
        assert alerts == []


class TestStateIsolation:
    def test_two_monitors_are_independent(self):
        m1 = CacheHitMonitor()
        m2 = CacheHitMonitor()

        m1.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        m2.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)

        alerts_m1 = m1.observe_turn(turn=2, cache_read=0, cache_create=0, uncached=10000)
        alerts_m2 = m2.observe_turn(turn=2, cache_read=10000, cache_create=0, uncached=0)

        assert any(a.alert_type == CacheAlertType.PREFIX_UNSTABLE for a in alerts_m1)
        assert not any(a.alert_type == CacheAlertType.PREFIX_UNSTABLE for a in alerts_m2)

    def test_prev_cache_read_advances_each_turn(self):
        """The monitor tracks only the immediate previous turn, not a rolling max."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        alerts2 = m.observe_turn(turn=2, cache_read=5000, cache_create=0, uncached=5000)
        assert any(a.alert_type == CacheAlertType.PREFIX_UNSTABLE for a in alerts2)
        alerts3 = m.observe_turn(turn=3, cache_read=5000, cache_create=0, uncached=5000)
        prefix3 = [a for a in alerts3 if a.alert_type == CacheAlertType.PREFIX_UNSTABLE]
        assert prefix3 == []


class TestAbsoluteTokenThreshold:
    def test_relative_drop_above_threshold_but_below_absolute_floor(self):
        """A 50% drop of only 500 tokens should NOT fire — below 2000 floor."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=1000, cache_create=0, uncached=0)
        alerts = m.observe_turn(turn=2, cache_read=500, cache_create=0, uncached=0)
        prefix = [a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE]
        assert prefix == []

    def test_relative_drop_above_threshold_and_above_absolute_floor(self):
        """A 50% drop of 5000 tokens SHOULD fire — above 2000 floor."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        alerts = m.observe_turn(turn=2, cache_read=5000, cache_create=0, uncached=5000)
        prefix = [a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE]
        assert len(prefix) == 1

    def test_exactly_2000_token_drop_fires(self):
        """Exactly 2000 tokens dropped with >10% SHOULD fire (>= threshold)."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        alerts = m.observe_turn(turn=2, cache_read=8000, cache_create=0, uncached=0)
        prefix = [a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE]
        assert len(prefix) == 1

    def test_1999_token_drop_does_not_fire(self):
        """1999 tokens dropped (just below floor) should NOT fire."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        alerts = m.observe_turn(turn=2, cache_read=8001, cache_create=0, uncached=0)
        prefix = [a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE]
        assert prefix == []


class TestCompactionSuppression:
    def test_suppress_next_drop_skips_one_alert(self):
        """After suppress_next_drop(), the next big drop does NOT fire."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        m.suppress_next_drop()
        alerts = m.observe_turn(turn=2, cache_read=0, cache_create=0, uncached=10000)
        prefix = [a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE]
        assert prefix == []

    def test_suppression_only_lasts_one_turn(self):
        """suppress_next_drop() is consumed after one observe_turn()."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        m.suppress_next_drop()
        m.observe_turn(turn=2, cache_read=5000, cache_create=0, uncached=5000)
        alerts = m.observe_turn(turn=3, cache_read=1000, cache_create=0, uncached=9000)
        prefix = [a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE]
        assert len(prefix) == 1

    def test_suppression_resets_baseline(self):
        """After suppression, prev_cache_read is updated to the suppressed turn's value."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        m.suppress_next_drop()
        m.observe_turn(turn=2, cache_read=3000, cache_create=0, uncached=7000)
        alerts = m.observe_turn(turn=3, cache_read=2800, cache_create=0, uncached=7200)
        prefix = [a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE]
        assert prefix == []

    def test_suppress_without_previous_reading_is_safe(self):
        """suppress_next_drop() on a fresh monitor does not crash."""
        m = CacheHitMonitor()
        m.suppress_next_drop()
        alerts = m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        assert alerts == []

    def test_ratio_low_still_fires_when_suppressed(self):
        """Suppression only affects PREFIX_UNSTABLE, not RATIO_LOW."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        m.suppress_next_drop()
        alerts = m.observe_turn(turn=2, cache_read=100, cache_create=100, uncached=9800)
        types = [a.alert_type for a in alerts]
        assert CacheAlertType.PREFIX_UNSTABLE not in types
        assert CacheAlertType.RATIO_LOW in types


class TestTTLAwareness:
    def test_no_elapsed_ms_gives_generic_message(self):
        """Without elapsed_ms, message says 'prefix may be unstable'."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        alerts = m.observe_turn(turn=2, cache_read=0, cache_create=0, uncached=10000)
        prefix = next(a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE)
        assert "prefix may be unstable" in prefix.message

    def test_elapsed_under_5min_gives_generic_message(self):
        """Elapsed < 5min: not TTL, use generic message."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        alerts = m.observe_turn(
            turn=2,
            cache_read=0,
            cache_create=0,
            uncached=10000,
            elapsed_ms=60_000,
        )
        prefix = next(a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE)
        assert "TTL" not in prefix.message

    def test_elapsed_over_5min_suggests_5min_ttl(self):
        """Elapsed > 5min but < 1h: likely 5min TTL expiry."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        alerts = m.observe_turn(
            turn=2,
            cache_read=0,
            cache_create=0,
            uncached=10000,
            elapsed_ms=CACHE_TTL_5MIN_MS + 1000,
        )
        prefix = next(a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE)
        assert "5min TTL" in prefix.message

    def test_elapsed_over_1h_suggests_1h_ttl(self):
        """Elapsed > 1h: likely 1h TTL expiry."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        alerts = m.observe_turn(
            turn=2,
            cache_read=0,
            cache_create=0,
            uncached=10000,
            elapsed_ms=CACHE_TTL_1HOUR_MS + 1000,
        )
        prefix = next(a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE)
        assert "1h TTL" in prefix.message

    def test_model_change_takes_priority_over_ttl(self):
        """Model change is attributed even when elapsed > TTL window."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0, model="gpt-4o")
        alerts = m.observe_turn(
            turn=2,
            cache_read=0,
            cache_create=0,
            uncached=10000,
            model="gpt-4o-mini",
            elapsed_ms=CACHE_TTL_1HOUR_MS + 1000,
        )
        prefix = next(a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE)
        assert "model changed" in prefix.message
        assert "TTL" not in prefix.message


class TestFeatureFlag:
    def test_default_enabled(self):
        """Settings default has cache_hit_monitor_enabled=True."""
        from src.config import Settings

        s = Settings()
        assert s.cache_hit_monitor_enabled is True

    def test_env_override_to_false(self, tmp_path):
        """CACHE_HIT_MONITOR_ENABLED=false disables the monitor."""
        from src.config import Settings

        env_file = tmp_path / ".env"
        env_file.write_text("CACHE_HIT_MONITOR_ENABLED=false\n")
        s = Settings.load(env_path=env_file)
        assert s.cache_hit_monitor_enabled is False

    def test_env_override_to_true(self, tmp_path):
        """CACHE_HIT_MONITOR_ENABLED=true explicitly enables."""
        from src.config import Settings

        env_file = tmp_path / ".env"
        env_file.write_text("CACHE_HIT_MONITOR_ENABLED=true\n")
        s = Settings.load(env_path=env_file)
        assert s.cache_hit_monitor_enabled is True


class TestCauseAttribution:
    def test_model_change_attributed(self):
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0, model="claude-sonnet")
        alerts = m.observe_turn(
            turn=2,
            cache_read=0,
            cache_create=0,
            uncached=10000,
            model="claude-opus",
        )
        prefix = next(a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE)
        assert "model changed" in prefix.message
        assert "claude-sonnet" in prefix.message
        assert "claude-opus" in prefix.message

    def test_tool_count_change_attributed(self):
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0, tool_count=5)
        alerts = m.observe_turn(
            turn=2,
            cache_read=0,
            cache_create=0,
            uncached=10000,
            tool_count=8,
        )
        prefix = next(a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE)
        assert "tools changed" in prefix.message
        assert "5" in prefix.message
        assert "8" in prefix.message

    def test_model_change_takes_priority_over_tools(self):
        """When both model and tools changed, model is reported first."""
        m = CacheHitMonitor()
        m.observe_turn(
            turn=1, cache_read=10000, cache_create=0, uncached=0, model="old-model", tool_count=5
        )
        alerts = m.observe_turn(
            turn=2,
            cache_read=0,
            cache_create=0,
            uncached=10000,
            model="new-model",
            tool_count=10,
        )
        prefix = next(a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE)
        assert "model changed" in prefix.message

    def test_no_context_gives_generic_cause(self):
        """No model/tools/elapsed → generic message."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        alerts = m.observe_turn(turn=2, cache_read=0, cache_create=0, uncached=10000)
        prefix = next(a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE)
        assert "prefix may be unstable" in prefix.message

    def test_same_model_no_model_attribution(self):
        """Same model between turns → model NOT blamed."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0, model="gpt-4o")
        alerts = m.observe_turn(
            turn=2,
            cache_read=0,
            cache_create=0,
            uncached=10000,
            model="gpt-4o",
        )
        prefix = next(a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE)
        assert "model changed" not in prefix.message

    def test_empty_prev_model_no_attribution(self):
        """If first turn didn't set model, don't attribute model change."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0)
        alerts = m.observe_turn(
            turn=2,
            cache_read=0,
            cache_create=0,
            uncached=10000,
            model="gpt-4o",
        )
        prefix = next(a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE)
        assert "model changed" not in prefix.message

    def test_negative_tool_count_means_unknown(self):
        """tool_count=-1 (default) means no tracking — don't attribute."""
        m = CacheHitMonitor()
        m.observe_turn(turn=1, cache_read=10000, cache_create=0, uncached=0, tool_count=-1)
        alerts = m.observe_turn(
            turn=2,
            cache_read=0,
            cache_create=0,
            uncached=10000,
            tool_count=-1,
        )
        prefix = next(a for a in alerts if a.alert_type == CacheAlertType.PREFIX_UNSTABLE)
        assert "tools changed" not in prefix.message


class CacheUsageAdapter:
    """Replays a scripted per-turn Usage through stream_with_tools().

    The only substituted dependency in the tests below. Everything else is the
    real ExecutionThread.agent_loop path: the monitor construction, the
    observe_turn call after record_turn_usage, and the real EventEmitter.

    Turn 1 returns a tool call so the loop takes a second trip; turn 2 ends the
    turn. Each turn yields the Usage taken from ``usages`` in order.
    """

    def __init__(self, usages: list[Usage]):
        self.usages = usages
        self.attempts = 0
        self.model = "claude-sonnet-4-6"

    async def stream_with_tools(
        self,
        messages,
        system=None,
        tools=None,
        super_thinking: bool = False,
        max_output_tokens: int | None = None,
        skip_cache_write: bool = False,
    ):
        usage = self.usages[min(self.attempts, len(self.usages) - 1)]
        self.attempts += 1
        if self.attempts < len(self.usages):
            yield StreamEvent(
                type="tool_call",
                tool_call=ToolCall(id=f"tc-{self.attempts}", name="noop", input={}),
            )
            yield StreamEvent(type="stop", stop_reason=StopReason.TOOL_USE)
        else:
            yield StreamEvent(type="text", text="done")
            yield StreamEvent(type="stop", stop_reason=StopReason.END_TURN)
        yield StreamEvent(type="usage", usage=usage)

    async def generate(self, messages, system=""):
        return "summary"


def noop_registry():
    """One trivial tool so turn 1's tool call resolves and the loop continues."""
    from src.utility_directory.tool_runtime import (
        ToolDef as RuntimeToolDef,
        ToolRegistry,
    )

    reg = ToolRegistry()

    async def noop_handler(params):
        return "ok"

    reg.register(
        RuntimeToolDef(
            name="noop",
            description="A no-op tool.",
            input_schema={"type": "object", "properties": {}},
            handler=noop_handler,
        )
    )
    return reg


UNSTABLE_TURNS = [
    Usage(input_tokens=1000, output_tokens=50, cache_read_input_tokens=50000),
    Usage(
        input_tokens=48500,
        output_tokens=50,
        cache_read_input_tokens=1000,
        cache_creation_input_tokens=500,
    ),
]


async def run_loop(db, settings, usages):
    """Drive the real agent_loop and return (thread, emitted CacheHitRatioAlerts)."""
    from src.engine_core.events import EventEmitter
    from src.engine_core.schema import RequestOptions
    from src.engine_core.thread import ExecutionThread
    from src.memory.memory_factory import create_working_memory

    emitter = EventEmitter()
    alerts: list = []

    async def capture(event):
        if isinstance(event, CacheHitRatioAlert):
            alerts.append(event)

    emitter.on(capture)

    th = ExecutionThread(
        adapter=CacheUsageAdapter(usages),
        emitter=emitter,
        memory=create_working_memory("sess-cache-monitor", db=db),
        tools=noop_registry(),
        settings=settings,
    )
    await th.agent_loop("hi", options=RequestOptions(query_source="foreground"))
    return th, alerts


@pytest.mark.asyncio
async def test_alerts_reach_the_emitter_through_the_real_loop(db):
    """A destabilized prefix on turn 2 surfaces as CacheHitRatioAlert events.

    This is the wiring regression: the monitor is constructed by agent_loop,
    observe_turn runs on every turn, and each alert becomes an event on the
    real emitter. Turn 1 must stay silent (no prior turn to compare against,
    and RATIO_LOW is guarded until turn 2).
    """
    settings = Settings()
    settings.cache_hit_monitor_enabled = True
    _, alerts = await run_loop(db, settings, UNSTABLE_TURNS)

    by_type = {a.alert_type: a for a in alerts}
    assert set(by_type) == {"prefix_unstable", "ratio_low"}
    assert all(a.turn == 2 for a in alerts), "turn 1 must not alert"

    unstable = by_type["prefix_unstable"]
    assert unstable.prev_cache_read == 50000
    assert unstable.cache_read == 1000
    assert unstable.drop_pct == pytest.approx(0.98)
    assert "cache_read dropped 98%" in unstable.message

    ratio = by_type["ratio_low"]
    assert ratio.cache_ratio == pytest.approx(0.02)
    assert ratio.uncached == 48500


@pytest.mark.asyncio
async def test_disabled_flag_emits_nothing(db):
    """cache_hit_monitor_enabled=False means the loop never builds a monitor.

    Same destabilized turns as above, which would otherwise raise two alerts.
    """
    settings = Settings()
    settings.cache_hit_monitor_enabled = False
    _, alerts = await run_loop(db, settings, UNSTABLE_TURNS)

    assert alerts == []
