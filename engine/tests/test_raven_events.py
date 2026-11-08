"""Tests for observability event dataclasses (src/raven/events.py)."""

from src.raven.events import (
    AgentStepEvent,
    CacheEvent,
    RetentionRunEvent,
    RouteDecisionEvent,
    TokenUsageEvent,
    ToolCallEvent,
    UntypedEvent,
    event_from_row,
    event_to_payload,
)

import json


ALL_TYPED_EVENTS = [
    TokenUsageEvent(
        model="claude-sonnet-4-6", input_tokens=100, output_tokens=50, timestamp=1000.0
    ),
    ToolCallEvent(tool_name="bash", duration_ms=42.5, success=True, timestamp=1001.0),
    AgentStepEvent(step_type="reasoning", step_id="s1", parent_id="p1", timestamp=1002.0),
    RouteDecisionEvent(
        chosen_model="claude-haiku-4-5",
        tier="light",
        reason="tool_call step",
        alternatives=["claude-sonnet-4-6"],
        timestamp=1004.0,
    ),
    CacheEvent(
        model="claude-sonnet-4-6",
        cache_hit_ratio=0.85,
        tokens_cached=8500,
        tokens_fresh=1500,
        timestamp=1005.0,
    ),
    RetentionRunEvent(rollup_count=100, drop_count=100, duration_ms=42.0, timestamp=1006.0),
]


class TestEventConstruction:
    def test_token_usage_fields(self):
        e = TokenUsageEvent(model="m", input_tokens=10, output_tokens=5, timestamp=1.0)
        assert e.kind == "token_usage"
        assert e.model == "m"
        assert e.input_tokens == 10
        assert e.output_tokens == 5

    def test_tool_call_fields(self):
        e = ToolCallEvent(tool_name="read", duration_ms=1.5, timestamp=2.0)
        assert e.kind == "tool_call"
        assert e.tool_name == "read"

    def test_route_decision_alternatives(self):
        e = RouteDecisionEvent(alternatives=["a", "b"], timestamp=4.0)
        assert e.alternatives == ["a", "b"]

    def test_untyped_event(self):
        e = UntypedEvent(kind="custom.event", metadata={"foo": "bar"}, timestamp=5.0)
        assert e.kind == "custom.event"
        assert e.metadata == {"foo": "bar"}

    def test_kind_not_settable(self):
        e = TokenUsageEvent(timestamp=1.0)
        assert e.kind == "token_usage"

    def test_default_timestamp_populated(self):
        e = TokenUsageEvent(model="m")
        assert e.timestamp > 0


class TestSerialization:
    def test_round_trip_all_typed_events(self):
        for event in ALL_TYPED_EVENTS:
            payload = event_to_payload(event)
            parsed = json.loads(payload)
            assert "kind" not in parsed
            assert "timestamp" not in parsed
            reconstructed = event_from_row(event.kind, event.timestamp, payload)
            assert type(reconstructed) is type(event)
            assert reconstructed.kind == event.kind
            assert reconstructed.timestamp == event.timestamp

    def test_token_usage_round_trip_values(self):
        orig = TokenUsageEvent(model="test", input_tokens=100, output_tokens=50, timestamp=999.0)
        payload = event_to_payload(orig)
        restored = event_from_row("token_usage", 999.0, payload)
        assert restored.model == "test"
        assert restored.input_tokens == 100
        assert restored.output_tokens == 50

    def test_route_decision_alternatives_preserved(self):
        orig = RouteDecisionEvent(alternatives=["a", "b"], timestamp=1.0)
        payload = event_to_payload(orig)
        restored = event_from_row("route_decision", 1.0, payload)
        assert restored.alternatives == ["a", "b"]

    def test_unknown_kind_becomes_untyped(self):
        payload = json.dumps({"foo": "bar"})
        event = event_from_row("unknown_thing", 1.0, payload)
        assert isinstance(event, UntypedEvent)
        assert event.kind == "unknown_thing"
        assert event.metadata == {"foo": "bar"}

    def test_empty_payload_untyped(self):
        event = event_from_row("unknown", 1.0, "{}")
        assert isinstance(event, UntypedEvent)

    def test_payload_excludes_kind_and_timestamp(self):
        e = TokenUsageEvent(model="m", input_tokens=1, timestamp=1.0)
        payload = event_to_payload(e)
        data = json.loads(payload)
        assert "kind" not in data
        assert "timestamp" not in data
        assert "model" in data
